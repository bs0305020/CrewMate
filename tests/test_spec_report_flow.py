from __future__ import annotations

import json
from pathlib import Path

import pytest

from functions.spec_report import app as lambda_app
from ncs_collector.gap_analyzer import analyze_gap
from ncs_collector.models import (
    AgentItemEvidenceDraft,
    AgentReportDraft,
    ApplicantSpecInput,
    Citation,
    Evidence,
    EvidenceType,
    Confidence,
    QualificationEvidence,
    RequirementEvidenceResult,
    SpecGapReport,
)
from ncs_collector.rag_ready import build_rag_ready
from ncs_collector.trade_requirements import LocalRuleRepository
from spec_report.orchestrator import SpecReportService, build_evidence_plan
from spec_report.qnet import QNetQualificationService
from spec_report.rendering import build_fallback_report, materialize_agent_report, render_markdown
from spec_report.report_agent import ReportAgentRunner
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


def test_async_report_id_is_used_for_s3_keys():
    storage = FakeStorage()
    report, _, stored = _service(storage).generate(
        _applicant(True), offline=True, report_id="job-123"
    )
    assert report.report_id == "job-123"
    assert stored["jsonS3Key"] == "reports/job-123/report.json"


def test_spec_report_job_start_is_owned_and_invoked_asynchronously(monkeypatch):
    class Storage:
        def __init__(self):
            self.started = None

        def start_job(self, report_id, **kwargs):
            self.started = (report_id, kwargs)

        def fail_job(self, *_args):
            raise AssertionError("job must not fail")

    storage = Storage()
    invoked = []
    monkeypatch.setattr(lambda_app, "_storage", lambda: storage)
    monkeypatch.setattr(lambda_app, "_invoke_async", lambda payload, _context: invoked.append(payload))
    response = lambda_app.lambda_handler({
        "httpMethod": "POST",
        "path": "/reports/spec-gap/jobs",
        "body": _applicant(False).model_dump_json(by_alias=True),
        "requestContext": {"authorizer": {"claims": {
            "sub": "worker-sub", "custom:role": "WORKER",
        }}},
    }, None)

    body = json.loads(response["body"])
    assert response["statusCode"] == 202
    assert storage.started[1]["owner_user_id"] == "worker-sub"
    assert invoked[0]["reportId"] == body["reportId"]
    assert invoked[0]["applicant"]["persistReport"] is True


def test_spec_report_job_can_only_be_read_by_owner(monkeypatch):
    class Storage:
        def get_job(self, _report_id):
            return {"report_id": "job-1", "owner_user_id": "owner", "status": "COMPLETED"}

        def read(self, _report_id):
            return {"report": {"reportId": "job-1"}, "persisted": True}

    monkeypatch.setattr(lambda_app, "_storage", lambda: Storage())
    event = {
        "httpMethod": "GET",
        "path": "/reports/spec-gap/jobs/job-1",
        "pathParameters": {"reportId": "job-1"},
        "requestContext": {"authorizer": {"claims": {
            "sub": "other", "custom:role": "WORKER",
        }}},
    }
    response = lambda_app.lambda_handler(event, None)
    assert response["statusCode"] == 403


def test_spec_report_job_list_is_scoped_to_owner(monkeypatch):
    observed = {}

    class Storage:
        def list_jobs(self, owner_user_id):
            observed["owner"] = owner_user_id
            return [{
                "report_id": "job-1",
                "target_trade": "방수시공",
                "status": "COMPLETED",
                "created_at": "2026-07-17T00:00:00+00:00",
                "completed_at": "2026-07-17T00:01:00+00:00",
            }]

    monkeypatch.setattr(lambda_app, "_storage", lambda: Storage())
    response = lambda_app.lambda_handler({
        "httpMethod": "GET",
        "path": "/reports/spec-gap/jobs",
        "requestContext": {"authorizer": {"claims": {
            "sub": "worker-owner", "custom:role": "WORKER",
        }}},
    }, None)

    payload = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert observed["owner"] == "worker-owner"
    assert payload == [{
        "reportId": "job-1",
        "targetTrade": "방수시공",
        "status": "COMPLETED",
        "createdAt": "2026-07-17T00:00:00+00:00",
        "completedAt": "2026-07-17T00:01:00+00:00",
        "errorCode": None,
    }]


def test_report_storage_queries_owner_index_without_report_body():
    from spec_report.storage import S3ReportStorage

    class JobsTable:
        def __init__(self):
            self.kwargs = None

        def query(self, **kwargs):
            self.kwargs = kwargs
            return {"Items": [{"report_id": "job-1", "owner_user_id": "owner"}]}

    table = JobsTable()
    storage = S3ReportStorage(jobs_table=table)

    jobs = storage.list_jobs("owner", limit=20)

    assert jobs == [{"report_id": "job-1", "owner_user_id": "owner"}]
    assert table.kwargs["IndexName"] == "OwnerCreatedAtIndex"
    assert table.kwargs["ExpressionAttributeValues"] == {":owner": "owner"}
    assert table.kwargs["ScanIndexForward"] is False


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
    assert set(table.item) <= set(QualificationEvidence.model_fields) | {"expires_at", "schema_version"}
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
    assert metadata_path.stat().st_size <= 1024
    assert metadata["documentStructureConfiguration"]["type"] == "RECORD_BASED_STRUCTURE_METADATA"
    assert metadata["documentStructureConfiguration"]["recordBasedStructureMetadata"]["contentFields"] == [{"fieldName": "search_text"}]


