"""In-memory test doubles for 담당자 A's ``backend/shared/*`` helpers.

담당자 B *consumes* (never implements) the shared helpers:

  - ``db``       : DynamoDB access helpers
  - ``auth``     : Cognito role / claim verification
  - ``state``    : status enum constants
  - ``response`` : ``{success, ...}`` response formatters

These stubs mirror the *interfaces* named in ``design.md`` ("소비하는 shared 계약")
and the shared contract in ``PRD_A_BACKEND.md`` so later tasks can drive the Lambda
handlers and the pure functions deterministically - without touching DynamoDB, Cognito,
or Bedrock. The real implementations remain 담당자 A's.

Two consumption styles are supported so later tasks can pick either without rework:

  1. Dependency injection - pass ``FakeSharedDB`` / ``StubAuth`` / ``state`` / ``response``
     (or a whole :class:`SharedStubs`) into the code under test.
  2. Module substitution - :func:`install_shared_stubs` registers the stubs under
     ``shared.*`` in ``sys.modules`` via pytest's ``monkeypatch``.

Nothing in this module creates or imports ``shared`` on disk.
"""
from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional


# --------------------------------------------------------------------------- #
# State enum constants (mirror shared contract 1.2 / 1.3)                      #
# --------------------------------------------------------------------------- #
class WorkerState:
    INACTIVE = "INACTIVE"
    READY = "READY"
    RESERVED = "RESERVED"
    RUNNING = "RUNNING"


class RequestStatus:
    REQUESTED = "REQUESTED"
    COMPOSING = "COMPOSING"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class CrewStatus:
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class GapStatus:
    DETECTED = "DETECTED"
    RECOMPOSING = "RECOMPOSING"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    FILLED = "FILLED"
    FAILED = "FAILED"


class GapType:
    NO_SHOW = "NO_SHOW"
    LEFT_SITE = "LEFT_SITE"
    UNAVAILABLE = "UNAVAILABLE"


class Role:
    WORKER = "WORKER"
    OFFICE = "OFFICE"
    COMPANY = "COMPANY"


class Source:
    AGENT = "AGENT"
    MANUAL = "MANUAL"


class ErrorCode:
    """Error codes from the shared contract (PRD_A_BACKEND.md 1.6 / design.md)."""

    AGENT_OUTPUT_INVALID = "AGENT_OUTPUT_INVALID"
    AGENT_RETRY_FAILED = "AGENT_RETRY_FAILED"
    GAP_EVENT_NOT_FOUND = "GAP_EVENT_NOT_FOUND"
    STATE_CONFLICT = "STATE_CONFLICT"
    CREW_INVALID = "CREW_INVALID"
    FORBIDDEN = "FORBIDDEN"
    UNAUTHORIZED = "UNAUTHORIZED"


def _build_state_namespace() -> SimpleNamespace:
    """Aggregate constants so tests can consume them as ``state.READY`` etc."""
    return SimpleNamespace(
        # worker states (flat access, most common in the agent scope)
        INACTIVE=WorkerState.INACTIVE,
        READY=WorkerState.READY,
        RESERVED=WorkerState.RESERVED,
        RUNNING=WorkerState.RUNNING,
        # grouped accessors
        WorkerState=WorkerState,
        RequestStatus=RequestStatus,
        CrewStatus=CrewStatus,
        GapStatus=GapStatus,
        GapType=GapType,
        Role=Role,
        Source=Source,
        ErrorCode=ErrorCode,
    )


