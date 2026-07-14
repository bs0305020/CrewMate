"""Lambda handler for the gap_event function (담당자 B).

This Lambda is the **EventBridge consumer** that reacts to a gap being registered on a
RUNNING crew. Under 담당자 A's decided design the GapEvent is created and published by
``company_request`` (route ``POST /company/crews/{crewId}/gap-events``): that route builds
the GapEvent (``schemas.build_gap_event``, ``status=DETECTED``) via ``put_item`` and THEN
publishes an EventBridge event (``Source="crewmate.company"``,
``DetailType="GapEventDetected"``). This handler is the target of that event: it consumes
the event, RECOMPOSES the crew in EMERGENCY mode by invoking agent_invoke through a trusted
internal invoke, and owns the GapEvent's terminal transition
(``DETECTED → RECOMPOSING → PROPOSED`` / ``FAILED``).

Contract (mirrors company_request → EventBridge → this Lambda)
--------------------------------------------------------------
``company_request._publish_gap_event`` emits ``Detail`` = JSON of exactly these keys::

    {
        "event_id":           "<GapEvent id, already DETECTED>",
        "office_id":          "<office id>",
        "crew_id":            "<affected crew id>",
        "request_id":         "<linked work request id>",
        "gap_type":           "NO_SHOW" | "LEFT_SITE" | "UNAVAILABLE",
        "missing_worker_ids": ["<departed worker id>", ...]
    }

The full EventBridge event wraps that under ``event["detail"]``. This handler reads
``event["detail"]`` (and, ergonomically for direct invoke / tests, treats the event itself
as the detail when it already carries the gap fields at top level).

What this handler does NOT do (differs from an API handler)
-----------------------------------------------------------
- **No Cognito auth.** An EventBridge invocation carries no Cognito principal, so there is
  no ``get_principal`` / ``require_role`` here. Trust is the EventBridge → Lambda wiring
  (담당자 A's infra) plus the IAM boundary on the internal agent_invoke invoke.
- **No GapEvent creation.** The GapEvent already exists as ``DETECTED`` (created by
  ``company_request``); this handler never calls ``save_gap_event``. It only transitions the
  existing event.
- **No API-Gateway request/response shape.** The trigger is not an API Gateway proxy event
  (no ``pathParameters`` / ``body``), and an EventBridge-invoked Lambda's return value is
  not an HTTP response — so this handler returns a plain status dict for logging / testing,
  NOT a ``responses.success/error`` proxy envelope.

End-to-end flow
---------------
1. Parse the EventBridge ``detail`` → ``event_id, office_id, crew_id, request_id, gap_type,
   missing_worker_ids``.
2. Load the affected Crew (``db.get_crew``); a missing/invalid crew (or no linked
   ``request_id``) → return a ``CREW_INVALID`` status WITHOUT locking or invoking (the
   GapEvent stays ``DETECTED``; it cannot be recomposed).
3. Compute the retained team (``compute_fixed_members(active_members, missing_worker_ids)``
   — the EventBridge ``missing_worker_ids`` are the leavers/departed set) and the shortage.
4. Assemble the FULL request spec + office-scoped READY candidates + collaboration via
   ``assemble_normal_input(request_id, office_id)``; narrow candidates to the missing trades;
   ``build_emergency_payload(...)`` → the EMERGENCY :class:`~agent.schemas.AgentInput`.
5. **Acquire the lock BEFORE invoking**: ``transition_gap_event_status(DETECTED →
   RECOMPOSING)``. If it returns ``False`` (not DETECTED — duplicate / already recomposing)
   → log a state conflict and STOP; return a ``CONFLICT`` status. Do NOT invoke.
6. Trusted internal invoke of agent_invoke with the internal EMERGENCY payload carrying this
   ``event_id``. On agent success → ``transition_gap_event_status(RECOMPOSING → PROPOSED)``;
   on agent failure → ``transition_gap_event_status(RECOMPOSING → FAILED)`` (manual-
   composition guidance logged). This handler NEVER changes worker state (담당자 A's
   ``assignment`` owns that, and it recomputes the retained team itself on approval).

Trusted internal invoke — the contract and the IAM trust boundary
-----------------------------------------------------------------
gap_event invokes agent_invoke SYNCHRONOUSLY through the module-level :func:`invoke_agent`
seam: production uses boto3 (``lambda.invoke``, ``RequestResponse``); tests monkeypatch it.
The payload MUST match agent_invoke's ``INTERNAL_INVOKE_MARKER`` + ``_handle_internal``
contract (keys declared locally here, aligned by this docstring)::

    {
        "internal_invoke": true,                  # routing marker (trust is IAM-enforced)
        "mode": "EMERGENCY",
        "event_id": "<GapEvent id>",              # the GapEvent gap_event already locked
        "agent_input": { ...AgentInput.model_dump()... },
        "office_id": "<office id>",
        "current_crew_id": "<crew being recomposed>"
    }

The ``internal_invoke`` marker is only a routing hint — the real trust boundary is IAM
(only gap_event's execution role may invoke agent_invoke directly). agent_invoke's internal
path skips its OFFICE gate and accepts the already-``RECOMPOSING`` GapEvent as its expected
state, and does NOT transition the GapEvent (this handler owns the terminal transition).

Terminal-transition ownership
-----------------------------
On this internal path gap_event owns the GapEvent terminal transition
(``RECOMPOSING → PROPOSED`` / ``FAILED``); agent_invoke does not touch the GapEvent. This
scope stops at ``PROPOSED`` (or ``FAILED``). The ``APPROVED`` / ``FILLED`` transitions, the
replacement workers' assignment, and marking the departed worker ``INACTIVE`` are 담당자 A's
emergency approval API (``POST /office/emergency/{eventId}/approve``) — NOT implemented here.

Observability
-------------
After the internal invoke, gap_event emits its OWN PII-free ``AgentLogRecord`` capturing the
emergency from its vantage point (``agent_mode=EMERGENCY``, ``request_id`` = the GapEvent
``event_id``). agent_invoke separately logs the compose execution it ran (keyed on the work
``request_id``), so the two records are complementary, not double-counted. A logging failure
never alters the flow (it is swallowed).

shared helper consumption
-------------------------
``backend/shared/*`` is 담당자 A's and consumed, never implemented. ``db`` is the high-level
``shared_gateway`` adapter, imported lazily inside functions so it resolves at call time
(the real Layer in deployment, the in-memory fake in tests).

Python 3.9 note
---------------
``from __future__ import annotations`` keeps annotations lazy; ``Optional[...]`` is used for
nullable fields (no PEP 604 unions).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from agent.schemas import AgentInput
from functions.agent_invoke.assembler import assemble_normal_input
from functions.agent_invoke.observability import (
    build_agent_log_record,
    log_agent_execution,
    new_execution_id,
)
from functions.gap_event.emergency_payload import build_emergency_payload
from functions.gap_event.gap_logic import (
    Member,
    compute_fixed_members,
    compute_missing,
)

# Module logger — plain, propagating logger for the state-conflict / crew-invalid diagnostic
# lines and the defensive "structured log emit failed" line. The full per-execution
# structured record is emitted via ``log_agent_execution`` on observability.py's dedicated
# logger, exactly as agent_invoke does.
_logger = logging.getLogger(__name__)

__all__ = [
    "handler",
    "invoke_agent",
]

# --------------------------------------------------------------------------- #
# Modes                                                                        #
# --------------------------------------------------------------------------- #
_MODE_EMERGENCY = "EMERGENCY"

# --------------------------------------------------------------------------- #
# GapEvent status constants — mirror shared.state (GapStatus). Declared #
# locally (like agent_invoke/handler.py) so this module stays importable        #
# standalone; values are fixed by the shared-contract glossary and verified      #
# against tests/mocks/shared_stubs.py.                                          #
# --------------------------------------------------------------------------- #
_GAP_DETECTED = "DETECTED"  # GapStatus.DETECTED (the state company_request created)
_GAP_RECOMPOSING = "RECOMPOSING"  # GapStatus.RECOMPOSING (lock acquired before invoke)
_GAP_PROPOSED = "PROPOSED"  # GapStatus.PROPOSED (terminal transition on save success)
_GAP_FAILED = "FAILED"  # GapStatus.FAILED (terminal transition on retry-exhausted failure)

# Coercion defaults for 담당자 A's Crew-member records (mirror agent_invoke/handler.py's
# non-safety-critical fallbacks). Fixed-member wages here are only Agent hints; the
# validator recomputes total_cost from the freshest get_workers snapshot.
_RUNNING = "RUNNING"
_DEFAULT_TRADE = "GENERAL"
_DEFAULT_WAGE = 1  # smallest positive wage (schema constrains > 0); malformed record only

# --------------------------------------------------------------------------- #
# gap_status values in the returned STATUS DICT (for logging / testing). The     #
# terminal DB states reuse the GapStatus constants above; CONFLICT / CREW_INVALID#
# are handler-local outcomes for a run that stopped before / at the lock.         #
# --------------------------------------------------------------------------- #
_STATUS_CONFLICT = "CONFLICT"
_STATUS_CREW_INVALID = "CREW_INVALID"

# Shared error code (PRD_A_BACKEND.md 1.6) surfaced in the FAILED status dict when the agent
# response carries no explicit error code.
_ERR_AGENT_RETRY_FAILED = "AGENT_RETRY_FAILED"

# --------------------------------------------------------------------------- #
# Internal-invoke payload contract keys — MUST match agent_invoke/handler.py    #
# (INTERNAL_INVOKE_MARKER + _PAYLOAD_* constants). See the module docstring.    #
# --------------------------------------------------------------------------- #
_PAYLOAD_INTERNAL_MARKER = "internal_invoke"
_PAYLOAD_MODE = "mode"
_PAYLOAD_EVENT_ID = "event_id"
_PAYLOAD_AGENT_INPUT = "agent_input"
_PAYLOAD_OFFICE_ID = "office_id"
_PAYLOAD_CURRENT_CREW_ID = "current_crew_id"

# EventBridge detail keys — MUST match company_request._publish_gap_event's Detail schema.
_DETAIL_EVENT_ID = "event_id"
_DETAIL_OFFICE_ID = "office_id"
_DETAIL_CREW_ID = "crew_id"
_DETAIL_REQUEST_ID = "request_id"
_DETAIL_GAP_TYPE = "gap_type"
_DETAIL_MISSING_WORKER_IDS = "missing_worker_ids"

# Target agent_invoke function for the production boto3 invoke (env-overridable).
_AGENT_INVOKE_FUNCTION_ENV = "AGENT_INVOKE_FUNCTION_NAME"
_DEFAULT_AGENT_INVOKE_FUNCTION = "agent_invoke"

# Manual-composition guidance logged when an emergency recomposition fails (Req 10.9).
_MANUAL_GUIDANCE = "자동 긴급 재편성에 실패했습니다. 수동으로 작업조를 편성해 주세요."


# --------------------------------------------------------------------------- #
# EventBridge detail parsing                                                   #
# --------------------------------------------------------------------------- #
def _extract_detail(event: Any) -> Dict[str, Any]:
    """Return the gap detail dict from an EventBridge event.

    An EventBridge event carries the gap fields under ``event["detail"]`` (a dict, or a JSON
    string on some delivery shapes). For ergonomic direct invocation (tests / tooling) an
    event WITHOUT a ``detail`` key that already carries the gap fields at the top level is
    treated as the detail itself.
    """
    if not isinstance(event, dict):
        return {}
    detail = event.get("detail")
    if detail is None:
        # No ``detail`` wrapper: treat the event itself as the detail (direct invoke / test).
        return dict(event)
    if isinstance(detail, str):
        return json.loads(detail) if detail else {}
    if isinstance(detail, dict):
        return dict(detail)
    return {}


# --------------------------------------------------------------------------- #
# Coercion helpers (담당자 A record shapes → strict schemas)                     #
# --------------------------------------------------------------------------- #
def _as_int(value: Any, default: int) -> int:
    """Coerce a possibly-``Decimal`` / ``None`` numeric to ``int`` (``None`` → default)."""
    if value is None:
        return default
    return int(value)


def _to_member(raw: Dict[str, Any]) -> Optional[Member]:
    """Coerce a Crew-member record to a :class:`Member`, or ``None`` when unusable.

    ``worker_id`` is required (a member without an id can be neither retained nor
    excluded); such entries are skipped. Other fields fall back to documented defaults.
    The wage is only an Agent hint — the validator recomputes ``total_cost`` from the
    freshest ``get_workers`` snapshot — so a defaulted wage cannot make an invalid output
    pass. ``state`` is carried for context only; the gap logic never mutates it.
    """
    worker_id = raw.get("worker_id")
    if not worker_id:
        return None
    return Member(
        worker_id=worker_id,
        trade=(raw.get("trade") or _DEFAULT_TRADE),
        desired_daily_wage=_as_int(raw.get("desired_daily_wage"), _DEFAULT_WAGE),
        state=(raw.get("state") or _RUNNING),
    )


def _extract_active_members(crew: Dict[str, Any]) -> List[Member]:
    """Extract active crew members (``active_members`` preferred, then ``members``)."""
    raw_members = crew.get("active_members")
    if raw_members is None:
        raw_members = crew.get("members") or []
    members = [_to_member(m) for m in raw_members]
    return [m for m in members if m is not None]


# --------------------------------------------------------------------------- #
# Trusted internal invoke seam (boto3 in prod; monkeypatched in tests)          #
# --------------------------------------------------------------------------- #
def invoke_agent(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronously invoke agent_invoke with the trusted internal EMERGENCY payload.

    This is the single, injectable SEAM between gap_event and agent_invoke:

    - **Production**: an AWS Lambda ``RequestResponse`` invoke via boto3. The IAM trust
      boundary (only gap_event's execution role may invoke agent_invoke directly) is what
      lets agent_invoke trust this path; the ``internal_invoke`` marker is merely a routing
      hint. The target function name is read from the ``AGENT_INVOKE_FUNCTION_NAME`` env var.
    - **Tests**: monkeypatched to call ``agent_invoke.handler`` directly with ``payload`` (or
      to return a stubbed response), so the flow is exercised without live AWS.

    Returns the parsed agent_invoke response dict (an API-Gateway proxy dict). ``boto3`` is
    imported lazily so importing this module never requires it in the local dev/test env.
    """
    import boto3  # lazy: deploy-time dependency only; tests monkeypatch this function

    client = boto3.client("lambda")
    function_name = os.environ.get(
        _AGENT_INVOKE_FUNCTION_ENV, _DEFAULT_AGENT_INVOKE_FUNCTION
    )
    result = client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    raw = result.get("Payload")
    body = raw.read() if raw is not None else b""
    return json.loads(body) if body else {}


