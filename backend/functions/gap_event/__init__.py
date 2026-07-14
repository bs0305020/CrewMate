"""gap_event Lambda (담당자 B).

Responsibilities: gap-event capture (status DETECTED), affected-crew lookup,
fixed_members / shortage computation (pure functions), the DETECTED -> RECOMPOSING lock,
EMERGENCY payload assembly, the trusted internal invoke of agent_invoke, and GapEvent
terminal transitions (RECOMPOSING -> PROPOSED / FAILED) for the internal-invoke path.

Scope stops at PROPOSED; APPROVED / FILLED transitions and worker state assignment are
담당자 A's emergency-approval API.
"""
