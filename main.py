"""CrewMate data and applicant specification report CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from ncs_collector.models import ApplicantSpecInput  # noqa: E402
from ncs_collector.rag_ready import build_rag_ready, upload_rag_ready  # noqa: E402
from ncs_collector.trade_requirements import LocalRuleRepository  # noqa: E402
from spec_report.orchestrator import SpecReportService  # noqa: E402
from spec_report.qnet import (  # noqa: E402
    DynamoQualificationCache,
    NullQualificationCache,
    QNetHttpAdapter,
    QNetQualificationService,
)
from spec_report.retrieval import BedrockKnowledgeBaseRetriever, LocalKeywordRetriever  # noqa: E402
from spec_report.report_agent import ReportAgentRunner  # noqa: E402
from spec_report.storage import S3ReportStorage  # noqa: E402


def _generate(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
    if args.persist_report:
        payload["persistReport"] = True
    applicant = ApplicantSpecInput.model_validate(payload)
    repository = LocalRuleRepository(args.data_root)
    if args.offline:
        retriever = LocalKeywordRetriever(Path(args.data_root) / "RAG_검색문서.jsonl")
        qnet = QNetQualificationService(QNetHttpAdapter(), NullQualificationCache())
        runner = None
        storage = None
    else:
        retriever = BedrockKnowledgeBaseRetriever()
        qnet = QNetQualificationService(QNetHttpAdapter(), DynamoQualificationCache())
        runner = ReportAgentRunner(retriever, qnet)
        storage = S3ReportStorage() if applicant.persist_report else None
    service = SpecReportService(repository, retriever, qnet, agent_runner=runner, storage=storage)
    report, markdown, stored = service.generate(
        applicant,
        offline=args.offline,
        refresh_qnet=args.refresh_qnet,
        json_only=args.json_only,
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(report.model_dump_json(by_alias=True, indent=2), encoding="utf-8")
    if markdown is not None and args.markdown_output:
        Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_output).write_text(markdown, encoding="utf-8")
    print(json.dumps({"reportId": report.report_id, "output": args.output, "storage": stored}, ensure_ascii=False))
    return 0


def _build_rag(args: argparse.Namespace) -> int:
    assets = build_rag_ready(args.source_root, args.output_root)
    uploaded: list[str] = []
    if args.upload_bucket:
        uploaded = upload_rag_ready(args.output_root, args.upload_bucket)
    print(json.dumps(
        {
            "rules": str(assets["rules"]),
            "knowledgeBase": str(assets["knowledge_base"]),
            "manifest": str(assets["manifest"]),
            "uploaded": uploaded,
        },
        ensure_ascii=False,
    ))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CrewMate applicant specification reporting")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate-spec-report", help="generate JSON/Markdown gap report")
    generate.add_argument("--input", required=True)
    generate.add_argument("--output", required=True)
    generate.add_argument("--markdown-output")
    generate.add_argument("--data-root", default=str(ROOT / "Archive"))
    generate.add_argument("--offline", action="store_true", help="disable Bedrock KB, model, and Q-Net HTTP")
    generate.add_argument("--refresh-qnet", action="store_true")
    generate.add_argument("--json-only", action="store_true")
    generate.add_argument("--persist-report", action="store_true")
    generate.set_defaults(func=_generate)

    rag = subparsers.add_parser("build-rag", help="convert Archive files to Bedrock KB CSV assets")
    rag.add_argument("--source-root", default=str(ROOT / "Archive"))
    rag.add_argument("--output-root", default=str(ROOT / "data" / "rag-ready"))
    rag.add_argument("--upload-bucket", help="optional Knowledge Source S3 bucket")
    rag.set_defaults(func=_build_rag)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
