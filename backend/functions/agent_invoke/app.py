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
- LLM(Strands+Bedrock) 추천을 우선 시도하고, 검증 실패 시 1회 재시도, 그래도 실패하거나
  Bedrock 미가용 시 결정론적 폴백으로 동일 규칙의 추천을 생성한다(데모 안정성).
"""

from __future__ import annotations

import logging
import os
import random
from typing import Any

from shared import db
from shared.auth import Principal
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
        raise ApiError(ErrorCode.AGENT_RETRY_FAILED,
                       "READY 상태 후보가 부족하여 AI 편성에 실패했습니다. 수동 편성으로 진행해주세요.")

    recs = _recommend(candidates, needed, int(request.get("budget", 0)),
                      priority=request.get("priority"), request=request)
    if not recs:
        raise ApiError(ErrorCode.AGENT_RETRY_FAILED,
                       "예산 범위 내에서 가능한 조합을 찾지 못했습니다. 예산 조정 또는 수동 편성이 필요합니다.")

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
                      priority=request.get("priority"), request=request,
                      context_worker_ids=[m["worker_id"] for m in fixed])
    if not recs:
        _set_gap_status(gap["event_id"], GapStatus.FAILED)
        raise ApiError(ErrorCode.AGENT_RETRY_FAILED,
                       "대체 가능한 인력을 찾지 못했습니다. 수동 편성 또는 편성 취소가 필요합니다.")

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
# 추천 엔진: LLM(선택) → 검증 → 결정론적 폴백
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


def _recommend(candidates, needed_trades, budget, *, priority=None,
               request=None, context_worker_ids=None) -> list[dict[str, Any]]:
    """LLM 추천을 우선 시도하고(가용 시), 검증 실패 시 결정론적 추천으로 폴백한다.

    priority(순위)·경력·협업 이력을 종합해 정렬/평가하고, 각 추천안에 적합도(fitness %)를 부여한다.
    """
    weights = _priority_weights(priority)
    collab = _CollabIndex()
    context_ids = list(context_worker_ids or [])
    wages = [int(w.get("desired_daily_wage", 0)) for w in candidates] or [0]
    wage_lo, wage_hi = min(wages), max(wages)

    recs = None
    llm_recs = _try_llm(candidates, needed_trades, budget, priority, request, collab)
    if llm_recs:
        valid = [r for r in llm_recs if _valid_rec(r, candidates, needed_trades, budget)]
        if valid:
            recs = valid[:3]
    if recs is None:
        recs = _greedy(candidates, needed_trades, budget, weights, collab, context_ids)

    for i, r in enumerate(recs, start=1):
        r["rank"] = i
        r["fitness"] = _fitness_percent(r["members"], weights, collab, context_ids, wage_lo, wage_hi)
    return recs


_BEDROCK_SYSTEM = (
    "당신은 건설 일용직 작업조 편성 추천 AI입니다. 제공된 후보 근로자만으로 요청 직종·인원을 "
    "정확히 충족하는 작업조 조합을 1~3개 추천합니다.\n"
    "규칙(엄수): (1) 후보 목록 밖 worker_id 생성 금지. (2) 각 멤버의 assigned_trade는 그 근로자의 "
    "excluded_trades에 포함되면 안 됨(가능하면 preferred_trades 내에서 배정). (3) 필요 직종별 인원을 "
    "정확히 충족(미달·초과 금지). trade가 \"ANY\"인 요구는 직종 무관이며 어떤 후보로도 채울 수 있다"
    "(이때 assigned_trade는 후보의 preferred_trades 중 하나 또는 GENERAL을 사용). "
    "(4) 한 추천 안에서 worker_id 중복 금지. (5) offered_wage 합이 예산 이내. "
    "(6) priority는 cost·career·teamwork의 우선순위 순위(1=최우선, 3=최하위)이며, 순위가 높은(숫자가 "
    "작은) 축을 더 크게 반영한다: cost=인건비 저렴, career=career_years 많음, teamwork=collaboration_pairs "
    "협업 이력 많음. (7) 사유는 업무 정보 중심, 특정 근로자 부정 평가·확률 수치·최적 보장 표현 금지.\n"
    "출력은 아래 JSON 하나만. 다른 텍스트·코드펜스 금지:\n"
    '{"recommendations":[{"members":[{"worker_id":"..","assigned_trade":".."'
    ',"offered_wage":0}],"reason":"..","considerations":["..",".."]}]}'
)


def _try_llm(candidates, needed_trades, budget, priority=None, request=None, collab=None):
    """Amazon Bedrock(boto3 converse) 직접 호출로 추천 생성. 미가용/오류 시 None → 결정론 폴백.

    Strands/pydantic 등 네이티브 의존성 없이 Lambda 런타임의 boto3만 사용한다(레이어·Docker 불필요).
    우선순위(순위)·협업 이력(collaboration_pairs)·현장/일시를 함께 전달한다.
    """
    import json as _json
    from collections import Counter

    try:
        import boto3
        from botocore.config import Config
    except Exception:  # noqa: BLE001
        return None

    model_id = os.environ.get("CREW_AGENT_MODEL_ID")
    if not model_id:
        return None
    region = os.environ.get("CREW_AGENT_REGION") or os.environ.get("AWS_REGION") or "ap-northeast-2"
    timeout_s = float(os.environ.get("AGENT_INVOKE_TIMEOUT_S", "25"))

    need = Counter(needed_trades)
    cand_summary = [
        {
            "worker_id": w["worker_id"],
            "preferred_trades": list(w.get("preferred_trades") or []),
            "excluded_trades": list(w.get("excluded_trades") or []),
            "desired_daily_wage": int(w.get("desired_daily_wage", 0)),
            "career_years": int(w.get("career_years", 0)),
        }
        for w in candidates
    ]
    collab_pairs = collab.pairs([w["worker_id"] for w in candidates]) if collab is not None else []
    req = request or {}
    user_msg = _json.dumps(
        {
            "required_workers": [{"trade": t, "count": c} for t, c in need.items()],
            "budget": budget if budget and budget > 0 else None,
            "priority": priority if isinstance(priority, dict) else None,
            "site": req.get("site_name") or "",
            "work_date": req.get("work_date") or "",
            "start_time": req.get("start_time") or "",
            "candidates": cand_summary,
            "collaboration_pairs": collab_pairs,
        },
        ensure_ascii=False,
    )

    try:
        client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(read_timeout=timeout_s, connect_timeout=5, retries={"max_attempts": 0}),
        )
        resp = client.converse(
            modelId=model_id,
            system=[{"text": _BEDROCK_SYSTEM}],
            messages=[{"role": "user", "content": [{"text": user_msg}]}],
            inferenceConfig={"maxTokens": 1500, "temperature": 0.2},
        )
        text = "".join(
            block.get("text", "")
            for block in resp["output"]["message"]["content"]
            if isinstance(block, dict)
        )
    except Exception:  # noqa: BLE001 - Bedrock 미가용/타임아웃/권한
        logger.info("bedrock_unavailable_fallback_deterministic")
        return None

    return _parse_llm_recs(text, candidates)


def _parse_llm_recs(text, candidates):
    """모델 텍스트에서 JSON을 추출·파싱하여 추천 리스트로 변환한다."""
    import json as _json

    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw[:4].lower() == "json":
            raw = raw[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        data = _json.loads(raw[start:end + 1])
    except (ValueError, TypeError):
        return None

    by_id = {w["worker_id"]: w for w in candidates}
    recs = []
    for rec in data.get("recommendations", []):
        members = []
        for m in rec.get("members", []):
            w = by_id.get(m.get("worker_id"))
            if not w:
                members = []
                break
            try:
                wage = int(m.get("offered_wage") or w.get("desired_daily_wage", 0))
            except (ValueError, TypeError):
                wage = int(w.get("desired_daily_wage", 0))
            members.append(_member(w, m.get("assigned_trade"), wage))
        if not members:
            continue
        recs.append(_rec(len(recs) + 1, members,
                         rec.get("reason", ""), list(rec.get("considerations") or [])))
    return recs or None


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


def _set_request_status(request_id, status):
    now = now_iso()
    db.update_request(
        request_id,
        UpdateExpression="SET #s = :s, gsi1sk = :g, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":g": db.request_gsi1sk(status, request_id), ":t": now},
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


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return router.dispatch(event)
