"""S3-backed authoritative rule repository using the existing CSV parser."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from ncs_collector.trade_requirements import LocalRuleRepository


class S3RuleRepository(LocalRuleRepository):
    """Materialize all three complete rule objects to Lambda /tmp, then reuse local logic."""

    def __init__(
        self,
        bucket_name: str | None = None,
        *,
        prefix: str | None = None,
        s3_client: Any | None = None,
        cache_root: str | Path | None = None,
    ):
        self.bucket_name = bucket_name or os.environ.get("KNOWLEDGE_SOURCE_BUCKET", "")
        self.prefix = (prefix if prefix is not None else os.environ.get("RULES_PREFIX", "rules/")).strip("/")
        if not self.bucket_name:
            raise ValueError("KNOWLEDGE_SOURCE_BUCKET is required")
        if s3_client is None:
            import boto3

            s3_client = boto3.client("s3")
        self.s3 = s3_client
        root = Path(cache_root or Path(tempfile.gettempdir()) / "crewmate-spec-rules")
        super().__init__(root)
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        for filename in (self.NORMALIZATION_FILE, self.CERTIFICATION_FILE, self.ABILITY_FILE):
            key = f"{self.prefix}/{filename}" if self.prefix else filename
            response = self.s3.get_object(Bucket=self.bucket_name, Key=key)
            payload = response["Body"].read()
            (self.root / filename).write_bytes(payload)
        self._loaded = True

    def certification_normalizer(self):
        self._ensure_loaded()
        return super().certification_normalizer()

    def certification_groups(self, target_trade: str):
        self._ensure_loaded()
        return super().certification_groups(target_trade)

    def abilities(self, target_trade: str):
        self._ensure_loaded()
        return super().abilities(target_trade)
