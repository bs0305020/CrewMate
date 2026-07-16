"""Safe Q-Net official qualification lookup with injectable web and cache adapters."""

from __future__ import annotations

import html
import os
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ncs_collector.models import QualificationEvidence
from ncs_collector.text import comparison_key, normalize_text

_ALLOWED_HOSTS = {"q-net.or.kr", "www.q-net.or.kr"}


def validate_qnet_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("Q-Net URL must use https")
    if (parsed.hostname or "").lower() not in _ALLOWED_HOSTS:
        raise ValueError("Q-Net URL host is not allowed")
    if parsed.username or parsed.password:
        raise ValueError("credentials in Q-Net URL are not allowed")
    return url


class QualificationWebTool(Protocol):
    def fetch_qualification(self, normalized_name: str, qnet_url: str) -> QualificationEvidence: ...


class QualificationCache(Protocol):
    def get(self, normalized_name: str) -> QualificationEvidence | None: ...
    def put(self, evidence: QualificationEvidence, expires_at: int) -> None: ...


class NullQualificationCache:
    def get(self, normalized_name: str) -> QualificationEvidence | None:
        del normalized_name
        return None

    def put(self, evidence: QualificationEvidence, expires_at: int) -> None:
        del evidence, expires_at


class DynamoQualificationCache:
    def __init__(self, table_name: str | None = None, *, table: Any | None = None):
        self.table_name = table_name or os.environ.get("QUALIFICATION_CACHE_TABLE", "")
        if table is None and self.table_name:
            import boto3

            table = boto3.resource("dynamodb").Table(self.table_name)
        self.table = table

    def get(self, normalized_name: str) -> QualificationEvidence | None:
        if self.table is None:
            return None
        item = self.table.get_item(Key={"normalized_name": normalized_name}, ConsistentRead=False).get("Item")
        if not item or int(item.get("expires_at", 0)) <= int(time.time()):
            return None
        fields = set(QualificationEvidence.model_fields)
        payload = {key: value for key, value in item.items() if key in fields}
        payload["from_cache"] = True
        return QualificationEvidence.model_validate(payload)

    def put(self, evidence: QualificationEvidence, expires_at: int) -> None:
        if self.table is None:
            return
        item = evidence.model_dump(mode="json")
        item["expires_at"] = int(expires_at)
        # The cache contract contains qualification evidence only, never applicant data.
        self.table.put_item(Item={key: value for key, value in item.items() if value is not None})


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        value = normalize_text(html.unescape(data))
        if value:
            self.parts.append(value)
            if self._in_title:
                self.title_parts.append(value)


class _ValidatingRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        validated = validate_qnet_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, validated)


class QNetHttpAdapter:
    """Small stdlib HTTP adapter; remote HTML is parsed as untrusted data only."""

    def __init__(self, *, timeout: float = 5.0, retries: int = 1, min_interval: float = 0.5, opener: Any | None = None):
        self.timeout = timeout
        self.retries = max(0, retries)
        self.min_interval = max(0.0, min_interval)
        self.opener = opener or build_opener(_ValidatingRedirectHandler())
        self._last_call = 0.0

    def fetch_qualification(self, normalized_name: str, qnet_url: str) -> QualificationEvidence:
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            validate_qnet_url(qnet_url)
        except ValueError as exc:
            return QualificationEvidence(
                normalized_name=normalized_name,
                source_url=qnet_url or None,
                checked_at=checked_at,
                fetch_status="INVALID_URL",
                error=str(exc),
            )
        request = Request(qnet_url, headers={"User-Agent": "CrewMateQualificationVerifier/1.0"})
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                self._last_call = time.monotonic()
                with self.opener.open(request, timeout=self.timeout) as response:
                    final_url = validate_qnet_url(response.geturl())
                    payload = response.read(2_000_000)
                    encoding = response.headers.get_content_charset() or "utf-8"
                parser = _TextParser()
                parser.feed(payload.decode(encoding, errors="replace"))
                page_text = normalize_text(" ".join(parser.parts))
                if comparison_key(normalized_name) not in comparison_key(page_text):
                    return QualificationEvidence(
                        normalized_name=normalized_name,
                        source_url=final_url,
                        checked_at=checked_at,
                        fetch_status="NAME_MISMATCH",
                        error="The requested qualification name was not found on the returned Q-Net page.",
                    )
                return QualificationEvidence(
                    normalized_name=normalized_name,
                    official_name=normalized_name,
                    status="OFFICIAL_PAGE_CONFIRMED",
                    source_url=final_url,
                    checked_at=checked_at,
                    fetch_status="SUCCESS",
                )
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(0.25 * (2**attempt), 1.0))
        return QualificationEvidence(
            normalized_name=normalized_name,
            source_url=qnet_url,
            checked_at=checked_at,
            fetch_status="UNAVAILABLE",
            error=f"Q-Net request failed: {type(last_error).__name__}",
        )


class QNetQualificationService:
    def __init__(
        self,
        web_tool: QualificationWebTool,
        cache: QualificationCache | None = None,
        *,
        ttl_seconds: int | None = None,
    ):
        self.web_tool = web_tool
        self.cache = cache or NullQualificationCache()
        self.ttl_seconds = ttl_seconds or int(os.environ.get("QNET_CACHE_TTL_SECONDS", "604800"))

    def fetch_qnet_qualification(
        self,
        normalized_name: str,
        qnet_url: str,
        force_refresh: bool = False,
    ) -> QualificationEvidence:
        name = normalize_text(normalized_name)
        if not qnet_url:
            return QualificationEvidence(
                normalized_name=name,
                fetch_status="URL_MISSING",
                error="The normalization master has no Q-Net URL for this qualification.",
            )
        try:
            validate_qnet_url(qnet_url)
        except ValueError as exc:
            return QualificationEvidence(
                normalized_name=name,
                source_url=qnet_url,
                fetch_status="INVALID_URL",
                error=str(exc),
            )
        if not force_refresh:
            cached = self.cache.get(name)
            if cached is not None:
                return cached
        evidence = self.web_tool.fetch_qualification(name, qnet_url)
        if evidence.fetch_status in {"SUCCESS", "NAME_MISMATCH", "UNAVAILABLE"}:
            self.cache.put(evidence, int(time.time()) + self.ttl_seconds)
        return evidence