def test_fallback_kb_citation_has_document_or_location():
    structured = analyze_gap(_applicant(), REPO)
    result = LocalKeywordRetriever(ROOT / "Archive" / "RAG_검색문서.jsonl").retrieve_requirement_evidence("방수시공", "도막 방수")
    report = build_fallback_report(structured, {"도막 방수": result}, {})
    validate_report(report, structured)
    assert all(c.document_id or c.source_url for c in report.citations if c.source_type == "LOCAL_KEYWORD")


def test_validator_rejects_unreturned_kb_document_id():
    structured = analyze_gap(_applicant(), REPO)
    plan = build_evidence_plan(structured, REPO, include_qnet=False)
    item_name = next(item["itemName"] for item in plan if item["action"] == "KB")
    retrieved = RequirementEvidenceResult(
        status="SUCCESS",
        evidence=[Evidence(
            text="구조화 요건 근거",
            document_id="returned-doc",
            source_location="s3://knowledge/returned-doc.csv",
            evidence_type=EvidenceType.BEDROCK_KB,
        )],
    )
    report = build_fallback_report(structured, {item_name: retrieved}, {})
    report.knowledge_base_evidence[0].local_document_ids = ["invented-doc"]
    with pytest.raises(ReportValidationError, match="unreturned document id"):
        validate_report(report, structured, {item_name: retrieved}, {}, plan)


def test_agent_draft_cannot_own_structured_qnet_or_citations():
    structured = analyze_gap(_applicant(), REPO)
    item_name = structured.missing_abilities[0].ability_name
    retrieved = RequirementEvidenceResult(
        status="SUCCESS",
        evidence=[Evidence(
            text="NCS 근거",
            document_id="ability-doc",
            source_location="s3://knowledge/ability.csv",
            metadata={"ncs_code": structured.missing_abilities[0].ncs_code},
            evidence_type=EvidenceType.BEDROCK_KB,
        )],
    )
    qnet = QualificationEvidence(
        normalized_name="방수기능사",
        official_name="방수기능사",
        source_url="https://www.q-net.or.kr/official",
        checked_at="2026-07-16T00:00:00+00:00",
        fetch_status="SUCCESS",
    )
    draft = AgentReportDraft(knowledge_base_evidence=[AgentItemEvidenceDraft(
        item_name=item_name,
        item_type="ABILITY",
        reason="검색 근거를 구조화 판정에 연결했다.",
        local_document_ids=["ability-doc"],
        ncs_codes=[structured.missing_abilities[0].ncs_code],
        confidence=Confidence.REFERENCE,
    )])
    report = materialize_agent_report(
        structured, draft, {item_name: retrieved}, {"방수기능사": qnet}
    )
    assert report.missing_abilities == structured.missing_abilities
    assert report.qnet_evidence == [qnet]
    assert report.knowledge_base_evidence[0].decision == "AUTHORITATIVE_RESULT_UNCHANGED"
    assert report.knowledge_base_evidence[0].evidence_types == ["BEDROCK_KB"]
    assert {citation.source_type for citation in report.citations} == {"BEDROCK_KB", "QNET"}


def test_runner_uses_strands_structured_output_model():
    draft = AgentReportDraft()

    class StructuredAgent:
        def __init__(self):
            self.kwargs = None

        def __call__(self, prompt, **kwargs):
            assert "structuredGapAnalysis" in prompt
            self.kwargs = kwargs
            return type("Result", (), {
                "structured_output": draft,
                "message": {"content": []},
            })()

    agent = StructuredAgent()
    runner = ReportAgentRunner(
        LocalKeywordRetriever(ROOT / "Archive" / "RAG_검색문서.jsonl"),
        QNetQualificationService(OfflineWeb()),
        agent=agent,
    )
    result = runner.run(analyze_gap(_applicant(), REPO), [])
    assert result is draft
    assert agent.kwargs["structured_output_model"] is AgentReportDraft


def test_markdown_hides_ncs_codes_and_uses_clickable_qnet_link():
    structured = analyze_gap(_applicant(), REPO)
    code = structured.missing_abilities[0].ncs_code
    structured.priority_actions[0].reason = f"직종 요구 NCS 능력 보완 ({code})"
    qnet_url = "https://www.q-net.or.kr/official"
    report = build_fallback_report(
        structured,
        {},
        {"방수기능사": QualificationEvidence(
            normalized_name="방수기능사",
            official_name="방수기능사",
            source_url=qnet_url,
            checked_at="2026-07-17T00:00:00+09:00",
            fetch_status="SUCCESS",
        )},
    )

    markdown = render_markdown(report)

    assert code not in markdown
    assert f"[Q-Net 공식 페이지]({qnet_url})" in markdown
    assert report.report_id not in markdown