def _parse_agent_response(agent_resp: Any) -> Dict[str, Any]:
    """Parse agent_invoke's API-Gateway proxy response into its ``{success, data, error}`` body.

    agent_invoke returns a proxy dict ``{statusCode, headers, body}`` where ``body`` is the
    JSON-encoded ``{success, data|error}`` envelope. This helper decodes ``body`` on both the
    production boto3 path and the direct-call test seam (which returns the same proxy dict).
    Tolerant fallbacks: an already-flat envelope (``{"success": ...}`` with no ``body``) is
    returned as-is, and an unparseable / empty response degrades to an empty dict (treated as
    a non-success downstream).
    """
    if not isinstance(agent_resp, dict):
        return {}
    body = agent_resp.get("body")
    if body is None:
        # Already a flat envelope (no proxy wrapper) — use it directly.
        return agent_resp if "success" in agent_resp else {}
    if isinstance(body, dict):
        return body
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8")
    if isinstance(body, str):
        if not body:
            return {}
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _to_replacement_recommendations(
    recommendations: List[Dict[str, Any]], fixed_member_ids: List[str]
) -> List[Dict[str, Any]]:
    """Reshape agent_invoke's recommendations into the GapEvent's option-1 storage form.

    agent_invoke returns each recommendation with the FULL ``member_ids`` (retained
    ``fixed_members`` + new hires). Under the option-1 hand-off the GapEvent stores, per
    recommendation, only ``{replacement_member_ids, total_cost, reason}`` where
    ``replacement_member_ids`` is the NEW hires alone (``member_ids`` minus the retained
    ``fixed_member_ids``) — the exact set 담당자 A's ``approve_emergency`` needs to assign.
    Order within each recommendation is preserved.
    """
    fixed_set = set(fixed_member_ids)
    reshaped: List[Dict[str, Any]] = []
    for rec in recommendations:
        member_ids = rec.get("member_ids") or []
        reshaped.append(
            {
                "replacement_member_ids": [m for m in member_ids if m not in fixed_set],
                "total_cost": rec.get("total_cost"),
                "reason": rec.get("reason", ""),
            }
        )
    return reshaped


