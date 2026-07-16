"""Optional S3 report persistence and non-sensitive DynamoDB job metadata."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from ncs_collector.models import SpecGapReport


class S3ReportStorage:
    def __init__(
        self,
        bucket_name: str | None = None,
        *,
        s3_client: Any | None = None,
        jobs_table: Any | None = None,
        kms_key_id: str | None = None,
        jobs_ttl_seconds: int | None = None,
    ):
        self.bucket_name = bucket_name or os.environ.get("REPORT_OUTPUT_BUCKET", "")
        self.kms_key_id = kms_key_id or os.environ.get("REPORT_KMS_KEY_ID", "")
        self.jobs_ttl_seconds = jobs_ttl_seconds or int(os.environ.get("REPORT_JOBS_TTL_SECONDS", "2592000"))
        if s3_client is None and self.bucket_name:
            import boto3

            s3_client = boto3.client("s3")
        self.s3 = s3_client
        if jobs_table is None and os.environ.get("SPEC_REPORT_JOBS_TABLE"):
            import boto3

            jobs_table = boto3.resource("dynamodb").Table(os.environ["SPEC_REPORT_JOBS_TABLE"])
        self.jobs_table = jobs_table

    def _put(self, key: str, body: bytes, content_type: str) -> None:
        if not self.bucket_name or self.s3 is None:
            raise RuntimeError("Report output bucket is not configured")
        kwargs = {
            "Bucket": self.bucket_name,
            "Key": key,
            "Body": body,
            "ContentType": content_type,
            "ServerSideEncryption": "aws:kms",
        }
        if self.kms_key_id:
            kwargs["SSEKMSKeyId"] = self.kms_key_id
        self.s3.put_object(**kwargs)

    def save(self, report: SpecGapReport, markdown: str | None) -> dict[str, str]:
        prefix = f"reports/{report.report_id}"
        json_key = f"{prefix}/report.json"
        markdown_key = f"{prefix}/report.md" if markdown is not None else ""
        self._put(json_key, report.model_dump_json(by_alias=True, indent=2).encode("utf-8"), "application/json; charset=utf-8")
        if markdown is not None:
            self._put(markdown_key, markdown.encode("utf-8"), "text/markdown; charset=utf-8")
        now = datetime.now(timezone.utc).isoformat()
        if self.jobs_table is not None:
            item = {
                "report_id": report.report_id,
                "target_trade": report.target_trade,
                "analysis_scope": report.analysis_scope,
                "status": "COMPLETED",
                "json_s3_key": json_key,
                "created_at": report.generated_at,
                "completed_at": now,
                "expires_at": int(time.time()) + self.jobs_ttl_seconds,
            }
            if markdown_key:
                item["markdown_s3_key"] = markdown_key
            # No report body, certifications, abilities, experience, or PII is stored here.
            self.jobs_table.put_item(Item=item)
        result = {"jsonS3Key": json_key}
        if markdown_key:
            result["markdownS3Key"] = markdown_key
        return result
