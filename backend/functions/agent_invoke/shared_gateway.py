"""High-level DB gateway for 담당자 B's Lambdas over 담당자 A's real ``backend/shared/*``.

담당자 B's code (assembler / persistence / the two handlers / the read-only Agent tools)
was written against a *high-level* database contract — ten domain functions such as
``get_work_request`` / ``query_ready_workers`` / ``save_crew`` /
``transition_request_status`` — that mirrored the "소비하는 shared 계약" table in
``design.md``. 담당자 A's real ``backend/shared/db`` instead exposes a *low-level*
single-table API (``get_item`` / ``put_item`` / ``update_item`` + key builders + GSI
queries) plus ``backend/shared/schemas`` item builders and ``backend/shared/state`` enums.

This module is the thin ADAPTER that maps the high-level contract my code already calls
onto 담당자 A's real low-level API. My call sites import it as::

    from functions.agent_invoke import shared_gateway as db

so the existing ``db.xxx(...)`` calls keep working unchanged, but now run against the real
single-table backend (and, in tests, against the in-memory ``FakeSharedDB`` — the tests
monkeypatch these ten module-level functions onto the fake).

Design boundary
---------------
The adapter carries NO business logic: it only maps names/shapes and applies the exact
conditional-write pattern 담당자 A uses in ``office_core`` / ``assignment`` (an
``UpdateExpression`` that also rewrites ``GSI1SK`` + ``updated_at`` guarded by a
``ConditionExpression`` on the reserved ``status`` attribute, catching the
conditional-check failure). ``status`` is a DynamoDB reserved word, so it is always
addressed via the ``#status`` ``ExpressionAttributeName``.

Documented mapping decisions / limitations
------------------------------------------
- ``query_ready_workers`` : 담당자 A's ``query_office_workers_by_state`` has no trade
  argument, so the (optional) trade filter is applied here in Python after the READY
  query. Passing an empty/omitted ``trades`` applies no filter.
- ``get_worker_collaborations`` : the real Collaboration items are stored bidirectionally
  (``PK=WORKER#{owner}``, ``SK=COLLAB#{other}#{date}``; one item per shared job, no
  ``count`` field). This adapter aggregates a per-pair count for pairs where BOTH workers
  are in scope, halving the raw tally because each shared job is stored as two items
  (``a→b`` and ``b→a``). The Agent tolerates an empty ``collaboration_pairs``, so an
  unexpected shape degrades to ``[]`` rather than raising.
- ``save_crew`` : builds the canonical ``schemas.build_crew`` item. The canonical Crew
  schema has NO ``gap_event_id`` / ``current_crew_id`` field, so the Crew↔GapEvent linkage
  is intentionally NOT persisted on the Crew item — it is surfaced only in the agent_invoke
  RESPONSE (sourced from ``SaveContext``). ``rank`` / ``considerations`` are likewise not in
  the canonical schema; the persistence layer folds ``considerations`` into ``rationale``
  and drops ``rank`` (see ``persistence.py``).

Python 3.9 note: ``from __future__ import annotations`` keeps annotations lazy.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from botocore.exceptions import ClientError

from shared import db as _db
from shared import schemas as _schemas
from shared.state import CrewStatus, GapStatus, WorkerState

__all__ = [
    "get_work_request",
    "query_ready_workers",
    "get_workers",
    "get_worker_collaborations",
    "get_crew",
    "get_gap_event",
    "save_crew",
    "save_gap_event",
    "transition_request_status",
    "transition_gap_event_status",
    "record_gap_recommendations",
]

_META = "META"
_PROFILE = "PROFILE"

# DynamoDB reserved word — always addressed via an ExpressionAttributeName.
_STATUS_NAME = {"#status": "status"}

# The AWS conditional-check-failed error code returned when a ConditionExpression is not met.
_CONDITIONAL_CHECK_FAILED = "ConditionalCheckFailedException"


# --------------------------------------------------------------------------- #
# Reads                                                                        #
# --------------------------------------------------------------------------- #
def get_work_request(request_id: str) -> Optional[Dict[str, Any]]:
    """Return the WorkRequest META item (``None`` when absent)."""
    return _db.get_item(_db.request_pk(request_id), _META)


def query_ready_workers(
    office_id: str, trades: Optional[Iterable[str]] = None
) -> List[Dict[str, Any]]:
    """Return the office's READY workers, optionally narrowed to ``trades``.

    Wraps ``db.query_office_workers_by_state(office_id, READY)`` (which already scopes to
    the office and the READY state) and applies the trade filter in Python because the real
    query has no trade argument. An empty/omitted ``trades`` applies no filter.
    """
    workers = _db.query_office_workers_by_state(office_id, WorkerState.READY)
    if trades:
        allowed = set(trades)
        workers = [w for w in workers if w.get("trade") in allowed]
    return workers


def get_workers(worker_ids: Iterable[str]) -> List[Dict[str, Any]]:
    """Batch-read worker PROFILE items by id, dropping any that are absent."""
    out: List[Dict[str, Any]] = []
    for wid in worker_ids:
        item = _db.get_item(_db.worker_pk(wid), _PROFILE)
        if item is not None:
            out.append(item)
    return out


def get_worker_collaborations(worker_ids: Iterable[str]) -> List[Dict[str, Any]]:
    """Return prior-collaboration pairs among ``worker_ids`` as ``{worker_a, worker_b, count}``.

    Reads each worker's Collaboration items (``db.query_worker_collaborations``), keeps only
    those whose ``other_id`` is ALSO in scope, and aggregates a per-pair count. Because the
    real items are bidirectional (each shared job stored as ``a→b`` and ``b→a``), the raw
    tally is halved so the reported ``count`` reflects the number of shared jobs. Returns
    ``[]`` when nothing is in scope; the Agent tolerates an empty collaboration list.
    """
    id_set = set(worker_ids)
    if not id_set:
        return []

    pair_counts: Dict[tuple, int] = {}
    for wid in id_set:
        for item in _db.query_worker_collaborations(wid):
            owner = item.get("worker_id", wid)
            other = item.get("other_id")
            if other is None or other not in id_set:
                continue
            key = tuple(sorted((str(owner), str(other))))
            pair_counts[key] = pair_counts.get(key, 0) + 1

    pairs: List[Dict[str, Any]] = []
    for (worker_a, worker_b), raw in pair_counts.items():
        # Each shared job is stored as two items (a→b and b→a); both are counted when both
        # workers are in scope, so halve to recover the shared-job count (>= 1).
        pairs.append({"worker_a": worker_a, "worker_b": worker_b, "count": max(1, raw // 2)})
    return pairs


def get_crew(crew_id: str) -> Optional[Dict[str, Any]]:
    """Return the Crew META item (``None`` when absent)."""
    return _db.get_item(_db.crew_pk(crew_id), _META)


def get_gap_event(event_id: str) -> Optional[Dict[str, Any]]:
    """Return the GapEvent META item (``None`` when absent)."""
    return _db.get_item(_db.gap_pk(event_id), _META)


# --------------------------------------------------------------------------- #
# Writes                                                                       #
# --------------------------------------------------------------------------- #
def save_crew(
    *,
    office_id: Optional[str],
    request_id: str,
    member_ids: Iterable[str],
    rationale: str = "",
    estimated_cost: Optional[int] = None,
    source: str = "AGENT",
    status: Optional[str] = None,
    crew_id: Optional[str] = None,
) -> str:
    """Build a canonical ``Crew`` item via ``schemas.build_crew`` and persist it.

    Defaults ``status`` to ``CrewStatus.PROPOSED`` (an AGENT proposal). Returns the crew id
    (assigned by ``build_crew`` when ``crew_id`` is ``None``). See the module docstring for
    the dropped-field / linkage decisions.
    """
    item = _schemas.build_crew(
        office_id=office_id,
        request_id=request_id,
        member_ids=list(member_ids),
        status=status or CrewStatus.PROPOSED,
        crew_id=crew_id,
        source=source,
        rationale=rationale,
        estimated_cost=estimated_cost,
    )
    _db.put_item(item)
    return item["crew_id"]


def save_gap_event(
    *,
    office_id: Optional[str],
    crew_id: Optional[str],
    request_id: str,
    gap_type: str,
    missing_worker_ids: Iterable[str],
    status: Optional[str] = None,
    event_id: Optional[str] = None,
) -> str:
    """Build a canonical ``GapEvent`` item via ``schemas.build_gap_event`` and persist it.

    Defaults ``status`` to ``GapStatus.DETECTED``. Returns the event id (assigned by
    ``build_gap_event`` when ``event_id`` is ``None``).
    """
    item = _schemas.build_gap_event(
        office_id=office_id,
        crew_id=crew_id,
        request_id=request_id,
        gap_type=gap_type,
        missing_worker_ids=list(missing_worker_ids),
        status=status or GapStatus.DETECTED,
        event_id=event_id,
    )
    _db.put_item(item)
    return item["event_id"]


# --------------------------------------------------------------------------- #
# Conditional status transitions                                               #
# (mirror 담당자 A's office_core / assignment conditional-write pattern)         #
# --------------------------------------------------------------------------- #
def transition_request_status(request_id: str, expected: str, target: str) -> bool:
    """Conditionally move a WorkRequest ``expected → target``; ``False`` on a state conflict.

    Rewrites ``status`` + ``GSI1SK`` + ``updated_at`` guarded by ``status = expected``.
    A ``ConditionalCheckFailedException`` (wrong/changed state, or the item is absent) maps
    to ``False`` (a ``STATE_CONFLICT`` signal); success returns ``True``.
    """
    try:
        _db.update_item(
            _db.request_pk(request_id),
            _META,
            UpdateExpression="SET #status = :t, GSI1SK = :g, updated_at = :u",
            ConditionExpression="#status = :e",
            ExpressionAttributeNames=dict(_STATUS_NAME),
            ExpressionAttributeValues={
                ":t": target,
                ":e": expected,
                ":g": _db.request_gsi1sk(target, request_id),
                ":u": _schemas.now_iso(),
            },
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == _CONDITIONAL_CHECK_FAILED:
            return False
        raise


def transition_gap_event_status(event_id: str, expected: str, target: str) -> bool:
    """Conditionally move a GapEvent ``expected → target``; ``False`` on a state conflict.

    Same conditional-write shape as :func:`transition_request_status`, over the GapEvent key
    and ``gap_gsi1sk``.
    """
    try:
        _db.update_item(
            _db.gap_pk(event_id),
            _META,
            UpdateExpression="SET #status = :t, GSI1SK = :g, updated_at = :u",
            ConditionExpression="#status = :e",
            ExpressionAttributeNames=dict(_STATUS_NAME),
            ExpressionAttributeValues={
                ":t": target,
                ":e": expected,
                ":g": _db.gap_gsi1sk(target, event_id),
                ":u": _schemas.now_iso(),
            },
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == _CONDITIONAL_CHECK_FAILED:
            return False
        raise


def record_gap_recommendations(
    event_id: str,
    *,
    fixed_member_ids: Iterable[str],
    recommendations: List[Dict[str, Any]],
    expected: str,
    target: str,
) -> bool:
    """Conditionally transition a GapEvent ``expected → target`` AND record the EMERGENCY
    recommendations on the item, in ONE atomic conditional write; ``False`` on state conflict.

    This is the option-1 EMERGENCY hand-off write (담당자 B): no separate Crew is created for
    an emergency re-composition. Instead the retained team (``fixed_member_ids``) and the
    Agent's 1..3 alternatives (``recommendations`` — each ``{replacement_member_ids,
    total_cost, reason}``) are written straight onto the GapEvent so 담당자 A's emergency
    approval API (``approve_emergency``) can read them and the OFFICE can pick a
    ``replacement_member_ids`` set to approve.

    Same conditional-write shape as :func:`transition_gap_event_status` (status + ``GSI1SK`` +
    ``updated_at`` guarded by ``status = expected``), extended to also SET ``fixed_member_ids``
    and ``recommendations``. Combining the transition with the recommendation write keeps the
    two consistent — a state conflict (``ConditionalCheckFailedException`` → ``False``) leaves
    both the status and the recommendations untouched.
    """
    try:
        _db.update_item(
            _db.gap_pk(event_id),
            _META,
            UpdateExpression=(
                "SET #status = :t, GSI1SK = :g, fixed_member_ids = :f, "
                "recommendations = :r, updated_at = :u"
            ),
            ConditionExpression="#status = :e",
            ExpressionAttributeNames=dict(_STATUS_NAME),
            ExpressionAttributeValues={
                ":t": target,
                ":e": expected,
                ":g": _db.gap_gsi1sk(target, event_id),
                ":f": list(fixed_member_ids),
                ":r": list(recommendations),
                ":u": _schemas.now_iso(),
            },
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == _CONDITIONAL_CHECK_FAILED:
            return False
        raise
