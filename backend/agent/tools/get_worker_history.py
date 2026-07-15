"""``get_worker_history`` — 읽기 전용 Agent Tool (계약 v2).

지정 근로자들의 제한된 작업/협업 이력을 반환한다. 개인정보(name/phone 등)와
성실도·부정 신호는 반환하지 않는다 (판단용 중립 신호만: 경력/자격/협업 횟수).

작업/협업 이력은 Assignments GSI1 에서 유도한다 (별도 테이블 없음).
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from ._shared import resolve_db, tool

_JUDGMENT_FIELDS = ("career_years", "certifications")


def _read_worker_history(worker_ids: Sequence[str], *, db: Any = None) -> Dict[str, Any]:
    """제한된 판단 신호 + 협업 횟수를 반환한다 (읽기 전용).

    각 근로자의 Assignments(GSI1)에서 참여 crew 집합을 구해, 주어진 근로자들 간
    같은 crew 공유 횟수를 협업 횟수로 집계한다. 개인정보·성실도는 제외한다.
    """
    ids = list(worker_ids)
    helper = resolve_db(db)

    crews_by_worker: Dict[str, set] = {}
    for wid in ids:
        crews = set()
        for a in helper.query_worker_assignments(wid):
            if a.get("crew_id"):
                crews.add(a["crew_id"])
        crews_by_worker[wid] = crews

    pairs: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {wid: 0 for wid in ids}
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            shared = len(crews_by_worker[a] & crews_by_worker[b])
            if shared > 0:
                pairs.append({"worker_a": a, "worker_b": b, "count": shared})
                counts[a] += shared
                counts[b] += shared

    workers = []
    for wid in ids:
        rec = helper.get_worker(wid) or {}
        limited = {"worker_id": wid, "collaboration_count": counts.get(wid, 0)}
        for f in _JUDGMENT_FIELDS:
            if f in rec:
                limited[f] = rec[f]
        workers.append(limited)

    return {"workers": workers, "collaboration_pairs": pairs}


@tool
def get_worker_history(worker_ids: List[str]) -> Dict[str, Any]:
    """지정 근로자들의 제한된 작업·협업 이력을 반환한다 (읽기 전용, 개인정보·성실도 제외).

    Args:
        worker_ids: 이력을 조회할 근로자 목록.

    Returns:
        {workers: [{worker_id, career_years, certifications, collaboration_count}],
         collaboration_pairs: [{worker_a, worker_b, count}]}.
    """
    return _read_worker_history(worker_ids)