# --------------------------------------------------------------------------- #
# response formatter (mirror shared contract 1.6)                             #
# --------------------------------------------------------------------------- #
def ok(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``{"success": true, "data": {...}}``."""
    return {"success": True, "data": data if data is not None else {}}


def error(code: str, message: str = "") -> Dict[str, Any]:
    """``{"success": false, "error": {"code": ..., "message": ...}}``."""
    return {"success": False, "error": {"code": code, "message": message}}


def _build_response_namespace() -> SimpleNamespace:
    return SimpleNamespace(ok=ok, error=error)


# --------------------------------------------------------------------------- #
# auth stub (mirror auth.require_role(event, roles))                          #
# --------------------------------------------------------------------------- #
class ForbiddenError(Exception):
    """Raised by the auth stub when the caller's role is not permitted.

    Carries ``code``/``message`` so handlers can map it onto a FORBIDDEN response.
    """

    def __init__(self, message: str = "forbidden", code: str = ErrorCode.FORBIDDEN):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class StubAuth:
    """Configurable stand-in for 담당자 A's ``auth`` helper.

    Set ``role`` / ``office_id`` / ``company_id`` to model the authenticated caller.
    ``require_role`` returns the caller identity when allowed, and raises
    :class:`ForbiddenError` otherwise.
    """

    role: str = Role.OFFICE
    office_id: Optional[str] = "OFFICE001"
    company_id: Optional[str] = None
    user_id: str = "user-stub"
    calls: List[dict] = field(default_factory=list)

    def identity(self) -> dict:
        return {
            "role": self.role,
            "office_id": self.office_id,
            "company_id": self.company_id,
            "user_id": self.user_id,
        }

    def require_role(self, event: Any, roles: Iterable[str]) -> dict:
        allowed = {roles} if isinstance(roles, str) else set(roles)
        self.calls.append({"event": event, "roles": sorted(allowed), "role": self.role})
        if self.role not in allowed:
            raise ForbiddenError(f"role {self.role!r} not in {sorted(allowed)!r}")
        return self.identity()


# --------------------------------------------------------------------------- #
# db stub (mirror the design.md "소비하는 shared 계약" table)                   #
# --------------------------------------------------------------------------- #
class FakeSharedDB:
    """In-memory fake of 담당자 A's ``db`` helper.

    Storage is plain dicts keyed by id. Status transitions use conditional-write
    semantics (they only succeed when the current status equals ``expected``), matching
    the DynamoDB ``ConditionExpression`` approach in the design - a failed transition is
    how ``STATE_CONFLICT`` arises. Every call is recorded so tests can assert on side
    effects (for example, "save_crew was never called for an invalid output").
    """

    def __init__(self) -> None:
        self.work_requests: Dict[str, dict] = {}
        self.workers: Dict[str, dict] = {}
        self.crews: Dict[str, dict] = {}
        self.gap_events: Dict[str, dict] = {}
        self.collaborations: List[dict] = []
        # side-effect recorders
        self.calls: List[dict] = []
        self.saved_crews: List[dict] = []
        self.saved_gap_events: List[dict] = []
        self.status_transitions: List[dict] = []      # WorkRequest transitions
        self.gap_status_transitions: List[dict] = []   # GapEvent transitions

    # --- seeding helpers (test setup only) --------------------------------- #
    def add_worker(
        self,
        worker_id: str,
        *,
        office_id: str = "OFFICE001",
        state: str = WorkerState.READY,
        trade: str = "GENERAL",
        desired_daily_wage: int = 150000,
        current_crew_id: Optional[str] = None,
        **extra: Any,
    ) -> dict:
        item = {
            "worker_id": worker_id,
            "office_id": office_id,
            "state": state,
            "trade": trade,
            "desired_daily_wage": desired_daily_wage,
            "current_crew_id": current_crew_id,
        }
        item.update(extra)
        self.workers[worker_id] = item
        return item

    def add_work_request(
        self,
        request_id: str,
        *,
        status: str = RequestStatus.REQUESTED,
        office_id: str = "OFFICE001",
        **extra: Any,
    ) -> dict:
        item = {"request_id": request_id, "status": status, "office_id": office_id}
        item.update(extra)
        self.work_requests[request_id] = item
        return item

    def add_crew(self, crew_id: str, **extra: Any) -> dict:
        item = {"crew_id": crew_id}
        item.update(extra)
        self.crews[crew_id] = item
        return item

    def add_gap_event(
        self,
        event_id: str,
        *,
        status: str = GapStatus.DETECTED,
        **extra: Any,
    ) -> dict:
        item = {"event_id": event_id, "status": status}
        item.update(extra)
        self.gap_events[event_id] = item
        return item

    def add_collaboration(self, worker_a: str, worker_b: str, count: int = 1) -> None:
        self.collaborations.append(
            {"worker_a": worker_a, "worker_b": worker_b, "count": count}
        )

    # --- read helpers ------------------------------------------------------ #
    def get_work_request(self, request_id: str) -> Optional[dict]:
        self.calls.append({"method": "get_work_request", "request_id": request_id})
        item = self.work_requests.get(request_id)
        return copy.deepcopy(item) if item is not None else None

    def query_ready_workers(
        self, office_id: str, trades: Optional[Iterable[str]] = None
    ) -> List[dict]:
        self.calls.append(
            {
                "method": "query_ready_workers",
                "office_id": office_id,
                "trades": list(trades) if trades is not None else None,
            }
        )
        trade_set = set(trades) if trades is not None else None
        out: List[dict] = []
        for worker in self.workers.values():
            if worker.get("office_id") != office_id:
                continue
            if worker.get("state") != WorkerState.READY:
                continue
            if trade_set is not None and worker.get("trade") not in trade_set:
                continue
            out.append(copy.deepcopy(worker))
        return out

    def get_workers(self, worker_ids: Iterable[str]) -> List[dict]:
        ids = list(worker_ids)
        self.calls.append({"method": "get_workers", "worker_ids": ids})
        return [copy.deepcopy(self.workers[wid]) for wid in ids if wid in self.workers]

    def get_worker_collaborations(self, worker_ids: Iterable[str]) -> List[dict]:
        ids = set(worker_ids)
        self.calls.append(
            {"method": "get_worker_collaborations", "worker_ids": sorted(ids)}
        )
        return [
            copy.deepcopy(pair)
            for pair in self.collaborations
            if pair["worker_a"] in ids and pair["worker_b"] in ids
        ]

    def get_crew(self, crew_id: str) -> Optional[dict]:
        self.calls.append({"method": "get_crew", "crew_id": crew_id})
        item = self.crews.get(crew_id)
        return copy.deepcopy(item) if item is not None else None

    def get_gap_event(self, event_id: str) -> Optional[dict]:
        """Read a GapEvent by id (``None`` when absent).

        Consumption point required by the external/direct ``agent-recompose`` route
        (task 5.3): agent_invoke looks the GapEvent up by ``eventId`` to (a) return
        ``GAP_EVENT_NOT_FOUND`` when it is absent (Req 10.10) and (b) source the
        affected ``crew_id`` / ``departed_ids`` / ``type`` for the server-side EMERGENCY
        payload assembly (it does NOT trust the client body). The design's
        "소비하는 shared 계약" table pre-dates that external-route decision (tasks.md
        Notes), so this read is an additive consumption point to confirm with 담당자 A;
        it mirrors the read-only, deep-copy semantics of the other getters.
        """
        self.calls.append({"method": "get_gap_event", "event_id": event_id})
        item = self.gap_events.get(event_id)
        return copy.deepcopy(item) if item is not None else None

    # --- write / transition helpers ---------------------------------------- #
    def save_crew(
        self,
        *,
        office_id: Optional[str] = None,
        request_id: Optional[str] = None,
        member_ids: Optional[Iterable[str]] = None,
        rationale: str = "",
        estimated_cost: Optional[int] = None,
        source: str = Source.AGENT,
        status: Optional[str] = None,
        crew_id: Optional[str] = None,
    ) -> str:
        """Persist a Crew via the high-level kwargs contract (mirrors ``shared_gateway.save_crew``).

        Builds the canonical Crew shape ``schemas.build_crew`` produces (``member_ids`` /
        ``status`` / ``source`` / ``rationale`` / ``estimated_cost`` + office/request linkage);
        defaults ``status`` to ``PROPOSED`` exactly as the adapter does. There is intentionally
        no ``gap_event_id`` / ``current_crew_id`` / ``rank`` / ``total_cost`` field — the
        canonical schema has none (the linkage lives only in the agent_invoke response).
        """
        cid = crew_id or f"CREW#{len(self.crews) + 1}"
        item = {
            "crew_id": cid,
            "office_id": office_id,
            "request_id": request_id,
            "member_ids": list(member_ids or []),
            "status": status or CrewStatus.PROPOSED,
            "source": source,
            "rationale": rationale,
            "estimated_cost": estimated_cost,
        }
        self.crews[cid] = item
        self.saved_crews.append(item)
        self.calls.append({"method": "save_crew", "crew_id": cid})
        return cid

    def transition_request_status(
        self, request_id: str, expected: str, target: str
    ) -> bool:
        req = self.work_requests.get(request_id)
        succeeded = req is not None and req.get("status") == expected
        record = {
            "method": "transition_request_status",
            "request_id": request_id,
            "expected": expected,
            "target": target,
            "ok": succeeded,
        }
        self.calls.append(record)
        self.status_transitions.append(record)
        if succeeded:
            req["status"] = target
        return succeeded

    def save_gap_event(
        self,
        *,
        office_id: Optional[str] = None,
        crew_id: Optional[str] = None,
        request_id: Optional[str] = None,
        gap_type: Optional[str] = None,
        missing_worker_ids: Optional[Iterable[str]] = None,
        status: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> str:
        """Persist a GapEvent via the high-level kwargs contract (mirrors ``shared_gateway``).

        Builds the canonical GapEvent shape ``schemas.build_gap_event`` produces: the real
        schema names the gap kind ``gap_type`` (not ``type``) and the departed workers
        ``missing_worker_ids`` (not ``departed_ids``). Defaults ``status`` to ``DETECTED``.
        """
        eid = event_id or f"GAP#{len(self.gap_events) + 1}"
        item = {
            "event_id": eid,
            "office_id": office_id,
            "crew_id": crew_id,
            "request_id": request_id,
            "gap_type": gap_type,
            "missing_worker_ids": list(missing_worker_ids or []),
            "status": status or GapStatus.DETECTED,
        }
        self.gap_events[eid] = item
        self.saved_gap_events.append(item)
        self.calls.append({"method": "save_gap_event", "event_id": eid})
        return eid

    def transition_gap_event_status(
        self, event_id: str, expected: str, target: str
    ) -> bool:
        event = self.gap_events.get(event_id)
        succeeded = event is not None and event.get("status") == expected
        record = {
            "method": "transition_gap_event_status",
            "event_id": event_id,
            "expected": expected,
            "target": target,
            "ok": succeeded,
        }
        self.calls.append(record)
        self.gap_status_transitions.append(record)
        if succeeded:
            event["status"] = target
        return succeeded

    def record_gap_recommendations(
        self,
        event_id: str,
        *,
        fixed_member_ids: Optional[Iterable[str]] = None,
        recommendations: Optional[List[dict]] = None,
        expected: Optional[str] = None,
        target: Optional[str] = None,
    ) -> bool:
        """Conditional transition + EMERGENCY recommendation write (mirrors ``shared_gateway``).

        Option-1 emergency hand-off: on a successful ``expected → target`` conditional
        transition, also stamp ``fixed_member_ids`` + ``recommendations`` onto the GapEvent
        item. Recorded into ``gap_status_transitions`` (like a plain transition) so
        transition-sequence assertions still see the ``RECOMPOSING → PROPOSED`` step, and the
        written fields land on ``gap_events[event_id]`` for direct assertions. A failed
        conditional write leaves both status and recommendations untouched.
        """
        event = self.gap_events.get(event_id)
        succeeded = event is not None and event.get("status") == expected
        record = {
            "method": "record_gap_recommendations",
            "event_id": event_id,
            "expected": expected,
            "target": target,
            "ok": succeeded,
            "fixed_member_ids": list(fixed_member_ids or []),
            "recommendations": list(recommendations or []),
        }
        self.calls.append(record)
        self.gap_status_transitions.append(record)
        if succeeded:
            event["status"] = target
            event["fixed_member_ids"] = list(fixed_member_ids or [])
            event["recommendations"] = list(recommendations or [])
        return succeeded

    # --- assertion conveniences -------------------------------------------- #
    def method_calls(self, name: str) -> List[dict]:
        """All recorded calls to ``name`` (handy for side-effect assertions)."""
        return [c for c in self.calls if c.get("method") == name]


# --------------------------------------------------------------------------- #
# Aggregate factory + optional sys.modules installer                          #
# --------------------------------------------------------------------------- #
@dataclass
class SharedStubs:
    db: FakeSharedDB
    auth: StubAuth
    state: SimpleNamespace
    response: SimpleNamespace


def build_shared_stubs(
    *,
    role: str = Role.OFFICE,
    office_id: Optional[str] = "OFFICE001",
    company_id: Optional[str] = None,
) -> SharedStubs:
    """Return a fresh, independent set of shared stubs for a single test."""
    return SharedStubs(
        db=FakeSharedDB(),
        auth=StubAuth(role=role, office_id=office_id, company_id=company_id),
        state=_build_state_namespace(),
        response=_build_response_namespace(),
    )


_DB_CONTRACT_METHODS = (
    "get_work_request",
    "query_ready_workers",
    "get_workers",
    "get_worker_collaborations",
    "get_crew",
    "get_gap_event",
    "save_crew",
    "transition_request_status",
    "save_gap_event",
    "transition_gap_event_status",
    "record_gap_recommendations",
)


def install_shared_stubs(monkeypatch, stubs: Optional[SharedStubs] = None) -> SharedStubs:
    """Register the stubs under ``shared.*`` in ``sys.modules``.

    Optional convenience for later handler tests that import shared helpers with
    ``from shared import db, auth, state, response``. Uses pytest's
    ``monkeypatch`` so the substituted modules are removed automatically at teardown.
    담당자 A's real package is never created on disk by this helper.

    The db module exposes the contract functions bound to a single :class:`FakeSharedDB`
    instance, also reachable as ``shared.db.instance`` for seeding / assertions.
    """
    stubs = stubs or build_shared_stubs()

    shared_pkg = ModuleType("shared")
    shared_pkg.__path__ = []  # mark as a (namespace-less) package

    db_mod = ModuleType("shared.db")
    for name in _DB_CONTRACT_METHODS:
        setattr(db_mod, name, getattr(stubs.db, name))
    db_mod.instance = stubs.db  # type: ignore[attr-defined]

    auth_mod = ModuleType("shared.auth")
    auth_mod.require_role = stubs.auth.require_role  # type: ignore[attr-defined]
    auth_mod.ForbiddenError = ForbiddenError  # type: ignore[attr-defined]
    auth_mod.instance = stubs.auth  # type: ignore[attr-defined]

    state_mod = ModuleType("shared.state")
    for name in vars(stubs.state):
        setattr(state_mod, name, getattr(stubs.state, name))

    response_mod = ModuleType("shared.response")
    response_mod.ok = stubs.response.ok  # type: ignore[attr-defined]
    response_mod.error = stubs.response.error  # type: ignore[attr-defined]

    # Attach submodules as attributes so ``from shared import db`` works
    # by direct attribute lookup, in addition to the sys.modules registration.
    shared_pkg.db = db_mod  # type: ignore[attr-defined]
    shared_pkg.auth = auth_mod  # type: ignore[attr-defined]
    shared_pkg.state = state_mod  # type: ignore[attr-defined]
    shared_pkg.response = response_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "shared", shared_pkg)
    monkeypatch.setitem(sys.modules, "shared.db", db_mod)
    monkeypatch.setitem(sys.modules, "shared.auth", auth_mod)
    monkeypatch.setitem(sys.modules, "shared.state", state_mod)
    monkeypatch.setitem(sys.modules, "shared.response", response_mod)
    return stubs


def install_fake_db(monkeypatch, fake: Optional[FakeSharedDB] = None) -> FakeSharedDB:
    """Redirect 담당자 B's high-level DB contract onto an in-memory :class:`FakeSharedDB`.

    담당자 B's code now consumes the real ``backend/shared/*`` low-level API through the
    ADAPTER ``functions.agent_invoke.shared_gateway`` (imported at call sites as
    ``db``). This helper monkeypatches the adapter's ten high-level module-level functions
    (``get_work_request`` … ``transition_gap_event_status``) onto ``fake``'s methods, so the
    handler / flow / assembler / gap tests exercise 담당자 B's real logic against the
    in-memory fake with the SAME high-level contract — WITHOUT shadowing the real
    ``shared`` package (so ``shared.auth`` / ``responses`` stay REAL, driven
    by claim-bearing events).

    Returns the (new or supplied) ``FakeSharedDB`` for seeding and side-effect assertions
    (``saved_crews`` / ``saved_gap_events`` / ``status_transitions`` / ``gap_status_transitions``).
    Uses pytest's ``monkeypatch`` so the adapter functions are restored automatically at
    teardown.
    """
    from functions.agent_invoke import shared_gateway

    fake = fake or FakeSharedDB()
    for name in _DB_CONTRACT_METHODS:
        monkeypatch.setattr(shared_gateway, name, getattr(fake, name))
    return fake
