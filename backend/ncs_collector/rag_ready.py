"""Convert immutable Archive rules/JSONL to Bedrock KB record-based CSV assets."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any, Iterable

from .text import normalize_text

FIELDS = [
    "search_text",
    "document_id",
    "document_type",
    "trade",
    "certification_group",
    "importance",
    "selection_rule",
    "certification_name",
    "ncs_code",
    "review_status",
    "source_file",
]
METADATA_FIELDS = [field for field in FIELDS if field != "search_text"]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: normalize_text(value) for key, value in row.items()} for row in csv.DictReader(handle)]


def _write_record_csv(path: Path, rows: Iterable[dict[str, Any]], source_file: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore", quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})
    metadata = {
        "metadataAttributes": {"source_file": source_file},
        "documentStructureConfiguration": {
            "type": "RECORD_BASED_STRUCTURE_METADATA",
            "recordBasedStructureMetadata": {
                "contentFields": [{"fieldName": "search_text"}],
                "metadataFieldsSpecification": {
                    "fieldsToInclude": [{"fieldName": field} for field in METADATA_FIELDS]
                },
            },
        },
    }
    path.with_name(path.name + ".metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def build_rag_ready(source_root: str | Path, output_root: str | Path) -> dict[str, Path]:
    """Build rules/ and knowledge-base/ without modifying source files."""
    source = Path(source_root)
    output = Path(output_root)
    rules_dir = output / "rules"
    kb_dir = output / "knowledge-base"
    rules_dir.mkdir(parents=True, exist_ok=True)
    kb_dir.mkdir(parents=True, exist_ok=True)

    source_names = (
        "직종별_자격요건.csv",
        "직종별_능력요건.csv",
        "자격증_정규화_마스터.csv",
    )
    for name in source_names:
        shutil.copy2(source / name, rules_dir / name)

    cert_rows = []
    for index, row in enumerate(_read_csv(source / "직종별_자격요건.csv"), start=1):
        cert_rows.append({
            "search_text": (
                f"직종 {row['직종']}의 {row['중요도']} 자격그룹은 {row['자격그룹']}이며 "
                f"선택규칙은 {row['선택규칙']}, 자격증은 {row['자격증명']}이다. "
                "직종 관련성 근거이며 법적 필수 여부는 별도 확인한다."
            ),
            "document_id": f"cert-requirement-{index:05d}",
            "document_type": "자격 요건",
            "trade": row["직종"],
            "certification_group": row["자격그룹"],
            "importance": row["중요도"],
            "selection_rule": row["선택규칙"],
            "certification_name": row["자격증명"],
            "review_status": "원본 상태 미표기",
            "source_file": "직종별_자격요건.csv",
        })
    cert_path = kb_dir / "certification-requirements.csv"
    _write_record_csv(cert_path, cert_rows, "직종별_자격요건.csv")

    ability_rows = []
    for index, row in enumerate(_read_csv(source / "직종별_능력요건.csv"), start=1):
        ability_rows.append({
            "search_text": (
                f"직종 {row['직종']}의 NCS 능력은 {row['능력명']}이며 "
                f"NCS 코드는 {row['NCS코드']}, 세분류는 {row['NCS세분류']}이다."
            ),
            "document_id": f"ability-requirement-{index:05d}",
            "document_type": "NCS 능력 요구사항",
            "trade": row["직종"],
            "ncs_code": row["NCS코드"],
            "review_status": "원본 상태 미표기",
            "source_file": "직종별_능력요건.csv",
        })
    ability_path = kb_dir / "ability-requirements.csv"
    _write_record_csv(ability_path, ability_rows, "직종별_능력요건.csv")

    normalization_rows = []
    for index, row in enumerate(_read_csv(source / "자격증_정규화_마스터.csv"), start=1):
        normalization_rows.append({
            "search_text": (
                f"자격 표기 {row['입력표기']}의 표준명은 {row['정규화자격증명']}이다. "
                f"표기구분 {row['표기구분']}, 자격유형 {row['자격유형']}, 상태 {row['자격상태']}이다."
            ),
            "document_id": f"cert-normalization-{index:05d}",
            "document_type": "자격 정규화 근거",
            "certification_name": row["정규화자격증명"],
            "review_status": row["자격상태"],
            "source_file": "자격증_정규화_마스터.csv",
        })
    normalization_path = kb_dir / "certification-normalization-evidence.csv"
    _write_record_csv(normalization_path, normalization_rows, "자격증_정규화_마스터.csv")

    rag_rows = []
    with (source / "RAG_검색문서.jsonl").open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            rag_rows.append({
                "search_text": " ".join(
                    filter(None, [normalize_text(row.get("제목")), normalize_text(row.get("본문")),
                                  f"직종 {normalize_text(row.get('직종'))}",
                                  f"NCS 코드 {normalize_text(row.get('NCS코드'))}" if row.get("NCS코드") else ""])
                ),
                "document_id": normalize_text(row.get("문서ID")),
                "document_type": normalize_text(row.get("문서유형")),
                "trade": normalize_text(row.get("직종")),
                "ncs_code": normalize_text(row.get("NCS코드")),
                "review_status": normalize_text(row.get("검토상태")),
                "source_file": "RAG_검색문서.jsonl",
            })
    rag_path = kb_dir / "rag-search-documents.csv"
    _write_record_csv(rag_path, rag_rows, "RAG_검색문서.jsonl")

    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "rulesPrefix": "rules/",
                "knowledgeBasePrefix": "knowledge-base/",
                "contentField": "search_text",
                "metadataFields": METADATA_FIELDS,
                "sourceFilesModified": False,
                "assets": [str(path.relative_to(output)).replace("\\", "/") for path in (
                    cert_path, ability_path, normalization_path, rag_path
                )],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "rules": rules_dir,
        "knowledge_base": kb_dir,
        "manifest": manifest_path,
    }


def upload_rag_ready(output_root: str | Path, bucket_name: str, *, s3_client: Any | None = None) -> list[str]:
    if s3_client is None:
        import boto3

        s3_client = boto3.client("s3")
    root = Path(output_root)
    uploaded: list[str] = []
    for directory in (root / "rules", root / "knowledge-base"):
        for path in directory.rglob("*"):
            if path.is_file():
                key = str(path.relative_to(root)).replace("\\", "/")
                s3_client.upload_file(str(path), bucket_name, key)
                uploaded.append(key)
    return uploaded
