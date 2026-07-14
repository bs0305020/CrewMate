"""CrewMate Crew Composition Agent package (담당자 B).

Holds the single Crew Composition Agent (NORMAL + EMERGENCY via one Agent) and its
shared I/O contract.

``agent.schemas`` (added in task 1.2) is the canonical Pydantic input/output contract
consumed by BOTH Lambda function packages:

  - backend/functions/agent_invoke
  - backend/functions/gap_event

During local dev/test the workspace root is placed on ``sys.path`` (see the
``pythonpath`` setting in ``pyproject.toml`` and the root ``conftest.py``) so that
``import agent.schemas`` resolves the same module from either function. This
substitutes the AWS Lambda Layer packaging path used in deployment.
"""
