"""Single Strands Report Agent with exactly two read-only evidence tools."""

from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
from typing import Any

from ncs_collector.models import (
    AgentReportDraft,
    QualificationEvidence,
    RequirementEvidenceResult,
    StructuredGapAnalysis,
)
from ncs_collector.text import comparison_key, normalize_text
from spec_report.qnet import QNetQualificationService
from spec_report.retrieval import RequirementRetriever

try:
    from strands import Agent, tool
    from strands.models import BedrockModel

    STRANDS_AVAILABLE = True
except Exception:  # import-safe for local deterministic mode/tests
    Agent = None  # type: ignore[assignment]
    BedrockModel = None  # type: ignore[assignment]
    tool = None  # type: ignore[assignment]
    STRANDS_AVAILABLE = False

REPORT_TOOL_NAMES = ("retrieve_requirement_evidence", "fetch_qnet_qualification")
SYSTEM_PROMPT_PATH = Path(__file__).with_name("report_system_prompt.md")


class ReportAgentUnavailable(RuntimeError):
    pass


def load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _extract_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    message = getattr(result, "message", None)
    if isinstance(message, dict):
        return "".join(
            block.get("text", "")
            for block in message.get("content", [])
            if isinstance(block, dict)
        )
    return str(result or "")


def _extract_json_payload(result: Any) -> str:
    """Accept a single JSON object, optionally wrapped in one JSON code fence."""
    value = _extract_text(result).strip()
    if value.startswith("```json"):
        value = value[len("```json"):].strip()
        if value.endswith("```"):
            value = value[:-3].strip()
    return value


