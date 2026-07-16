from __future__ import annotations

import json
from pathlib import Path

import pytest

from functions.spec_report import app as lambda_app
from ncs_collector.gap_analyzer import analyze_gap
from ncs_collector.models import (
    ApplicantSpecInput,
    Citation,
    QualificationEvidence,
    RequirementEvidenceResult,
    SpecGapReport,
)
from ncs_collector.rag_ready import build_rag_ready
from ncs_collector.trade_requirements import LocalRuleRepository
from spec_report.orchestrator import SpecReportService
from spec_report.qnet import QNetQualificationService
from spec_report.rendering import build_fallback_report
from spec_report.retrieval import LocalKeywordRetriever
from spec_report.validator import ReportValidationError, validate_report

ROOT = Path(__file__).resolve().parents[1]
REPO = LocalRuleRepository(ROOT / "Archive")


def _applicant(persist=False):
    return ApplicantSpecInput.model_validate({
        "targetTrade": "방수시공",
        "certifications": ["방수 기능사"],
        "abilities": ["도막 방수", "바탕 처리"],
        "persistReport": persist,
    })


class OfflineWeb:
    def fetch_qualification(self, normalized_name, qnet_url):
        return QualificationEvidence(normalized_name=normalized_name, fetch_status="UNAVAILABLE")


class FakeStorage:
    def __init__(self):
        self.calls = []

    def save(self, report, markdown):
        self.calls.append((report, markdown))
        return {"jsonS3Key": f"reports/{report.report_id}/report.json", "markdownS3Key": f"reports/{report.report_id}/report.md"}


def _service(storage=None):
    return SpecReportService(
        REPO,
        LocalKeywordRetriever(ROOT / "Archive" / "RAG_검색문서.jsonl"),
        QNetQualificationService(OfflineWeb()),
        storage=storage,
    )


def test_20_qnet_failure_does_not_block_report():
    structured = analyze_gap(_applicant(), REPO)
    report = build_fallback_report(
        structured,
        {},
        {"방수기능사": QualificationEvidence(normalized_name="방수기능사", fetch_status="UNAVAILABLE", error="timeout")},
    )
    assert report.target_trade == "방수시공"
    assert any("Q-Net 확인 실패" in item for item in report.limitations)


def test_27_agent_cannot_change_authoritative_gap_result():
    structured = analyze_gap(_applicant(), REPO)
    report = build_fallback_report(structured, {}, {})
    report.missing_core_certification_groups = [structured.satisfied_certification_groups[0]]
    with pytest.raises(ReportValidationError):
        validate_report(report, structured)


def test_28_report_output_schema_validation():
    report, _, _ = _service().generate(_applicant(), offline=True)
    reparsed = SpecGapReport.model_validate_json(report.model_dump_json(by_alias=True))
    assert reparsed.report_id == report.report_id


def test_29_official_claim_requires_url_and_checked_time():
    structured = analyze_gap(_applicant(), REPO)
    report = build_fallback_report(structured, {}, {})
    report.qnet_evidence.append(QualificationEvidence(
        normalized_name="방수기능사",
        official_name="방수기능사",
        fetch_status="SUCCESS",
    ))
    with pytest.raises(ReportValidationError):
        validate_report(report, structured)


def test_30_persist_false_does_not_write_storage():
    storage = FakeStorage()
    _, _, stored = _service(storage).generate(_applicant(False), offline=True)
    assert stored == {} and not storage.calls


def test_31_persist_true_writes_json_and_markdown():
    storage = FakeStorage()
    _, _, stored = _service(storage).generate(_applicant(True), offline=True)
    assert len(storage.calls) == 1
    assert stored["jsonS3Key"].endswith("report.json")
    assert stored["markdownS3Key"].endswith("report.md")


def test_32_cache_record_has_no_applicant_personal_data():
    from spec_report.qnet import DynamoQualificationCache

    class Table:
        def __init__(self): self.item = None
        def put_item(self, Item): self.item = Item

    table = Table()
    cache = DynamoQualificationCache(table=table)
    cache.put(QualificationEvidence(
        normalized_name="방수기능사", official_name="방수기능사", source_url="https://www.q-net.or.kr/x",
        checked_at="2026-01-01", fetch_status="SUCCESS"
    ), 9999999999)
    assert set(table.item) <= set(QualificationEvidence.model_fields) | {"expires_at"}
    assert not {"name", "phone", "experience", "abilities"} & set(table.item)


def test_33_lambda_invalid_input_error_code():
    response = lambda_app.lambda_handler({"body": json.dumps({"targetTrade": ""})}, None)
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"]["code"] == "INVALID_INPUT"


def test_rag_conversion_writes_record_metadata(tmp_path):
    result = build_rag_ready(ROOT / "Archive", tmp_path)
    csv_path = result["knowledge_base"] / "rag-search-documents.csv"
    metadata_path = csv_path.with_name(csv_path.name + ".metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert csv_path.exists()
    assert metadata["documentStructureConfiguration"]["type"] == "RECORD_BASED_STRUCTURE_METADATA"
    assert metadata["documentStructureConfiguration"]["recordBasedStructureMetadata"]["contentFields"] == [{"fieldName": "search_text"}]


def test_fallback_kb_citation_has_document_or_location():
    structured = analyze_gap(_applicant(), REPO)
    result = LocalKeywordRetriever(ROOT / "Archive" / "RAG_검색문서.jsonl").retrieve_requirement_evidence("방수시공", "도막 방수")
    report = build_fallback_report(structured, {"도막 방수": result}, {})
    validate_report(report, structured)
    assert all(c.document_id or c.source_url for c in report.citations if c.source_type == "LOCAL_KEYWORD")
