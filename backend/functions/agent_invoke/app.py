"""agent_invoke Lambda (계약 v2) — Crew Composition Agent (조회·추천 전용).

Route (OFFICE 전용):
  POST /office/requests/{requestId}/agent-compose      NORMAL 편성 추천 → Crew(PROPOSED, AGENT)
  POST /office/gap-events/{eventId}/agent-recompose     EMERGENCY 대체 추천 → GapEvent(PROPOSED)

원칙 (PROMPT §5):
- Agent는 추천만 한다. 쓰기 Tool 없음. 각 추천 멤버에 assigned_trade 포함.
- 후보에 preferred_trades / excluded_trades / desired_daily_wage 반영, assigned_trade ∉ excluded.
- 예산: NORMAL=budget, EMERGENCY=budget − 고정 인원 offered_wage 합.
- EMERGENCY 후보에서 declined_worker_ids 제외. fixed_members 불변.
- 추천 사유에 개인 부정 평가·확률 수치·최적 보장 표현 금지.
- Strands+Bedrock 추천을 우선 시도하고, 호출 또는 검증 실패 시 설정에 따라 결정론적
  폴백으로 동일 규칙의 추천을 생성한다.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
from collections import Counter
from typing import Any

from pydantic import ValidationError

from agent.crew_agent import BedrockUnavailable, compose as compose_crew
from agent.schemas import AgentInput, AgentOutput
from shared import db
from shared.auth import Principal, get_principal
from shared.crew import assemble_crew_members
from shared.responses import ApiError, ErrorCode, success
from shared.routing import Router
from shared.schemas import (
    build_crew,
    crew_view,
    gap_view,
    now_iso,
)
from shared.state import CrewStatus, GapStatus, RequestStatus, Role, Trade, WorkerState

logger = logging.getLogger()
router = Router()

FALLBACK_ENABLED = os.environ.get("AGENT_FALLBACK_ENABLED", "false").lower() == "true"
ASYNC_ENABLED = os.environ.get("CREW_AGENT_ASYNC_ENABLED", "false").lower() == "true"
_LAMBDA_CLIENT = None


# ---------------------------------------------------------------------------
# NORMAL: agent-compose
# ---------------------------------------------------------------------------
@router.route("POST", "/office/requests/{requestId}/agent-compose")
def agent_compose(_event, principal: Principal, params):
    principal.require_role(Role.OFFICE)
    request = db.get_request(params["requestId"])
    if not request:
        raise ApiError(ErrorCode.REQUEST_NOT_FOUND, "요청을 찾을 수 없습니다.")
    principal.require_office(request["office_id"])

    declined = set(request.get("declined_worker_ids") or [])
    candidates = [
        w for w in db.query_office_workers_by_state(request["office_id"], WorkerState.READY)
        if w["worker_id"] not in declined
    ]
    needed = _flatten_required(request.get("required_workers", []))
    if len(candidates) < len(needed):
        raise ApiError(
            ErrorCode.AGENT_RETRY_FAILED,
            f"편성에 필요한 인원은 {len(needed)}명이지만 현재 대기 중인 후보는 "
            f"{len(candidates)}명입니다. 근로자 대기 상태와 필요 인원을 확인해주세요.",
        )

    budget = int(request.get("budget", 0))
    recs = _recommend(candidates, needed, budget,
                      mode="NORMAL", priority=request.get("priority"), request=request)
    if not recs:
        recs = _rule_based_recommendations(
            candidates,
            needed,
            budget=budget,
            priority=request.get("priority"),
        )
    if not recs:
        raise ApiError(ErrorCode.AGENT_RETRY_FAILED, _composition_failure_reason(candidates, needed))

    _cancel_existing_crews(request["request_id"])
    top = recs[0]
    proposed = [
        {"worker_id": m["worker_id"], "assigned_trade": m["assigned_trade"],
         "offered_wage": m["offered_wage"], "is_replacement": False}
        for m in top["members"]
    ]
    crew = build_crew(
        office_id=request["office_id"],
        request_id=request["request_id"],
        proposed_members=proposed,
        status=CrewStatus.PROPOSED,
        source="AGENT",
        reason=top["reason"],
        considerations=top["considerations"],
        total_cost=top["total_cost"],
        recommendations=recs,
    )
    db.put_crew(crew)
    _set_request_status(request["request_id"], RequestStatus.PROPOSED)
    return success(crew_view(crew, assemble_crew_members(crew)))


# ---------------------------------------------------------------------------
# EMERGENCY: agent-recompose
# ---------------------------------------------------------------------------
@router.route("POST", "/office/gap-events/{eventId}/agent-recompose")
def agent_recompose(_event, principal: Principal, params):
    principal.require_role(Role.OFFICE)
    gap = db.get_gap_event(params["eventId"])
    if not gap:
        raise ApiError(ErrorCode.GAP_EVENT_NOT_FOUND, "결원 이벤트를 찾을 수 없습니다.")
    principal.require_office(gap["office_id"])
    crew = db.get_crew(gap["crew_id"])
    request = db.get_request(gap["request_id"])
    if not crew or not request:
        raise ApiError(ErrorCode.CREW_INVALID, "작업조/요청을 찾을 수 없습니다.")

    _set_gap_status(gap["event_id"], GapStatus.RECOMPOSING)

    fixed = assemble_crew_members(crew)
    fixed_cost = sum(int(m.get("offered_wage", 0)) for m in fixed)
    gap_trades = _gap_trades(request.get("required_workers", []), fixed)
    if not gap_trades:
        # 결원 직종이 없으면(이미 충족) 실패 처리
        _set_gap_status(gap["event_id"], GapStatus.FAILED)
        raise ApiError(ErrorCode.AGENT_RETRY_FAILED, "충원이 필요한 직종을 찾지 못했습니다.")

    declined = set(request.get("declined_worker_ids") or [])
    fixed_ids = {m["worker_id"] for m in fixed}
    candidates = [
        w for w in db.query_office_workers_by_state(gap["office_id"], WorkerState.READY)
        if w["worker_id"] not in declined and w["worker_id"] not in fixed_ids
    ]
    budget = int(request.get("budget", 0))
    remaining = (budget - fixed_cost) if budget > 0 else 0
    recs = _recommend(candidates, gap_trades, remaining,
                      mode="EMERGENCY", fixed_members=fixed,
                      priority=request.get("priority"), request=request,
                      crew_id=crew["crew_id"],
                      context_worker_ids=[m["worker_id"] for m in fixed])
    if not recs:
        recs = _rule_based_recommendations(
            candidates,
            gap_trades,
            budget=remaining,
            priority=request.get("priority"),
            context_worker_ids=[m["worker_id"] for m in fixed],
        )
    if not recs:
        _set_gap_status(gap["event_id"], GapStatus.FAILED)
        raise ApiError(ErrorCode.AGENT_RETRY_FAILED, _composition_failure_reason(candidates, gap_trades))

    now = now_iso()
    db.update_gap_event(
        gap["event_id"],
        UpdateExpression="SET #s = :s, gsi1sk = :g, recommendations = :r, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": GapStatus.PROPOSED,
            ":g": db.gap_gsi1sk(GapStatus.PROPOSED, gap["event_id"]),
            ":r": _to_decimal(recs),
            ":t": now,
        },
    )
    return success(gap_view(db.get_gap_event(gap["event_id"])))


# ---------------------------------------------------------------------------
# 추천 엔진: Strands Agent → 독립 검증 → 설정 기반 결정론적 폴백
# ---------------------------------------------------------------------------
CAREER_NORM = 15          # career_years 정규화 상한(년)
TEAM_NORM = 3             # 협업 횟수 정규화 상한
_PRIORITY_AXES = ("cost", "career", "teamwork")
_AXIS_LABEL = {"cost": "비용", "career": "경력", "teamwork": "팀워크"}


def _priority_weights(priority) -> dict[str, float]:
    """priority 순위(1=최우선)를 정규화 가중치로 변환. 누락/이상 시 균등 가중치."""
    ranks: dict[str, int] = {}
    if isinstance(priority, dict):
        for axis in _PRIORITY_AXES:
            try:
                r = int(priority.get(axis))
            except (TypeError, ValueError):
                continue
            if r in (1, 2, 3):
                ranks[axis] = r
    if len(ranks) != 3 or sorted(ranks.values()) != [1, 2, 3]:
        return {axis: 1 / 3 for axis in _PRIORITY_AXES}      # 균등 (미지정/이상)
    raw = {axis: (4 - ranks[axis]) for axis in _PRIORITY_AXES}  # rank1→3, 2→2, 3→1
    total = float(sum(raw.values()))
    return {axis: raw[axis] / total for axis in _PRIORITY_AXES}


class _CollabIndex:
    """후보/컨텍스트 근로자 간 과거 공동 작업조 공유 횟수(협업 이력). crew 집합 lazy 캐시."""

    def __init__(self):
        self._crew_sets: dict[str, set] = {}

    def _crews(self, worker_id: str) -> set:
        cached = self._crew_sets.get(worker_id)
        if cached is None:
            cached = set()
            try:
                for a in db.query_worker_assignments(worker_id):
                    if a.get("crew_id"):
                        cached.add(a["crew_id"])
            except Exception:  # noqa: BLE001 - 조회 실패 시 협업 0 취급
                cached = set()
            self._crew_sets[worker_id] = cached
        return cached

    def shared(self, a: str, b: str) -> int:
        if a == b:
            return 0
        return len(self._crews(a) & self._crews(b))

    def pairs(self, worker_ids) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(worker_ids))
        out = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                c = self.shared(ids[i], ids[j])
                if c > 0:
                    out.append({"worker_a": ids[i], "worker_b": ids[j], "count": c})
        return out


def _recommend(candidates, needed_trades, budget, *, mode="NORMAL", fixed_members=None,
               priority=None, request=None, crew_id=None,
               context_worker_ids=None) -> list[dict[str, Any]]:
    """LLM 추천을 우선 시도하고(가용 시), 검증 실패 시 결정론적 추천으로 폴백한다.

    priority(순위)·경력·협업 이력을 종합해 정렬/평가하고, 각 추천안에 적합도(fitness %)를 부여한다.
    """
    weights = _priority_weights(priority)
    collab = _CollabIndex()
    context_ids = list(context_worker_ids or [])
    wages = [int(w.get("desired_daily_wage", 0)) for w in candidates] or [0]
    wage_lo, wage_hi = min(wages), max(wages)

    recs = None
    llm_recs = _try_strands(
        candidates,
        needed_trades,
        budget,
        mode=mode,
        fixed_members=fixed_members,
        priority=priority,
        request=request,
        crew_id=crew_id,
    )
    if llm_recs:
        valid = [r for r in llm_recs if _valid_rec(r, candidates, needed_trades, budget)]
        if valid:
            recs = valid[:3]
    if recs is None:
        if os.environ.get("CREW_AGENT_MODEL_ID") and not FALLBACK_ENABLED:
            return []
        recs = _greedy(candidates, needed_trades, budget, weights, collab, context_ids)

    for i, r in enumerate(recs, start=1):
        r["rank"] = i
        r["fitness"] = _fitness_percent(r["members"], weights, collab, context_ids, wage_lo, wage_hi)
    return recs


def _rule_based_recommendations(
    candidates,
    needed_trades,
    *,
    budget,
    priority=None,
    context_worker_ids=None,
) -> list[dict[str, Any]]:
    """Return an executable combination even when only the configured budget blocks it."""
    weights = _priority_weights(priority)
    collab = _CollabIndex()
    context_ids = list(context_worker_ids or [])
    recs = _greedy(candidates, needed_trades, 0, weights, collab, context_ids)
    if not recs:
        return []
    wages = [int(worker.get("desired_daily_wage", 0)) for worker in candidates] or [0]
    wage_lo, wage_hi = min(wages), max(wages)
    for rank, rec in enumerate(recs, start=1):
        rec["rank"] = rank
        rec["fitness"] = _fitness_percent(
            rec["members"], weights, collab, context_ids, wage_lo, wage_hi
        )
        excess = rec["total_cost"] - int(budget or 0)
        if budget and excess > 0:
            warning = f"⚠ 예산 {excess:,}원 초과 — 임금 수정 필요"
            rec["reason"] = (
                f"{warning}. 필요한 직종과 인원은 충족하므로 임금을 조정한 뒤 승인할 수 있습니다."
            )
            rec["considerations"] = list(dict.fromkeys([
                *[
                    item for item in rec.get("considerations", [])
                    if "예산 범위" not in item
                ],
                warning,
            ]))
    return recs


def _composition_failure_reason(candidates, needed_trades) -> str:
    """Explain why no combination exists without exposing worker identifiers."""
    if not candidates:
        return "현재 대기 중인 근로자가 없어 작업조를 구성할 수 없습니다. 근로자의 대기 상태를 확인해주세요."
    shortages = []
    required = Counter(needed_trades)
    for trade, count in required.items():
        if trade == "ANY":
            available = sum(1 for worker in candidates if _assign_any_trade(worker) is not None)
        else:
            available = sum(
                1 for worker in candidates
                if trade not in (worker.get("excluded_trades") or [])
            )
        if available < count:
            shortages.append(f"{_HUMAN_TRADE_LABELS.get(trade, trade)} {count - available}명 부족")
    if shortages:
        return "배치 가능한 후보가 부족합니다: " + ", ".join(shortages) + ". 근로자 직종 제외 조건을 확인해주세요."
    return "각 직종 후보는 있으나 한 근로자를 중복 배정하지 않고 모든 조건을 동시에 충족하는 조합이 없습니다. 필요 인원이나 직종 조건을 조정해주세요."


def _priority_for_agent(priority) -> dict[str, int] | None:
    if not isinstance(priority, dict):
        return None
    try:
        normalized = {axis: int(priority[axis]) for axis in _PRIORITY_AXES}
    except (KeyError, TypeError, ValueError):
        return None
    return normalized if sorted(normalized.values()) == [1, 2, 3] else None


def _build_agent_input(
    candidates,
    needed_trades,
    budget,
    *,
    mode,
    fixed_members=None,
    priority=None,
    request=None,
    crew_id=None,
) -> AgentInput:
    """Build identifiers, constraints, and the candidate allowlist for the Agent."""
    req = request or {}
    need = Counter(needed_trades)
    fixed = [
        {
            "worker_id": item["worker_id"],
            "assigned_trade": item["assigned_trade"],
            "offered_wage": int(item["offered_wage"]),
        }
        for item in (fixed_members or [])
    ]
    candidate_ids = list(dict.fromkeys(item["worker_id"] for item in candidates))
    return AgentInput.model_validate({
        "mode": mode,
        "request": {
            "request_id": str(req.get("request_id") or "UNKNOWN"),
            "office_id": str(req.get("office_id") or ""),
            "crew_id": str(crew_id) if crew_id else None,
            "required_workers": [
                {"trade": trade, "count": count} for trade, count in need.items()
            ],
            "budget": max(int(budget or 0), 0),
            "priority": _priority_for_agent(priority),
            "site": str(req.get("site_name") or req.get("site") or ""),
            "work_date": str(req.get("work_date") or ""),
            "start_time": str(req.get("start_time") or ""),
        },
        "fixed_members": fixed,
        "candidate_worker_ids": candidate_ids,
    })


def _agent_output_recommendations(
    output: AgentOutput,
    candidates,
    *,
    expected_mode: str,
    expected_request_id: str,
) -> list[dict[str, Any]] | None:
    """Convert strict AgentOutput to the API recommendation shape without trusting it."""
    if output.mode != expected_mode or output.request_id != expected_request_id:
        return None
    by_id = {item["worker_id"]: item for item in candidates}
    recommendations: list[dict[str, Any]] = []
    for item in output.recommendations[:3]:
        if item.total_cost != sum(member.offered_wage for member in item.members):
            continue
        members = []
        for member in item.members:
            worker = by_id.get(member.worker_id)
            if worker is None:
                members = []
                break
            members.append(_member(
                worker,
                member.assigned_trade,
                member.offered_wage,
            ))
        if members:
            reason = _humanize_agent_text(item.reason, by_id, expected_request_id)
            considerations = [
                _humanize_agent_text(value, by_id, expected_request_id)
                for value in item.considerations
            ]
            recommendations.append(_rec(
                len(recommendations) + 1,
                members,
                reason,
                considerations,
            ))
    return recommendations or None


_HUMAN_TRADE_LABELS = {
    "FORMWORK": "형틀목공",
    "REBAR": "철근공",
    "MASONRY": "조적공",
    "MATERIAL_CARRY": "자재운반",
    "GENERAL": "보통인부",
    "ANY": "직종 무관",
}


def _humanize_agent_text(
    value: str,
    candidates_by_id: dict[str, dict[str, Any]],
    request_id: str,
) -> str:
    """Agent 설명에서 허용된 식별자와 내부 코드값을 사용자용 표현으로 바꾼다."""
    text = str(value or "")
    for worker_id in sorted(candidates_by_id, key=len, reverse=True):
        worker = candidates_by_id[worker_id]
        label = f"{worker.get('name')} 근로자" if worker.get("name") else "선택 근로자"
        text = text.replace(worker_id, label)
    if request_id:
        text = text.replace(request_id, "현재 작업 요청")
    for code, label in _HUMAN_TRADE_LABELS.items():
        text = re.sub(rf"(?<![A-Za-z0-9_]){code}(?![A-Za-z0-9_])", label, text)
    field_labels = {
        "candidate_worker_ids": "후보 근로자",
        "worker_id": "근로자",
        "member_id": "근로자",
        "request_id": "작업 요청",
        "crew_id": "작업조",
    }
    for field, label in field_labels.items():
        text = re.sub(
            rf"(?<![A-Za-z0-9_]){field}(?![A-Za-z0-9_])",
            label,
            text,
            flags=re.IGNORECASE,
        )
    text = re.sub(r"\b(?:REQ|CREW|WORKER|W)_[A-Za-z0-9_-]+\b", "식별 정보", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        "식별 정보",
        text,
        flags=re.IGNORECASE,
    )
    return " ".join(text.split())


def _try_strands(
    candidates,
    needed_trades,
    budget,
    *,
    mode="NORMAL",
    fixed_members=None,
    priority=None,
    request=None,
    crew_id=None,
):
    """Run the actual Strands Crew Composition Agent; return None for safe fallback."""
    if not os.environ.get("CREW_AGENT_MODEL_ID"):
        return None
    try:
        agent_input = _build_agent_input(
            candidates,
            needed_trades,
            budget,
            mode=mode,
            fixed_members=fixed_members,
            priority=priority,
            request=request,
            crew_id=crew_id,
        )
        logger.info(
            "crew_strands_invoke request_id=%s mode=%s candidates=%d required_workers=%d",
            agent_input.request.request_id,
            mode,
            len(agent_input.candidate_worker_ids),
            sum(item.count for item in agent_input.request.required_workers),
        )
        output = compose_crew(
            agent_input,
            timeout_s=float(os.environ.get("AGENT_INVOKE_TIMEOUT_S", "45")),
        )
        recommendations = _agent_output_recommendations(
            output,
            candidates,
            expected_mode=mode,
            expected_request_id=agent_input.request.request_id,
        )
        if recommendations:
            logger.info(
                "crew_strands_success request_id=%s mode=%s recommendations=%d",
                agent_input.request.request_id,
                mode,
                len(recommendations),
            )
        else:
            logger.warning(
                "crew_strands_invalid_output request_id=%s mode=%s",
                agent_input.request.request_id,
                mode,
            )
        return recommendations
    except (BedrockUnavailable, ValidationError, ValueError) as exc:
        logger.warning("crew_strands_failed reason=%s", type(exc).__name__)
        return None


# ---------------------------------------------------------------------------
# 결정론적 추천 (그리디, 규칙 준수) — 폴백/기본 엔진
# ---------------------------------------------------------------------------
def _greedy(candidates, needed_trades, budget, weights, collab, context_ids) -> list[dict[str, Any]]:
    """우선순위(비용/경력/팀워크) 가중 점수로 직종별 슬롯을 채우는 결정론적 추천.

    각 슬롯마다 (이미 선택된 멤버 + context와의 협업 포함) 가중 점수가 가장 높은 후보를 고른다.
    trade == "ANY" 슬롯은 후보에게 배정 가능한 실제 직종을 부여한다.
    """
    recs: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    rng = random.Random(42)
    wages = [int(w.get("desired_daily_wage", 0)) for w in candidates] or [0]
    wage_lo, wage_hi = min(wages), max(wages)

    for attempt in range(8):
        if len(recs) >= 3:
            break
        members: list[dict[str, Any]] = []
        used: set[str] = set()
        ok = True
        for trade in needed_trades:
            best = None
            best_score = None
            for w in candidates:
                if w["worker_id"] in used:
                    continue
                assigned = _assign_any_trade(w) if trade == "ANY" else trade
                if assigned is None or assigned in (w.get("excluded_trades") or []):
                    continue
                partners = [m["worker_id"] for m in members] + context_ids
                score = _cand_score(w, partners, weights, wage_lo, wage_hi, collab)
                if attempt > 0:
                    score += rng.uniform(-0.15, 0.15)
                if best_score is None or score > best_score:
                    best_score, best = score, (w, assigned)
            if best is None:
                ok = False
                break
            w, assigned = best
            members.append(_member(w, assigned, int(w.get("desired_daily_wage", 0))))
            used.add(w["worker_id"])
        if not ok or len(members) < len(needed_trades):
            continue
        total = sum(m["offered_wage"] for m in members)
        if budget and budget > 0 and total > budget:
            continue
        key = tuple(sorted(m["worker_id"] for m in members))
        if key in seen:
            continue
        seen.add(key)
        recs.append(_rec(len(recs) + 1, members, _reason_for(weights), _considerations_for(weights)))
    return recs


# ---------------------------------------------------------------------------
# 검증 (LLM 출력 신뢰 금지)
# ---------------------------------------------------------------------------
def _valid_rec(rec, candidates, needed_trades, budget) -> bool:
    by_id = {w["worker_id"]: w for w in candidates}
    members = rec.get("members") or []
    ids = [m["worker_id"] for m in members]
    if not ids or len(ids) != len(set(ids)):
        return False
    from collections import Counter
    need = Counter(needed_trades)
    need.pop("ANY", 0)  # 직종 무관 요구는 초과분(총원 일치)으로 흡수
    got: Counter = Counter()
    total = 0
    for m in members:
        w = by_id.get(m["worker_id"])
        if not w or w.get("state") != WorkerState.READY:
            return False
        if m["assigned_trade"] in (w.get("excluded_trades") or []):
            return False
        if int(m["offered_wage"]) != int(w.get("desired_daily_wage", 0)):
            return False
        got[m["assigned_trade"]] += 1
        total += int(m["offered_wage"])
    # 총원 일치 (specific + ANY 슬롯 합)
    if len(members) != len(needed_trades):
        return False
    # 각 specific 직종 최소 인원 충족 (초과분은 ANY가 흡수)
    for trade, n in need.items():
        if got.get(trade, 0) < n:
            return False
    if budget and budget > 0 and total > budget:
        return False
    return True


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
def _member(worker, assigned_trade, offered_wage) -> dict[str, Any]:
    return {
        "worker_id": worker["worker_id"],
        "name": worker.get("name"),
        "assigned_trade": assigned_trade,
        "career_years": int(worker.get("career_years", 0)),
        "offered_wage": int(offered_wage),
        "acceptance": "PENDING",
    }


def _rec(rank, members, reason, considerations) -> dict[str, Any]:
    return {
        "rank": int(rank),
        "member_ids": [m["worker_id"] for m in members],
        "members": members,
        "total_cost": sum(m["offered_wage"] for m in members),
        "reason": reason,
        "considerations": considerations,
    }


def _assign_any_trade(worker) -> str | None:
    """직종 무관(ANY) 슬롯에 배정할 실제 직종을 고른다. excluded는 피한다."""
    excluded = set(worker.get("excluded_trades") or [])
    for t in (worker.get("preferred_trades") or []):
        if t not in excluded:
            return t
    if Trade.GENERAL not in excluded:
        return Trade.GENERAL
    for t in Trade.ALL:
        if t not in excluded:
            return t
    return None


def _cand_score(worker, partner_ids, weights, wage_lo, wage_hi, collab) -> float:
    """후보 1명의 우선순위 가중 점수(비용 저렴·경력 높음·기존 선택 멤버와 협업)."""
    wage = int(worker.get("desired_daily_wage", 0))
    cost_s = 1.0 if wage_hi <= wage_lo else (wage_hi - wage) / (wage_hi - wage_lo)
    career_s = min(int(worker.get("career_years", 0)) / CAREER_NORM, 1.0)
    if partner_ids:
        shared = sum(collab.shared(worker["worker_id"], p) for p in partner_ids)
        team_s = min(shared / (len(partner_ids) * TEAM_NORM), 1.0)
    else:
        team_s = 0.0
    return weights["cost"] * cost_s + weights["career"] * career_s + weights["teamwork"] * team_s


def _fitness_percent(members, weights, collab, context_ids, wage_lo, wage_hi) -> int:
    """추천 팀 조합의 적합도(0~100). 우선순위 가중치로 비용·경력·협업 점수를 합산한다."""
    if not members:
        return 0
    member_wages = [int(m.get("offered_wage", 0)) for m in members]
    if wage_hi > wage_lo:
        cost_s = sum((wage_hi - w) / (wage_hi - wage_lo) for w in member_wages) / len(member_wages)
    else:
        cost_s = 1.0
    careers = [int(m.get("career_years", 0)) for m in members]
    career_s = min((sum(careers) / len(careers)) / CAREER_NORM, 1.0)
    ids = [m["worker_id"] for m in members] + list(context_ids or [])
    shared_total = sum(
        collab.shared(ids[i], ids[j])
        for i in range(len(ids)) for j in range(i + 1, len(ids))
    )
    team_s = min(shared_total / (len(members) * TEAM_NORM), 1.0)
    score = weights["cost"] * cost_s + weights["career"] * career_s + weights["teamwork"] * team_s
    return int(round(score * 100))


def _top_axis(weights) -> str:
    return max(_PRIORITY_AXES, key=lambda a: weights[a])


def _considerations_for(weights) -> list[str]:
    extra = {"cost": "인건비 효율 우선", "career": "경력 우선 구성", "teamwork": "협업 이력 반영"}
    return ["필수 직종·인원 충족", "예산 범위 내 인건비", extra[_top_axis(weights)]]


def _reason_for(weights) -> str:
    return (f"필요 직종·인원을 충족하며 예산 범위 안에서 "
            f"{_AXIS_LABEL[_top_axis(weights)]} 우선순위를 반영한 조합입니다.")


def _flatten_required(required_workers) -> list[str]:
    out = []
    for spec in required_workers or []:
        out += [spec["trade"]] * int(spec.get("count", 0))
    return out


def _gap_trades(required_workers, fixed_members) -> list[str]:
    """결원 후 남은 필요 직종(ANY 포함). 고정 인원이 커버한 직종은 제외하고,
    특정 직종에 매칭되지 않은 고정 인원은 ANY 슬롯을 채운 것으로 간주한다."""
    remaining = _flatten_required(required_workers)   # ANY 포함
    leftover = 0
    for m in fixed_members:
        trade = m.get("assigned_trade")
        if trade in remaining:
            remaining.remove(trade)
        else:
            leftover += 1
    for _ in range(leftover):
        if "ANY" in remaining:
            remaining.remove("ANY")
        else:
            break
    return remaining


def _cancel_existing_crews(request_id: str):
    now = now_iso()
    for c in db.query_crews_by_request(request_id):
        if c.get("status") in (CrewStatus.CANCELLED, CrewStatus.RUNNING,
                               CrewStatus.COMPLETED, CrewStatus.DISPATCHED):
            continue
        db.update_crew(
            c["crew_id"],
            UpdateExpression="SET #s = :s, gsi1sk = :g, updated_at = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": CrewStatus.CANCELLED,
                ":g": db.crew_gsi1sk(CrewStatus.CANCELLED, c["crew_id"]),
                ":t": now,
            },
        )


def _set_request_status(request_id, status, *, composition_error: str | None = None):
    now = now_iso()
    expression = "SET #s = :s, gsi1sk = :g, updated_at = :t"
    values = {":s": status, ":g": db.request_gsi1sk(status, request_id), ":t": now}
    if composition_error:
        expression += ", composition_error = :error"
        values[":error"] = composition_error
    else:
        expression += " REMOVE composition_error"
    db.update_request(
        request_id,
        UpdateExpression=expression,
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues=values,
    )


def _set_gap_status(event_id, status):
    now = now_iso()
    db.update_gap_event(
        event_id,
        UpdateExpression="SET #s = :s, gsi1sk = :g, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":g": db.gap_gsi1sk(status, event_id), ":t": now},
    )


def _to_decimal(value):
    from shared.schemas import to_decimal
    return to_decimal(value)


def _invoke_self(event: dict[str, Any], context: Any) -> None:
    global _LAMBDA_CLIENT
    if _LAMBDA_CLIENT is None:
        import boto3

        _LAMBDA_CLIENT = boto3.client("lambda")
    function_name = getattr(context, "invoked_function_arn", None) or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    if not function_name:
        raise RuntimeError("Lambda function name is unavailable")
    response = _LAMBDA_CLIENT.invoke(
        FunctionName=function_name,
        InvocationType="Event",
        Payload=json.dumps(event, ensure_ascii=False).encode("utf-8"),
    )
    if int(response.get("StatusCode", 0)) != 202:
        raise RuntimeError("asynchronous crew agent invocation was not accepted")


def _start_async(event: dict[str, Any], context: Any) -> dict[str, Any]:
    principal = get_principal(event)
    principal.require_role(Role.OFFICE)
    path = event.get("path") or ""
    params = event.get("pathParameters") or {}
    previous_status = ""
    entity_id = ""
    entity_type = ""

    if path.endswith("/agent-compose"):
        request_id = params.get("requestId") or path.rstrip("/").split("/")[-2]
        request = db.get_request(request_id)
        if not request:
            raise ApiError(ErrorCode.REQUEST_NOT_FOUND, "요청을 찾을 수 없습니다.")
        principal.require_office(request["office_id"])
        if request["status"] in (
            RequestStatus.RUNNING,
            RequestStatus.COMPLETED,
            RequestStatus.CANCELLED,
            RequestStatus.REJECTED,
        ):
            raise ApiError(ErrorCode.STATE_CONFLICT, "종료되거나 작업 중인 요청은 AI 편성을 실행할 수 없습니다.")
        previous_status = request["status"]
        entity_id = request_id
        entity_type = "REQUEST"
        _set_request_status(request_id, RequestStatus.COMPOSING)
    else:
        event_id = params.get("eventId") or path.rstrip("/").split("/")[-2]
        gap = db.get_gap_event(event_id)
        if not gap:
            raise ApiError(ErrorCode.GAP_EVENT_NOT_FOUND, "결원 이벤트를 찾을 수 없습니다.")
        principal.require_office(gap["office_id"])
        previous_status = gap["status"]
        entity_id = event_id
        entity_type = "GAP"
        _set_gap_status(event_id, GapStatus.RECOMPOSING)

    async_event = dict(event)
    async_event["headers"] = {
        key: value for key, value in (event.get("headers") or {}).items()
        if key.lower() != "authorization"
    }
    async_event["_crewAgentAction"] = "RUN"
    async_event["_previousStatus"] = previous_status
    async_event["_entityType"] = entity_type
    async_event["_entityId"] = entity_id
    try:
        _invoke_self(async_event, context)
    except Exception:
        if entity_type == "REQUEST":
            _set_request_status(entity_id, previous_status)
        else:
            _set_gap_status(entity_id, previous_status)
        raise
    return success({"status": "PROCESSING", "entityId": entity_id}, status_code=202)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    if event.get("_crewAgentAction") == "RUN":
        response = router.dispatch(event)
        if int(response.get("statusCode", 500)) >= 400:
            if event.get("_entityType") == "REQUEST":
                try:
                    payload = json.loads(response.get("body") or "{}")
                    error_message = str((payload.get("error") or {}).get("message") or "AI 편성 조건을 충족하는 조합을 찾지 못했습니다.")
                except (TypeError, ValueError, json.JSONDecodeError):
                    error_message = "AI 편성 조건을 충족하는 조합을 찾지 못했습니다."
                _set_request_status(
                    event["_entityId"],
                    event.get("_previousStatus") or RequestStatus.REQUESTED,
                    composition_error=error_message,
                )
            elif event.get("_entityType") == "GAP":
                _set_gap_status(event["_entityId"], GapStatus.FAILED)
        return response
    if ASYNC_ENABLED:
        try:
            return _start_async(event, _context)
        except ApiError as exc:
            return exc.to_response()
        except Exception:
            logger.exception("crew_agent_async_start_failed")
            return ApiError(ErrorCode.INTERNAL_ERROR, "AI 편성 작업을 시작하지 못했습니다.").to_response()
    return router.dispatch(event)