def _build_internal_payload(
    agent_input: AgentInput,
    event_id: Optional[str],
    office_id: Optional[str],
    crew_id: Optional[str],
) -> Dict[str, Any]:
    """Build the trusted internal-invoke payload matching agent_invoke's contract.

    Serializes the EMERGENCY :class:`AgentInput` with ``model_dump()`` (agent_invoke
    re-parses it with ``AgentInput.model_validate``) and attaches the routing marker, the
    mode, the locked ``event_id``, and the optional Crew-linkage fields. See the module
    docstring for the full contract.
    """
    payload: Dict[str, Any] = {
        _PAYLOAD_INTERNAL_MARKER: True,
        _PAYLOAD_MODE: _MODE_EMERGENCY,
        _PAYLOAD_EVENT_ID: event_id,
        _PAYLOAD_AGENT_INPUT: agent_input.model_dump(),
    }
    if office_id is not None:
        payload[_PAYLOAD_OFFICE_ID] = office_id
    if crew_id is not None:
        payload[_PAYLOAD_CURRENT_CREW_ID] = crew_id
    return payload


# --------------------------------------------------------------------------- #
# Observability — gap_event's own structured execution log                      #
# --------------------------------------------------------------------------- #
def _log_gap_execution(
    agent_input: AgentInput,
    *,
    event_id: Optional[str],
    success: bool,
    recommendations: List[Dict[str, Any]],
    crew_id: Optional[str],
) -> None:
    """Emit gap_event's OWN structured, PII-free execution record (Req 12.1, 12.2).

    The design has BOTH Lambdas write a structured log to CloudWatch. On the trusted
    internal-invoke path, agent_invoke already logged the compose execution it ran; this
    record captures the same emergency from gap_event's vantage point, so the two are
    complementary rather than double-counted:

    - ``agent_mode`` = EMERGENCY — this Lambda only ever drives emergency recomposition.
    - ``request_id`` carries the GapEvent ``event_id`` — gap_event's own correlation key —
      which is what distinguishes this record from agent_invoke's (keyed on the work
      ``request_id``). Both are PII-free string ids.
    - ``input_candidate_count`` = the candidate pool gap_event assembled into the payload.
    - ``recommendation_count`` / ``saved`` / ``crew_id`` / ``validation_passed`` come from
      agent_invoke's response.

    Only counts / ids / flags are logged (never worker names / phones); ``AgentLogRecord``'s
    ``extra="forbid"`` would reject a stray key regardless (Req 12.2). Logging is a pure
    side-effect: any failure building / emitting the record is swallowed after a diagnostic
    line so it can never alter the gap-handling flow's outcome.
    """
    try:
        record = build_agent_log_record(
            agent_execution_id=new_execution_id(),
            agent_mode=_MODE_EMERGENCY,
            request_id=event_id or "",
            input_candidate_count=len(agent_input.candidates),
            recommendation_count=len(recommendations),
            validation_passed=success,
            saved=success,
            crew_id=crew_id,
        )
        log_agent_execution(record)
    except Exception:  # noqa: BLE001 - logging must never break the gap-handling flow
        _logger.exception("failed to emit gap_event structured execution log")


