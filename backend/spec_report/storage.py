"""S3 report persistence and non-sensitive asynchronous job metadata."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError

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

    def start_job(self, report_id: str, *, owner_user_id: str, target_trade: str) -> None:
        if self.jobs_table is None:
            raise RuntimeError("Spec report jobs table is not configured")
        now = datetime.now(timezone.utc).isoformat()
        self.jobs_table.put_item(Item={
            "report_id": report_id,
            "owner_user_id": owner_user_id,
            "target_trade": target_trade,
            "status": "PROCESSING",
            "created_at": now,
            "expires_at": int(time.time()) + self.jobs_ttl_seconds,
        })

    def fail_job(self, report_id: str, error_code: str = "REPORT_GENERATION_FAILED") -> None:
        if self.jobs_table is None:
            return
        self.jobs_table.update_item(
            Key={"report_id": report_id},
            UpdateExpression="SET #status = :status, error_code = :error, completed_at = :completed",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "FAILED",
                ":error": error_code,
                ":completed": datetime.now(timezone.utc).isoformat(),
            },
        )

    def get_job(self, report_id: str) -> dict[str, Any] | None:
        if self.jobs_table is None:
            raise RuntimeError("Spec report jobs table is not configured")
        return self.jobs_table.get_item(Key={"report_id": report_id}).get("Item")

    def list_jobs(self, owner_user_id: str, *, limit: int = 30) -> list[dict[str, Any]]:
        """List non-sensitive report metadata for one owner, newest first."""
        if self.jobs_table is None:
            raise RuntimeError("Spec report jobs table is not configured")
        response = self.jobs_table.query(
            IndexName="OwnerCreatedAtIndex",
            KeyConditionExpression="owner_user_id = :owner",
            ExpressionAttributeValues={":owner": owner_user_id},
            ScanIndexForward=False,
            Limit=max(1, min(int(limit), 100)),
        )
        return list(response.get("Items") or [])

    def read(self, report_id: str) -> dict[str, Any]:
        if not self.bucket_name or self.s3 is None:
            raise RuntimeError("Report output bucket is not configured")
        prefix = f"reports/{report_id}"
        report_body = self.s3.get_object(
            Bucket=self.bucket_name, Key=f"{prefix}/report.json"
        )["Body"].read()
        result: dict[str, Any] = {
            "report": json.loads(report_body.decode("utf-8")),
            "persisted": True,
        }
        try:
            markdown_body = self.s3.get_object(
                Bucket=self.bucket_name, Key=f"{prefix}/report.md"
            )["Body"].read()
            result["markdown"] = markdown_body.decode("utf-8")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
                raise
        return result

    def save(self, report: SpecGapReport, markdown: str | None) -> dict[str, str]:
        prefix = f"reports/{report.report_id}"
        json_key = f"{prefix}/report.json"
        markdown_key = f"{prefix}/report.md" if markdown is not None else ""
        self._put(json_key, report.model_dump_json(by_alias=True, indent=2).encode("utf-8"), "application/json; charset=utf-8")
        if markdown is not None:
            self._put(markdown_key, markdown.encode("utf-8"), "text/markdown; charset=utf-8")
        now = datetime.now(timezone.utc).isoformat()
        if self.jobs_table is not None:
            expression = (
                "SET #status = :status, target_trade = :trade, analysis_scope = :scope, "
                "json_s3_key = :json_key, completed_at = :completed, expires_at = :expires"
            )
            values: dict[str, Any] = {
                ":status": "COMPLETED",
                ":trade": report.target_trade,
                ":scope": report.analysis_scope,
                ":json_key": json_key,
                ":completed": now,
                ":expires": int(time.time()) + self.jobs_ttl_seconds,
            }
            if markdown_key:
                expression += ", markdown_s3_key = :markdown_key"
                values[":markdown_key"] = markdown_key
            # No report body, certifications, abilities, experience, or PII is stored here.
            self.jobs_table.update_item(
                Key={"report_id": report.report_id},
                UpdateExpression=expression,
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=values,
            )
        result = {"jsonS3Key": json_key}
        if markdown_key:
            result["markdownS3Key"] = markdown_key
        return result
