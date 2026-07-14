"""Pure EMERGENCY-payload assembly for the gap_event flow (담당자 B, task 8.4).

When a crew loses a worker mid-job, the EMERGENCY re-composition needs a single
:class:`~agent.schemas.AgentInput` payload with ``mode="EMERGENCY"`` so the Crew
Composition Agent keeps the retained team (``fixed_members``) and fills the shortage
from the READY ``candidates``. This module provides exactly that assembly step as one
**pure function** (:func:`build_emergency_payload`) - no I/O, no DB calls, no worker-state
mutation, and no branching on which caller invoked it.

Design references
-----------------
- ``design.md`` -> "Components and Interfaces" -> "6. Gap Event Lambda"
  (``emergency_payload.py`` signature: ``build_emergency_payload(request, fixed_members,
  candidates, collaboration_pairs) -> AgentInput``).
- ``requirements.md`` -> Requirement 10.6 (build the EMERGENCY payload), 1.3 (EMERGENCY
  keeps ``fixed_members`` and fills the shortage from ``candidates``), 2.1 (the input
  contains ``mode``, ``request``, ``fixed_members``, ``candidates``, ``collaboration_pairs``).

Shared by two callers, identical logic (no caller branching)
------------------------------------------------------------
This assembler is reused by BOTH EMERGENCY entry paths and behaves identically for each
(design.md + tasks.md Notes, "외부 ``agent-recompose``의 payload 서버 조립"):

- the **trusted internal invoke** path - the gap_event handler (task 8.5) assembles the
  payload and hands it to agent_invoke; and
- the **external/direct ``agent-recompose``** path - agent_invoke (task 5.3) does NOT
  trust the client body and re-assembles the payload server-side, reusing task 8.1's
  ``compute_fixed_members`` / ``compute_missing`` and this function.

Keeping it a clean, dependency-light pure function (it imports only the schema types) is
what lets both paths share one code path with no divergence.

Parameter-type contract (decision: accept already-built schema objects, not raw dicts)
--------------------------------------------------------------------------------------
``build_emergency_payload`` accepts **already-constructed schema objects** rather than raw
dicts:

- ``request``            : :class:`~agent.schemas.RequestSpec`
- ``fixed_members``      : ``list[FixedMember]``
- ``candidates``         : ``list[Candidate]``
- ``collaboration_pairs``: ``list[CollaborationPair]``

Rationale: the upstream producers already yield these exact types - ``gap_logic.
compute_fixed_members`` (task 8.1) returns ``list[FixedMember]``, and the candidate/
collaboration assemblers (tasks 5.1 / 8.5) build ``Candidate`` / ``CollaborationPair``
objects. Consuming built objects avoids a redundant dict->model round-trip and lets the
strict :class:`~agent.schemas.AgentInput` model (``strict=True``) type-check every field
at construction time. (Raw dicts would either force lax coercion or fail strict parsing.)

``required_workers`` responsibility (decision: caller passes the FULL requirement)
----------------------------------------------------------------------------------
This function does **not** touch or recompute ``request.required_workers``. The CALLER is
responsible for passing a :class:`~agent.schemas.RequestSpec` whose ``required_workers``
is the **FULL** required trade/headcount for the crew (i.e. the amount that
``fixed_members`` + the shortage-to-fill together cover) - it is intentionally NOT the
``compute_missing`` shortage alone.

This matches how the code validator treats the field: ``validator._check_trade_headcount``
tallies every recommendation's members (``fixed_members`` + new hires) and requires the
combined trades to equal ``ctx.required_workers`` exactly (remaining + filled = full). If
this payload carried only the shortage in ``required_workers``, a valid EMERGENCY
recommendation (which must include the retained fixed members per Req 1.5 / Property 7)
would over-satisfy the requirement and be rejected. ``compute_missing`` (task 8.1) is used
by callers to reason about the shortage for candidate sourcing/logging; it does not define
``required_workers`` on the payload. Keeping that reconstruction in the caller keeps this
function a single-purpose assembler and preserves the design's exact signature.

Purity
------
The function only reads its inputs and returns a freshly constructed ``AgentInput``. It
never mutates the passed objects or their contained models; Pydantic builds new list
containers for the model's fields during validation, so the caller's lists are not
aliased by the returned payload.

Python 3.9 note
---------------
``from __future__ import annotations`` keeps annotations lazy so the builtin-generic
annotation style (``list[...]``) resolves cleanly on the local Python 3.9 runtime. This
mirrors ``agent/schemas.py`` and the sibling ``gap_logic.py``.
"""
from __future__ import annotations

from typing import List

from agent.schemas import (
    AgentInput,
    Candidate,
    CollaborationPair,
    FixedMember,
    RequestSpec,
)

__all__ = ["build_emergency_payload"]

# Fixed mode marker for every payload this module builds (mirrors the shared contract's
# AgentInput.mode literal). Declared as a constant so the single-purpose intent is
# explicit and the value cannot drift.
_MODE_EMERGENCY = "EMERGENCY"


def build_emergency_payload(
    request: RequestSpec,
    fixed_members: List[FixedMember],
    candidates: List[Candidate],
    collaboration_pairs: List[CollaborationPair],
) -> AgentInput:
    """Assemble the ``mode="EMERGENCY"`` :class:`~agent.schemas.AgentInput` payload.

    Pure assembler (Req 10.6 / 1.3 / 2.1): it constructs and returns an ``AgentInput``
    carrying ``mode``, ``request``, ``fixed_members``, ``candidates``, and
    ``collaboration_pairs`` - nothing else. It performs no I/O, no state change, and no
    caller-specific branching, so both EMERGENCY entry paths (trusted internal invoke and
    external ``agent-recompose``) share this one code path.

    Parameters
    ----------
    request:
        The EMERGENCY re-composition :class:`~agent.schemas.RequestSpec`. Its
        ``required_workers`` MUST be the FULL required trade/headcount (retained
        ``fixed_members`` + shortage), NOT the ``compute_missing`` shortage alone - see
        the "``required_workers`` responsibility" section in the module docstring. This
        function does not inspect or recompute it.
    fixed_members:
        The retained RUNNING team members to keep in place (typically the output of
        ``gap_logic.compute_fixed_members``). Included so the Agent preserves them and the
        validator can enforce their preservation (Req 1.3 / 1.5 / Property 7).
    candidates:
        The READY candidate pool the Agent may draw new hires from to fill the shortage.
    collaboration_pairs:
        Prior-collaboration counts among the relevant workers (may be empty when no shared
        history is available).

    Returns
    -------
    AgentInput
        A freshly constructed EMERGENCY payload. Field-level type conformance is enforced
        by the strict ``AgentInput`` model at construction time.
    """
    return AgentInput(
        mode=_MODE_EMERGENCY,
        request=request,
        fixed_members=fixed_members,
        candidates=candidates,
        collaboration_pairs=collaboration_pairs,
    )
