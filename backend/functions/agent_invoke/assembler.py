"""Candidate + validation-context assembler for the agent_invoke Lambda (담당자 B, task 5.1).

This module is the **I/O boundary** that feeds the otherwise-pure pieces of the compose
flow. It does two jobs, both by *consuming* 담당자 A's ``backend/shared/db`` read helpers
(never touching a table directly, Req 6.4):

1. :func:`assemble_normal_input` - builds the ``AgentInput(mode=NORMAL)`` the Agent
   composes against: the work-request spec, the READY candidate pool (office-scoped), and
   the candidates' collaboration history. (Req 6.1, 6.3, 6.4, 6.5 / 2.1-2.4)
2. :func:`build_validation_context` - assembles the **freshest-snapshot**
   :class:`~functions.agent_invoke.validator.ValidationContext` used *immediately
   before* validation, so the safety checks judge the Agent output against current worker
   state rather than the (possibly stale) values that were passed into the Agent.
   (Req 7.3, 7.6, 7.7)

Why the split - "검증 직전 최신 스냅샷" (tasks.md Overview)
-------------------------------------------------------
The Agent input is assembled once at the start of the flow; by the time the Agent has
produced output, a worker's state / crew / wage may have changed. Trusting the input for
the safety checks would let a stale READY worker (now RUNNING elsewhere) slip through.
So this module re-reads the recommended members via ``db.get_workers`` **at validation
time** and injects that snapshot into the pure validator. The validator
(``validator.py``) stays I/O-free and deterministic; all freshness lives here.

Freshest-snapshot approach chosen (design.md offers two; this is option "b" + fallback)
--------------------------------------------------------------------------------------
:meth:`ValidationContext.build` derives ``trade_by_worker`` / ``wage_by_worker`` from the
candidate / fixed-member *pools* it is handed - i.e. the values that were passed into the
Agent (potentially stale). To make the server-side checks depend on **current truth**,
:func:`build_validation_context`:

- sources ``worker_states`` (``state`` + ``current_crew_id``) **entirely** from the fresh
  ``db.get_workers`` batch read - so ``new_ready`` (Property 2 / Req 7.3) and
  ``no_conflict_assignment`` (Property 6 / Req 7.7) see current state; and
- **overlays** ``trade_by_worker`` / ``wage_by_worker`` with the fresh ``get_workers``
  values on top of the pool-derived baseline - so ``total_cost`` (Property 5 / Req 7.6)
  and ``trade_headcount`` (Property 4) are computed from server-side wages/trades. The
  pool-derived values remain only as a *fallback* for any recommended worker the batch
  read did not return (e.g. a fixed member absent from the workers table).

A recommended worker that has *vanished* from ``get_workers`` gets **no** snapshot; the
validator then treats it as non-READY and fails closed (``new_ready`` uses ``"MISSING"``
for absent snapshots) - the safe outcome.

Assumed shapes & documented fallbacks (담당자 A owns the real schemas)
------------------------------------------------------------------
The exact ``work_request`` / ``worker`` record shapes are 담당자 A's. This module maps the
fields ``RequestSpec`` / ``Candidate`` need by their conceptual names (design.md
"소비하는 shared 계약") and applies conservative fallbacks when an optional field is
absent, so assembly never crashes on a slightly different shape:

- work_request → ``RequestSpec``: ``required_workers`` (list of ``{trade, count}``),
  ``budget``, ``priority`` (``{cost, skill, teamwork}``), ``site``, ``work_date``,
  ``start_time``. Missing ``budget`` → :data:`_DEFAULT_BUDGET` (large, non-constraining -
  budget is an Agent hint, never a validation check); missing ``priority`` → all
  ``MEDIUM``; missing text fields → ``""``; missing ``required_workers`` → ``[]``.
- worker → ``Candidate``: ``worker_id``, ``trade``, ``skill_level``, ``desired_daily_wage``,
  ``certifications``, ``career_years``. Missing ``skill_level`` → :data:`_DEFAULT_SKILL_LEVEL`,
  missing ``career_years`` → 0, missing ``certifications`` → ``[]``.

Crucially, these ``Candidate`` fallbacks are **not** safety-critical: the validator's
wage/state/trade checks use the *freshest* ``get_workers`` snapshot (see above), not the
candidate-pool values assembled here, so a defaulted candidate wage can never make an
invalid recommendation pass validation.

DynamoDB ``Decimal`` normalization
----------------------------------
DynamoDB returns numbers as ``Decimal``. The Agent schemas parse with ``strict=True``
(``agent/schemas.py``), which rejects ``Decimal``/``float`` where an ``int`` is required.
:func:`_as_int` normalizes every numeric field to ``int`` so assembly is robust whether
the (real) helper returns ``Decimal`` or the (test) stub returns plain ``int``.

shared helper consumption
-------------------------
``backend/shared/*`` is 담당자 A's and is **consumed, never implemented**; it does not
exist on disk in this scope. ``db`` is imported **lazily inside each function** (matching
``persistence.py``) so it resolves at call time - the real Layer module in deployment, or
the stub installed under ``shared.db`` by ``install_shared_stubs`` in tests -
regardless of import order. This module performs reads/assembly only: it never writes,
never transitions state, and never changes worker state (Req 6.4, delegation to 담당자 A).

Python 3.9 note
---------------
``from __future__ import annotations`` keeps annotations lazy so the builtin-generic
style resolves on the local Python 3.9 runtime; ``Optional[...]`` is used for nullable
fields (mirrors ``schemas.py`` / ``validator.py``).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from agent.schemas import (
    AgentInput,
    Candidate,
    CollaborationPair,
    FixedMember,
    Priority,
    RequestSpec,
    TradeRequirement,
)
from functions.agent_invoke.validator import (
    ValidationContext,
    WorkerStateSnapshot,
)

__all__ = [
    "assemble_normal_input",
    "build_validation_context",
]

# Mode + sentinel constants.
_MODE_NORMAL = "NORMAL"
_PRIORITY_DEFAULT = "MEDIUM"  # neutral weight when the request omits a priority axis
_STATE_UNKNOWN = "UNKNOWN"  # non-READY sentinel when a fresh record omits its state

# Documented fallbacks for absent OPTIONAL fields on 담당자 A's records. These affect only
# the Agent-facing input (hints); the safety checks use the freshest get_workers snapshot,
# so none of these can make an invalid recommendation pass validation (see module docstring).
_DEFAULT_BUDGET = 100_000_000  # large => does not artificially constrain composition
_DEFAULT_SKILL_LEVEL = 1  # lowest valid skill_level (schema constrains 1..5)
_DEFAULT_WAGE = 1  # smallest positive wage (schema constrains > 0); malformed record only
_DEFAULT_CAREER_YEARS = 0
_DEFAULT_TRADE = "GENERAL"  # design.md lists GENERAL among the example trades


# --------------------------------------------------------------------------- #
# Coercion helpers - normalize 담당자 A's (Decimal-bearing) records to the       #
# strict Agent schemas.                                                        #
# --------------------------------------------------------------------------- #
def _as_int(value: Any, default: int) -> int:
    """Coerce a possibly-``Decimal``/``None`` numeric to ``int``.

    ``None`` → ``default``; anything else passes through ``int(...)`` so the value
    satisfies the strict integer schema fields (Pydantic ``strict=True`` rejects
    ``Decimal``/``float`` in an ``int`` field). DynamoDB returns numbers as ``Decimal``,
    so this normalization is what lets assembly consume the real helper's output.
    """
    if value is None:
        return default
    return int(value)


def _coerce_priority(raw: Any) -> Priority:
    """Map a priority record (or existing :class:`Priority`) to a :class:`Priority`.

    Each axis (cost / skill / teamwork) falls back to ``MEDIUM`` when absent/empty. A
    present-but-invalid level surfaces as a parse error (bad data is not silently masked).
    """
    if isinstance(raw, Priority):
        return raw
    if isinstance(raw, Mapping):
        return Priority(
            cost=raw.get("cost") or _PRIORITY_DEFAULT,
            skill=raw.get("skill") or _PRIORITY_DEFAULT,
            teamwork=raw.get("teamwork") or _PRIORITY_DEFAULT,
        )
    return Priority(
        cost=_PRIORITY_DEFAULT, skill=_PRIORITY_DEFAULT, teamwork=_PRIORITY_DEFAULT
    )


def _coerce_trade_requirement(raw: Any) -> TradeRequirement:
    """Map a ``{trade, count}`` record (or existing :class:`TradeRequirement`)."""
    if isinstance(raw, TradeRequirement):
        return raw
    return TradeRequirement(
        trade=(raw.get("trade") or _DEFAULT_TRADE),
        count=_as_int(raw.get("count"), 1),
    )


def _to_candidate(worker: Mapping[str, Any]) -> Candidate:
    """Map a READY-worker record from ``query_ready_workers`` to a :class:`Candidate`.

    ``worker_id`` is required (a worker without an id is unusable); every other field
    falls back to a documented default so a lean worker record still yields a valid
    candidate. These values are Agent hints only - validation uses the fresh snapshot.
    """
    return Candidate(
        worker_id=worker["worker_id"],
        trade=(worker.get("trade") or _DEFAULT_TRADE),
        skill_level=_as_int(worker.get("skill_level"), _DEFAULT_SKILL_LEVEL),
        desired_daily_wage=_as_int(worker.get("desired_daily_wage"), _DEFAULT_WAGE),
        certifications=list(worker.get("certifications") or []),
        career_years=_as_int(worker.get("career_years"), _DEFAULT_CAREER_YEARS),
    )


def _to_collaboration_pair(raw: Mapping[str, Any]) -> Optional[CollaborationPair]:
    """Map a collaboration record to a :class:`CollaborationPair`, or drop it.

    Pairs with a non-positive ``count`` are dropped (``CollaborationPair.count`` is
    constrained ``> 0``, and a zero-count "collaboration" carries no signal).
    """
    count = _as_int(raw.get("count"), 0)
    if count <= 0:
        return None
    return CollaborationPair(
        worker_a=raw["worker_a"], worker_b=raw["worker_b"], count=count
    )


def _build_request_spec(record: Mapping[str, Any], request_id: str) -> RequestSpec:
    """Map a work-request record to a :class:`RequestSpec` (see module docstring)."""
    required_workers = [
        _coerce_trade_requirement(tr) for tr in (record.get("required_workers") or [])
    ]
    return RequestSpec(
        request_id=str(record.get("request_id") or request_id),
        required_workers=required_workers,
        budget=_as_int(record.get("budget"), _DEFAULT_BUDGET),
        priority=_coerce_priority(record.get("priority")),
        # The real WorkRequest schema stores the site under ``site_name`` (담당자 A); fall
        # back to ``site`` (and then "") defensively for the in-memory fake / lean records.
        site=str(record.get("site_name") or record.get("site") or ""),
        work_date=str(record.get("work_date") or ""),
        start_time=str(record.get("start_time") or ""),
    )


def _required_trades(required_workers: List[TradeRequirement]) -> Optional[List[str]]:
    """Distinct required trades (first-appearance order) to narrow the READY query.

    Returns ``None`` when there are no required trades so ``query_ready_workers`` applies
    no trade filter (passing an empty list would wrongly filter *every* worker out).
    """
    trades: List[str] = []
    seen: set = set()
    for tr in required_workers:
        if tr.trade not in seen:
            seen.add(tr.trade)
            trades.append(tr.trade)
    return trades or None


def _dedupe(ids: Iterable[str]) -> List[str]:
    """Unique ids preserving first-appearance order (one batch read, no repeats)."""
    seen: set = set()
    out: List[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #
def assemble_normal_input(request_id: str, office_id: str) -> AgentInput:
    """Assemble the ``AgentInput(mode=NORMAL)`` for a compose request.

    Reads (via 담당자 A's ``shared/db`` helpers only, Req 6.4):

    1. ``get_work_request(request_id)`` → the request spec.
    2. ``query_ready_workers(office_id, trades)`` → the candidate pool. This helper already
       scopes results to ``office_id`` match **and** ``state=READY`` (Req 6.3), so the
       candidates are in-scope by construction; ``trades`` (derived from the request's
       required trades) narrows the pool to what the request needs.
    3. ``get_worker_collaborations(candidate_ids)`` → the collaboration history among the
       candidates.

    Returns an ``AgentInput`` with ``mode=NORMAL`` and an empty ``fixed_members`` (fixed
    members exist only in EMERGENCY re-composition, assembled elsewhere). Performs reads
    only - no writes, no state transitions.

    Raises ``ValueError`` if the work request does not exist. (In the normal flow the
    handler's ``REQUESTED → COMPOSING`` conditional transition runs first and already
    proves existence, so this is a defensive guard.)
    """
    from functions.agent_invoke import shared_gateway as db  # high-level adapter over 담당자 A's shared

    record = db.get_work_request(request_id)
    if record is None:
        raise ValueError(f"work request not found: {request_id!r}")

    request = _build_request_spec(record, request_id)
    trades = _required_trades(request.required_workers)

    worker_records = db.query_ready_workers(office_id, trades)
    candidates = [_to_candidate(w) for w in worker_records]

    candidate_ids = [c.worker_id for c in candidates]
    collaboration_records = db.get_worker_collaborations(candidate_ids)
    collaboration_pairs = [
        pair
        for pair in (_to_collaboration_pair(r) for r in collaboration_records)
        if pair is not None
    ]

    return AgentInput(
        mode=_MODE_NORMAL,
        request=request,
        fixed_members=[],
        candidates=candidates,
        collaboration_pairs=collaboration_pairs,
    )


def build_validation_context(
    output_member_ids: Iterable[str],
    *,
    mode: str,
    candidates: List[Candidate],
    fixed_members: List[FixedMember],
    required_workers: List[TradeRequirement],
    current_crew_id: Optional[str] = None,
) -> ValidationContext:
    """Assemble the **freshest-snapshot** :class:`ValidationContext` for validation.

    Called *immediately before* ``validate_output`` with the ``member_ids`` the Agent
    recommended (the union across recommendations). It re-reads those workers via
    ``db.get_workers`` to capture their **current** ``state`` / ``current_crew_id`` /
    ``desired_daily_wage`` / ``trade``, then builds the context so the server-side checks
    judge against that fresh snapshot rather than the (stale) values handed to the Agent
    (tasks.md "검증 직전 최신 스냅샷"; Req 7.3, 7.6, 7.7).

    Parameters
    ----------
    output_member_ids:
        The recommended worker ids to snapshot (deduplicated internally into one batch
        read). Includes EMERGENCY fixed members, since every recommendation carries them.
    mode:
        ``"NORMAL"`` / ``"EMERGENCY"`` - governs the ``fixed_preserved`` check.
    candidates, fixed_members:
        The Agent-input pools, used for member **provenance** (``member_exists``) and as a
        trade/wage **fallback** for any recommended worker the batch read omits.
    required_workers:
        The FULL required trade/headcount the recommendation must satisfy.
    current_crew_id:
        EMERGENCY re-composition target crew - exempted by ``no_conflict_assignment``.

    Approach: ``worker_states`` comes entirely from the fresh ``get_workers`` read; the
    fresh ``trade`` / ``desired_daily_wage`` are overlaid on top of the pool-derived
    ``trade_by_worker`` / ``wage_by_worker`` so ``total_cost`` (Property 5) and
    ``trade_headcount`` (Property 4) use server-side truth (see module docstring). A
    recommended worker missing from the fresh read gets no snapshot and thus fails closed.
    """
    from functions.agent_invoke import shared_gateway as db  # high-level adapter over 담당자 A's shared

    member_ids = _dedupe(output_member_ids)
    fresh_records = db.get_workers(member_ids)  # FRESHEST snapshot, just before validation

    fresh_states: Dict[str, WorkerStateSnapshot] = {}
    fresh_trade: Dict[str, str] = {}
    fresh_wage: Dict[str, int] = {}
    for w in fresh_records:
        wid = w.get("worker_id")
        if wid is None:
            continue
        fresh_states[wid] = WorkerStateSnapshot(
            worker_id=wid,
            state=(w.get("state") or _STATE_UNKNOWN),
            current_crew_id=w.get("current_crew_id"),
        )
        if w.get("trade") is not None:
            fresh_trade[wid] = w["trade"]
        if w.get("desired_daily_wage") is not None:
            fresh_wage[wid] = _as_int(w.get("desired_daily_wage"), _DEFAULT_WAGE)

    ctx = ValidationContext.build(
        mode=mode,
        candidates=list(candidates),
        fixed_members=list(fixed_members),
        required_workers=list(required_workers),
        worker_states=fresh_states,
        current_crew_id=current_crew_id,
    )
    # ``build`` seeds trade/wage maps from the (stale) agent-input pools; overlay the
    # freshest get_workers values so the server-side checks use current truth, keeping the
    # pool values only as a fallback for workers the batch read did not return.
    ctx.trade_by_worker.update(fresh_trade)
    ctx.wage_by_worker.update(fresh_wage)
    return ctx
