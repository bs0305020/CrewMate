"""agent_invoke Lambda (담당자 B).

Responsibilities: request routing (NORMAL / EMERGENCY), role authorization, state
guards (conditional writes), candidate assembly, agent invocation, server-side output
validation (7 checks), persistence of the PROPOSED recommendation, one retry, and the
demo fallback.

Consumes 담당자 A's ``backend/shared/*`` helpers (never implements them) and the shared
Agent contract ``agent.schemas``.
"""