def _run_bounded_batch(
    requests: list[dict[str, Any]],
    operation: Any,
    *,
    max_workers: int,
) -> list[dict[str, Any]]:
    if not requests:
        return []
    workers = max(1, min(max_workers, len(requests)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(operation, requests))


def _compact_kb_result(result: RequirementEvidenceResult) -> dict[str, Any]:
    """Keep full evidence in Lambda memory while minimizing model context tokens."""
    compact_evidence = []
    for evidence in result.evidence[:2]:
        metadata = evidence.metadata or {}
        compact_evidence.append({
            "documentId": evidence.document_id,
            "sourceLocation": evidence.source_location,
            "score": evidence.score,
            "excerpt": evidence.text[:240],
            "metadata": {
                key: metadata[key]
                for key in (
                    "document_id",
                    "document_type",
                    "trade",
                    "certification_group",
                    "certification_name",
                    "ncs_code",
                    "review_status",
                )
                if metadata.get(key) not in (None, "")
            },
        })
    return {
        "status": result.status,
        "error": result.error,
        "evidence": compact_evidence,
    }


def _compact_qnet_result(result: QualificationEvidence) -> dict[str, Any]:
    return {
        "normalizedName": result.normalized_name,
        "officialName": result.official_name,
        "status": result.status,
        "sourceUrl": result.source_url,
        "checkedAt": result.checked_at,
        "fetchStatus": result.fetch_status,
        "fromCache": result.from_cache,
        "error": result.error,
        "confirmedFields": [
            field
            for field in (
                "issuingOrganization",
                "duties",
                "eligibility",
                "acquisitionMethod",
                "examSchedule",
                "fees",
            )
            if getattr(result, {
                "issuingOrganization": "issuing_organization",
                "duties": "duties",
                "eligibility": "eligibility",
                "acquisitionMethod": "acquisition_method",
                "examSchedule": "exam_schedule",
                "fees": "fees",
            }[field])
        ],
    }


class PlanBoundRetriever:
    """Allow the model to execute only KB calls already authorized by the evidence plan."""

    def __init__(self, delegate: RequirementRetriever, evidence_plan: list[dict[str, Any]]):
        self.delegate = delegate
        self.plan = [item for item in evidence_plan if item.get("action") == "KB"]
        self.results: dict[str, RequirementEvidenceResult] = {}

    def retrieve_requirement_evidence(
        self,
        target_trade: str,
        query: str,
        item_type: str | None = None,
        item_name: str | None = None,
        ncs_code: str | None = None,
        document_types: list[str] | None = None,
    ) -> RequirementEvidenceResult:
        expected = next(
            (
                item for item in self.plan
                if comparison_key(item.get("itemName")) == comparison_key(item_name)
            ),
            None,
        )
        if expected is None:
            raise PermissionError("KB tool call is not present in the evidence plan")
        expected_types = sorted(expected.get("documentTypes") or [])
        actual_types = sorted(document_types or [])
        if (
            comparison_key(target_trade) != comparison_key(expected.get("targetTrade") or target_trade)
            or normalize_text(query) != normalize_text(expected.get("query"))
            or comparison_key(item_type) != comparison_key(expected.get("itemType"))
            or comparison_key(ncs_code) != comparison_key(expected.get("ncsCode"))
            or actual_types != expected_types
        ):
            raise PermissionError("KB tool call parameters differ from the evidence plan")
        result = self.delegate.retrieve_requirement_evidence(
            target_trade=target_trade,
            query=query,
            item_type=item_type,
            item_name=item_name,
            ncs_code=ncs_code,
            document_types=document_types,
        )
        self.results[expected["itemName"]] = result
        return result


class PlanBoundQNetService:
    """Prevent invented qualifications, URLs, and cache bypass from model tool calls."""

    def __init__(self, delegate: QNetQualificationService, evidence_plan: list[dict[str, Any]]):
        self.delegate = delegate
        self.plan = [item for item in evidence_plan if item.get("action") == "QNET"]
        self.results: dict[str, QualificationEvidence] = {}

    def fetch_qnet_qualification(
        self,
        normalized_name: str,
        qnet_url: str,
        force_refresh: bool = False,
    ) -> QualificationEvidence:
        if force_refresh:
            raise PermissionError("The report agent cannot bypass the Q-Net cache")
        expected = next(
            (
                item for item in self.plan
                if comparison_key(item.get("itemName")) == comparison_key(normalized_name)
            ),
            None,
        )
        if expected is None or normalize_text(qnet_url) != normalize_text(expected.get("qnetUrl")):
            raise PermissionError("Q-Net tool call is not present in the evidence plan")
        result = self.delegate.fetch_qnet_qualification(normalized_name, qnet_url, False)
        self.results[expected["itemName"]] = result
        return result


def build_agent(retriever: RequirementRetriever, qnet_service: QNetQualificationService) -> Any:
    if not STRANDS_AVAILABLE:
        raise ReportAgentUnavailable("strands-agents is not installed")

    kb_tool_called = False
    qnet_tool_called = False

    @tool
    def retrieve_requirement_evidence(
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Retrieve all planned Bedrock KB items in one bounded batch call."""
        nonlocal kb_tool_called
        if kb_tool_called:
            raise PermissionError("KB evidence tool may be called only once per report attempt")
        kb_tool_called = True

        def retrieve_one(request: dict[str, Any]) -> dict[str, Any]:
            if request.get("action") != "KB":
                raise PermissionError("KB batch contains a non-KB evidence plan item")
            result = retriever.retrieve_requirement_evidence(
                str(request.get("targetTrade") or ""),
                str(request.get("query") or ""),
                request.get("itemType"),
                request.get("itemName"),
                request.get("ncsCode"),
                request.get("documentTypes"),
            )
            return {
                "itemName": request.get("itemName"),
                "evidence": _compact_kb_result(result),
            }

        return {"results": _run_bounded_batch(requests, retrieve_one, max_workers=6)}

    @tool
    def fetch_qnet_qualification(
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Fetch all planned Q-Net qualification items in one bounded batch call."""
        nonlocal qnet_tool_called
        if qnet_tool_called:
            raise PermissionError("Q-Net evidence tool may be called only once per report attempt")
        qnet_tool_called = True

        def fetch_one(request: dict[str, Any]) -> dict[str, Any]:
            if request.get("action") != "QNET":
                raise PermissionError("Q-Net batch contains a non-QNET evidence plan item")
            name = str(request.get("itemName") or "")
            result = qnet_service.fetch_qnet_qualification(
                name,
                str(request.get("qnetUrl") or ""),
                False,
            )
            return {
                "itemName": name,
                "evidence": _compact_qnet_result(result),
            }

        return {"results": _run_bounded_batch(requests, fetch_one, max_workers=4)}

    model = BedrockModel(
        model_id=os.environ.get("REPORT_MODEL_ID", "global.anthropic.claude-sonnet-4-6"),
        region_name=os.environ.get("REPORT_MODEL_REGION") or os.environ.get("AWS_REGION") or "ap-northeast-2",
        temperature=float(os.environ.get("REPORT_MODEL_TEMPERATURE", "0.1")),
    )
    agent = Agent(model=model, tools=[retrieve_requirement_evidence, fetch_qnet_qualification], system_prompt=load_system_prompt())
    agent._crewmate_tool_names = REPORT_TOOL_NAMES  # type: ignore[attr-defined]
    return agent


class ReportAgentRunner:
    def __init__(self, retriever: RequirementRetriever, qnet_service: QNetQualificationService, *, agent: Any | None = None):
        self.retriever = retriever
        self.qnet_service = qnet_service
        self.agent = agent
        self.last_kb_results: dict[str, RequirementEvidenceResult] = {}
        self.last_qnet_results: dict[str, QualificationEvidence] = {}

    def run(
        self,
        structured: StructuredGapAnalysis,
        evidence_plan: list[dict[str, Any]],
        evidence_context: dict[str, Any] | None = None,
    ) -> AgentReportDraft:
        self.last_kb_results = {}
        self.last_qnet_results = {}
        scoped_retriever = PlanBoundRetriever(self.retriever, evidence_plan)
        scoped_qnet = PlanBoundQNetService(self.qnet_service, evidence_plan)
        active_agent = self.agent or build_agent(scoped_retriever, scoped_qnet)
        prompt = json.dumps(
            {
                "structuredGapAnalysis": structured.model_dump(mode="json", by_alias=True),
                "evidencePlan": evidence_plan,
                "deterministicallyCollectedEvidence": evidence_context or {},
                "outputSchema": AgentReportDraft.model_json_schema(by_alias=True),
            },
            ensure_ascii=False,
        )
        timeout = float(os.environ.get("REPORT_AGENT_TIMEOUT_SECONDS", "30"))
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            active_agent,
            prompt,
            structured_output_model=AgentReportDraft,
        )
        try:
            result = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise ReportAgentUnavailable(f"report agent timed out after {timeout}s") from exc
        except Exception as exc:
            raise ReportAgentUnavailable(f"report agent failed: {type(exc).__name__}") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            self.last_kb_results = dict(scoped_retriever.results)
            self.last_qnet_results = dict(scoped_qnet.results)
        structured_output = getattr(result, "structured_output", None)
        if isinstance(structured_output, AgentReportDraft):
            return structured_output
        try:
            return AgentReportDraft.model_validate_json(_extract_json_payload(result))
        except Exception as exc:
            raise ReportAgentUnavailable("report agent returned invalid JSON/schema") from exc
