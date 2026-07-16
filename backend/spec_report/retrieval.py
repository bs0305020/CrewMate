"""Bedrock Knowledge Base and explicit offline keyword retrieval adapters."""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

from botocore.config import Config

from ncs_collector.models import Evidence, EvidenceType, RequirementEvidenceResult
from ncs_collector.text import comparison_key, normalize_text

_SENSITIVE_PATTERNS = (
    re.compile(r"\b01[016789][\s-]?\d{3,4}[\s-]?\d{4}\b"),
    re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I),
    re.compile(r"\b\d{6}[\s-]?[1-4]\d{6}\b"),
)


class RequirementRetriever(Protocol):
    def retrieve_requirement_evidence(
        self,
        target_trade: str,
        query: str,
        item_type: str | None = None,
        item_name: str | None = None,
        ncs_code: str | None = None,
        document_types: list[str] | None = None,
    ) -> RequirementEvidenceResult: ...


def _ensure_no_pii(query: str) -> None:
    if any(pattern.search(query) for pattern in _SENSITIVE_PATTERNS):
        raise ValueError("retrieval query contains possible personal information")


def _source_location(location: dict[str, Any] | None) -> str | None:
    if not location:
        return None
    for value in location.values():
        if isinstance(value, dict):
            for key in ("uri", "url", "id"):
                if value.get(key):
                    return str(value[key])
    return None


class BedrockKnowledgeBaseRetriever:
    """Call bedrock-agent-runtime.retrieve and preserve source provenance."""

    def __init__(
        self,
        knowledge_base_id: str | None = None,
        *,
        region_name: str | None = None,
        number_of_results: int | None = None,
        review_status: str | None = None,
        client: Any | None = None,
    ):
        self.knowledge_base_id = knowledge_base_id or os.environ.get("KNOWLEDGE_BASE_ID", "")
        self.region_name = region_name or os.environ.get("REPORT_MODEL_REGION") or os.environ.get("AWS_REGION") or "ap-northeast-2"
        self.number_of_results = number_of_results or int(os.environ.get("KB_NUMBER_OF_RESULTS", "5"))
        self.review_status = review_status or os.environ.get("KB_REVIEW_STATUS", "검토완료")
        if client is None and self.knowledge_base_id:
            import boto3

            client = boto3.client(
                "bedrock-agent-runtime",
                region_name=self.region_name,
                config=Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 2, "mode": "standard"}),
            )
        self.client = client

    def _filter(
        self,
        target_trade: str,
        ncs_code: str | None,
        document_types: list[str] | None,
    ) -> dict[str, Any]:
        clauses: list[dict[str, Any]] = [
            {"equals": {"key": "trade", "value": normalize_text(target_trade)}},
            {"equals": {"key": "review_status", "value": self.review_status}},
        ]
        if ncs_code:
            clauses.append({"equals": {"key": "ncs_code", "value": normalize_text(ncs_code)}})
        if document_types:
            if len(document_types) == 1:
                clauses.append({"equals": {"key": "document_type", "value": document_types[0]}})
            else:
                clauses.append({"in": {"key": "document_type", "value": document_types}})
        return clauses[0] if len(clauses) == 1 else {"andAll": clauses}

    def retrieve_requirement_evidence(
        self,
        target_trade: str,
        query: str,
        item_type: str | None = None,
        item_name: str | None = None,
        ncs_code: str | None = None,
        document_types: list[str] | None = None,
    ) -> RequirementEvidenceResult:
        del item_type, item_name
        _ensure_no_pii(query)
        if not self.knowledge_base_id or self.client is None:
            return RequirementEvidenceResult(
                status="NOT_CONFIGURED",
                error="KNOWLEDGE_BASE_ID is not configured; Bedrock KB retrieval was not performed.",
            )
        try:
            response = self.client.retrieve(
                knowledgeBaseId=self.knowledge_base_id,
                retrievalQuery={"text": normalize_text(query)},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {
                        "numberOfResults": self.number_of_results,
                        "filter": self._filter(target_trade, ncs_code, document_types),
                    }
                },
            )
        except Exception as exc:  # botocore errors are normalized for partial-report behavior
            return RequirementEvidenceResult(status="UNAVAILABLE", error=f"Bedrock KB retrieve failed: {type(exc).__name__}")

        evidence: list[Evidence] = []
        for result in response.get("retrievalResults", []):
            metadata = dict(result.get("metadata") or {})
            location = _source_location(result.get("location"))
            document_id = metadata.get("document_id") or metadata.get("문서ID")
            if not document_id and location:
                document_id = Path(location).name
            evidence.append(
                Evidence(
                    text=str((result.get("content") or {}).get("text") or ""),
                    score=result.get("score"),
                    metadata=metadata,
                    document_id=str(document_id) if document_id else None,
                    source_location=location,
                    evidence_type=EvidenceType.BEDROCK_KB,
                )
            )
        return RequirementEvidenceResult(status="SUCCESS", evidence=evidence)


class LocalKeywordRetriever:
    """Offline lexical search. It is intentionally not described as vector search."""

    def __init__(self, source_path: str | Path, number_of_results: int = 5):
        self.source_path = Path(source_path)
        self.number_of_results = number_of_results

    def _rows(self) -> list[dict[str, Any]]:
        if self.source_path.suffix.lower() == ".jsonl":
            with self.source_path.open("r", encoding="utf-8-sig") as handle:
                return [json.loads(line) for line in handle if line.strip()]
        with self.source_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def retrieve_requirement_evidence(
        self,
        target_trade: str,
        query: str,
        item_type: str | None = None,
        item_name: str | None = None,
        ncs_code: str | None = None,
        document_types: list[str] | None = None,
    ) -> RequirementEvidenceResult:
        del item_type, item_name
        _ensure_no_pii(query)
        terms = {comparison_key(term) for term in normalize_text(query).split() if term}
        candidates: list[tuple[float, dict[str, Any]]] = []
        for row in self._rows():
            trade = row.get("trade") or row.get("직종") or ""
            doc_type = row.get("document_type") or row.get("문서유형") or ""
            code = row.get("ncs_code") or row.get("NCS코드") or ""
            if comparison_key(trade) != comparison_key(target_trade):
                continue
            if ncs_code and comparison_key(code) != comparison_key(ncs_code):
                continue
            if document_types and doc_type not in document_types:
                continue
            text = row.get("search_text") or " ".join(
                str(row.get(key) or "") for key in ("제목", "본문", "자격증명", "능력명")
            )
            text_key = comparison_key(text)
            score = sum(1.0 for term in terms if term and term in text_key)
            if ncs_code and comparison_key(ncs_code) in text_key:
                score += 2.0
            if score:
                candidates.append((score, row | {"_text": text}))
        candidates.sort(key=lambda pair: (-pair[0], str(pair[1].get("문서ID") or pair[1].get("document_id") or "")))
        evidence = [
            Evidence(
                text=str(row["_text"]),
                score=score,
                metadata={k: v for k, v in row.items() if k != "_text"},
                document_id=str(row.get("document_id") or row.get("문서ID") or "") or None,
                source_location=str(self.source_path),
                evidence_type=EvidenceType.LOCAL_KEYWORD,
            )
            for score, row in candidates[: self.number_of_results]
        ]
        return RequirementEvidenceResult(status="SUCCESS", evidence=evidence)