# --------------------------------------------------------------------------- #
# Lambda entry point (EventBridge consumer)                                    #
# --------------------------------------------------------------------------- #
def handler(event: Any, context: Any = None) -> Dict[str, Any]:
    """gap_event Lambda entry point: consume the ``GapEventDetected`` EventBridge event.

    ``event`` is an EventBridge event (the gap fields live under ``event["detail"]``; a bare
    detail dict is also accepted for direct invoke). Returns a PLAIN status dict describing
    the outcome for logging / testing (an EventBridge-triggered Lambda's return value is not
    an HTTP response), one of ``gap_status`` in:

    - ``PROPOSED``     — recomposition succeeded (DETECTED → RECOMPOSING → PROPOSED).
    - ``FAILED``       — the agent failed after retries (RECOMPOSING → FAILED); guidance logged.
    - ``CONFLICT``     — the DETECTED → RECOMPOSING lock failed (duplicate / already recomposing);
                          no invoke, no further transition.
    - ``CREW_INVALID`` — the affected crew is missing / has no linked request; no lock, no invoke.
    """
    from functions.agent_invoke import shared_gateway as db  # high-level adapter

    detail = _extract_detail(event)
    event_id = detail.get(_DETAIL_EVENT_ID)
    office_id = detail.get(_DETAIL_OFFICE_ID)
    crew_id = detail.get(_DETAIL_CREW_ID)
    request_id = detail.get(_DETAIL_REQUEST_ID)
    gap_type = detail.get(_DETAIL_GAP_TYPE)
    missing_worker_ids = list(detail.get(_DETAIL_MISSING_WORKER_IDS) or [])

    # 1. Load the affected Crew (Req 10.2). Missing/invalid or unlinked → CREW_INVALID: the
    #    GapEvent stays DETECTED (it was already created by company_request); we cannot
    #    recompose without a crew + linked work request, so we neither lock nor invoke.
    crew = db.get_crew(crew_id) if crew_id else None
    if crew is None:
        _logger.warning("gap_event crew_invalid: crew not found crew_id=%r", crew_id)
        return {
            "event_id": event_id,
            "gap_status": _STATUS_CREW_INVALID,
            "crew_id": crew_id,
        }
    # ``request_id`` is carried in the EventBridge detail; fall back to the crew's own linkage.
    request_id = request_id or crew.get("request_id")
    if not request_id:
        _logger.warning("gap_event crew_invalid: crew %r has no linked request_id", crew_id)
        return {
            "event_id": event_id,
            "gap_status": _STATUS_CREW_INVALID,
            "crew_id": crew_id,
        }

    # 2. Compute the retained team (active − departed) — the EventBridge missing_worker_ids
    #    is the leavers/departed set (담당자 A's field name). Pure; no worker-state change.
    active_members = _extract_active_members(crew)
    fixed_members = compute_fixed_members(active_members, missing_worker_ids)

    # 3. Assemble the FULL request + office-scoped READY candidates + collaboration pairs,
    #    reusing the same assembler the external agent-recompose route uses (no divergence).
    assembly_office_id = office_id or crew.get("office_id")
    try:
        normal_like = assemble_normal_input(request_id, assembly_office_id)
    except ValueError:
        _logger.warning(
            "gap_event crew_invalid: work request %r for crew %r unavailable",
            request_id,
            crew_id,
        )
        return {
            "event_id": event_id,
            "gap_status": _STATUS_CREW_INVALID,
            "crew_id": crew_id,
        }
    request = normal_like.request

    # 4. Narrow candidates to trades with a positive shortage (fully covered trades need no
    #    new hires); build the mode=EMERGENCY payload (Req 10.6). The request carries the
    #    FULL required_workers (fixed + shortage), as build_emergency_payload requires.
    missing = compute_missing(request.required_workers, fixed_members)
    missing_trades = {tr.trade for tr in missing}
    candidates = [c for c in normal_like.candidates if c.trade in missing_trades]
    agent_input = build_emergency_payload(
        request, fixed_members, candidates, normal_like.collaboration_pairs
    )

    # 5. Acquire the lock BEFORE invoking: DETECTED → RECOMPOSING (Req 10.6). A failed
    #    conditional transition (not DETECTED — duplicate / already recomposing) → CONFLICT.
    #    Pre-locking is what lets agent_invoke's internal path accept the already-RECOMPOSING
    #    GapEvent as its expected state.
    if not db.transition_gap_event_status(event_id, _GAP_DETECTED, _GAP_RECOMPOSING):
        _logger.warning(
            "gap_event state_conflict: gap %r not in %s (already recomposing?)",
            event_id,
            _GAP_DETECTED,
        )
        return {"event_id": event_id, "gap_status": _STATUS_CONFLICT}

    # 6. Trusted internal invoke of agent_invoke (synchronous) with the EMERGENCY payload.
    payload = _build_internal_payload(
        agent_input, event_id, assembly_office_id, crew_id
    )
    agent_resp = invoke_agent(payload)

    # 6a. agent_invoke returns an API-Gateway PROXY response ({statusCode, headers, body});
    #     the real success/data envelope is the JSON-encoded ``body``. Parse it so both invoke
    #     paths work identically (production boto3 invoke and the direct-call test seam).
    payload_body = _parse_agent_response(agent_resp)
    success = payload_body.get("success") is True
    agent_data = payload_body.get("data") or {}

    # 6b. Option-1 hand-off reshape: EMERGENCY creates NO Crew. Compute the retained team ids
    #     and reshape the Agent's recommendations to {replacement_member_ids, total_cost,
    #     reason} for storage on the GapEvent (담당자 A's approve_emergency reads these).
    fixed_member_ids = [f.worker_id for f in fixed_members]
    persisted_recommendations = (
        _to_replacement_recommendations(
            agent_data.get("recommendations", []), fixed_member_ids
        )
        if success
        else []
    )

    # 6c. Emit gap_event's OWN structured execution record — ONCE per gap handling, from
    #     gap_event's vantage point (agent_invoke logged the compose execution separately).
    #     Never touches the DB, so it does not affect the transition order.
    _log_gap_execution(
        agent_input,
        event_id=event_id,
        success=success,
        recommendations=persisted_recommendations,
        crew_id=None,  # option-1: EMERGENCY creates no Crew
    )

    # 7. Own the terminal transition on this internal path (agent_invoke does not touch the
    #    GapEvent). On success, record fixed_member_ids + recommendations onto the GapEvent
    #    AND transition RECOMPOSING → PROPOSED in ONE conditional write. Remaining team
    #    members keep RUNNING — no worker-state change.
    if success:
        db.record_gap_recommendations(
            event_id,
            fixed_member_ids=fixed_member_ids,
            recommendations=persisted_recommendations,
            expected=_GAP_RECOMPOSING,
            target=_GAP_PROPOSED,
        )
        return {
            "event_id": event_id,
            "gap_status": _GAP_PROPOSED,
            "mode": _MODE_EMERGENCY,
            "fixed_member_ids": fixed_member_ids,
            "recommendations": persisted_recommendations,
        }

    # Recomposition failed (retry exhausted): FAILED + manual-composition guidance (Req 10.9).
    db.transition_gap_event_status(event_id, _GAP_RECOMPOSING, _GAP_FAILED)
    err = payload_body.get("error") or {}
    code = err.get("code") or _ERR_AGENT_RETRY_FAILED
    detail_msg = err.get("message") or "emergency recomposition failed"
    _logger.warning("gap_event recomposition failed event_id=%r: %s | %s",
                    event_id, detail_msg, _MANUAL_GUIDANCE)
    return {
        "event_id": event_id,
        "gap_status": _GAP_FAILED,
        "mode": _MODE_EMERGENCY,
        "error": {"code": code, "message": f"{detail_msg} | {_MANUAL_GUIDANCE}"},
    }
