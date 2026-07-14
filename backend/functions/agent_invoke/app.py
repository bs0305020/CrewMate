"""AWS Lambda entry module for the agent_invoke function (담당자 B).

Matches 담당자 A's packaging convention (``CodeUri: backend/``; Handler
``functions.agent_invoke.app.lambda_handler``). The implementation lives in
``handler.py`` — this thin shim only re-exports it under the ``lambda_handler`` name the
SAM template references, so the rich routing / compose-flow logic and its tests keep
importing ``functions.agent_invoke.handler`` unchanged.

Triggers (see ``handler.py`` for the full contract):
- EventBridge ``ComposeRequested`` -> NORMAL composition.
- gap_event's trusted internal invoke -> EMERGENCY recomposition.
"""
from __future__ import annotations

from functions.agent_invoke.handler import handler as lambda_handler

__all__ = ["lambda_handler"]
