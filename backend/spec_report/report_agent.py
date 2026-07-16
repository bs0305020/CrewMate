"""Single Strands Report Agent with exactly two read-only evidence tools."""

from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
from typing import Any

from ncs_collector.models import SpecGapReport, StructuredGapAnalysis
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


def build_agent(retriever: RequirementRetriever, qnet_service: QNetQualificationService) -> Any:
    if not STRANDS_AVAILABLE:
        raise ReportAgentUnavailable("strands-agents is not installed")

    @tool
    def retrieve_requirement_evidence(
        target_trade: str,
        query: str,
        item_type: str | None = None,
        item_name: str | None = None,
        ncs_code: str | None = None,
        document_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Retrieve evidence from the configured Amazon Bedrock Knowledge Base."""
        result = retriever.retrieve_requirement_evidence(
            target_trade, query, item_type, item_name, ncs_code, document_types
        )
        return result.model_dump(mode="json", by_alias=True)

    @tool
    def fetch_qnet_qualification(
        normalized_name: str,
        qnet_url: str,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Fetch or read cached official qualification evidence from Q-Net."""
        result = qnet_service.fetch_qnet_qualification(normalized_name, qnet_url, force_refresh)
        return result.model_dump(mode="json", by_alias=True)

    model = BedrockModel(
        model_id=os.environ.get("REPORT_MODEL_ID", "apac.anthropic.claude-sonnet-4-6"),
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

    def run(
        self,
        structured: StructuredGapAnalysis,
        evidence_plan: list[dict[str, Any]],
        evidence_context: dict[str, Any] | None = None,
    ) -> SpecGapReport:
        active_agent = self.agent or build_agent(self.retriever, self.qnet_service)
        prompt = json.dumps(
            {
                "structuredGapAnalysis": structured.model_dump(mode="json", by_alias=True),
                "evidencePlan": evidence_plan,
                "deterministicallyCollectedEvidence": evidence_context or {},
                "outputSchema": SpecGapReport.model_json_schema(by_alias=True),
            },
            ensure_ascii=False,
        )
        timeout = float(os.environ.get("REPORT_AGENT_TIMEOUT_SECONDS", "30"))
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(active_agent, prompt)
        try:
            result = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            raise ReportAgentUnavailable(f"report agent timed out after {timeout}s") from exc
        except Exception as exc:
            raise ReportAgentUnavailable(f"report agent failed: {type(exc).__name__}") from exc
        finally:
            executor.shutdown(wait=False)
        try:
            return SpecGapReport.model_validate_json(_extract_text(result).strip())
        except Exception as exc:
            raise ReportAgentUnavailable("report agent returned invalid JSON/schema") from exc
