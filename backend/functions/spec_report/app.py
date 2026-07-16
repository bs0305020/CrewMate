"""SpecReportAgentFunction Lambda entry point."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from pydantic import ValidationError

from ncs_collector.models import ApplicantSpecInput
from ncs_collector.trade_requirements import TradeNotFoundError
from spec_report.aws_rules import S3RuleRepository
from spec_report.orchestrator import SpecReportService
from spec_report.qnet import DynamoQualificationCache, QNetHttpAdapter, QNetQualificationService
from spec_report.report_agent import ReportAgentRunner
from spec_report.retrieval import BedrockKnowledgeBaseRetriever
from spec_report.storage import S3ReportStorage

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_SERVICE: SpecReportService | None = None


def _response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": os.environ.get("CORS_ALLOW_ORIGIN", "*"),
        },
        "body": json.dumps(payload, ensure_ascii=False),
    }


def build_service() -> SpecReportService:
    repository = S3RuleRepository()
    retriever = BedrockKnowledgeBaseRetriever()
    qnet = QNetQualificationService(
        QNetHttpAdapter(
            timeout=float(os.environ.get("QNET_TIMEOUT_SECONDS", "5")),
            retries=int(os.environ.get("QNET_MAX_RETRIES", "1")),
            min_interval=float(os.environ.get("QNET_MIN_INTERVAL_SECONDS", "0.5")),
        ),
        DynamoQualificationCache(),
    )
    return SpecReportService(
        repository,
        retriever,
        qnet,
        agent_runner=ReportAgentRunner(retriever, qnet),
        storage=S3ReportStorage(),
    )


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    global _SERVICE
    try:
        raw_body = event.get("body") if isinstance(event, dict) else event
        if isinstance(raw_body, str):
            payload = json.loads(raw_body)
        elif isinstance(raw_body, dict):
            payload = raw_body
        elif isinstance(event, dict) and "targetTrade" in event:
            payload = event
        else:
            raise ValueError("request body is required")
        applicant = ApplicantSpecInput.model_validate(payload)
    except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        logger.info("spec_report_rejected error_code=INVALID_INPUT")
        return _response(400, {"error": {"code": "INVALID_INPUT", "message": str(exc)}})

    try:
        if _SERVICE is None:
            _SERVICE = build_service()
        report, markdown, stored = _SERVICE.generate(applicant)
        result: dict[str, Any] = {
            "report": report.model_dump(mode="json", by_alias=True),
            "persisted": bool(stored),
        }
        if markdown is not None:
            result["markdown"] = markdown
        if stored:
            result["storage"] = stored
        return _response(200, result)
    except TradeNotFoundError:
        logger.info("spec_report_failed error_code=TRADE_NOT_FOUND target_trade=%s", applicant.target_trade)
        return _response(404, {"error": {"code": "TRADE_NOT_FOUND", "message": "구조화 규칙에서 직종을 찾을 수 없습니다."}})
    except Exception as exc:  # deployment/config/storage errors; no sensitive payload logging
        logger.exception("spec_report_failed error_code=REPORT_GENERATION_FAILED exception=%s", type(exc).__name__)
        return _response(500, {"error": {"code": "REPORT_GENERATION_FAILED", "message": "보고서 생성 중 오류가 발생했습니다."}})
