"""AWS Lambda entry module for the gap_event function (담당자 B).

Matches 담당자 A's packaging convention (``CodeUri: backend/``; Handler
``functions.gap_event.app.lambda_handler``). The implementation lives in ``handler.py`` —
this thin shim only re-exports it under the ``lambda_handler`` name the SAM template
references, so the EventBridge-consumer logic and its tests keep importing
``functions.gap_event.handler`` unchanged.

Trigger: the ``GapEventDetected`` EventBridge event published by 담당자 A's
``company_request`` route (see ``handler.py`` for the full contract).
"""
from __future__ import annotations

from functions.gap_event.handler import handler as lambda_handler

__all__ = ["lambda_handler"]
