"""Safe Q-Net official qualification lookup with injectable web and cache adapters."""

from __future__ import annotations

import html
import os
import re
import threading
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ncs_collector.models import QualificationEvidence
from ncs_collector.text import comparison_key, normalize_text

_ALLOWED_HOSTS = {"q-net.or.kr", "www.q-net.or.kr"}
_CACHE_SCHEMA_VERSION = 4


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
        self._lock = threading.Lock()

    def get(self, normalized_name: str) -> QualificationEvidence | None:
        if self.table is None:
            return None
        with self._lock:
            item = self.table.get_item(
                Key={"normalized_name": normalized_name}, ConsistentRead=False
            ).get("Item")
        if (
            not item
            or int(item.get("expires_at", 0)) <= int(time.time())
            or int(item.get("schema_version", 0)) != _CACHE_SCHEMA_VERSION
        ):
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
        item["schema_version"] = _CACHE_SCHEMA_VERSION
        # The cache contract contains qualification evidence only, never applicant data.
        with self._lock:
            self.table.put_item(
                Item={key: value for key, value in item.items() if value is not None}
            )


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized_tag = tag.lower()
        if normalized_tag in {"script", "style"}:
            self._ignored_depth += 1
        elif normalized_tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif normalized_tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = normalize_text(html.unescape(data))
        if value:
            self.parts.append(value)
            if self._in_title:
                self.title_parts.append(value)


def _plain_text(value: str) -> str:
    parser = _TextParser()
    parser.feed(html.unescape(value))
    return normalize_text(" ".join(parser.parts))


def _section_text(payload: str, label: str) -> str | None:
    """Extract a labelled Q-Net textarea section without executing remote markup."""
    match = re.search(
        rf"<b\b[^>]*>\s*{re.escape(label)}\s*</b>.*?<textarea\b[^>]*>(.*?)</textarea>",
        payload,
        flags=re.I | re.S,
    )
    if not match:
        return None
    value = _plain_text(match.group(1))
    return value or None


def _plain_section(
    payload: str,
    label: str,
    stop_labels: tuple[str, ...],
    *,
    max_length: int = 3000,
) -> str | None:
    """Extract a bounded visible-text section such as a schedule or fee table.

    Q-Net renders these areas as ordinary tables rather than labelled textareas.
    The remote page is still treated only as untrusted text: scripts/styles are
    discarded by ``_plain_text`` and no markup or instruction is executed.
    """
    text = _plain_text(payload)
    start = re.search(rf"(?:^|\s){re.escape(label)}(?:\s|$)", text)
    if not start:
        return None
    tail = text[start.end():].strip()
    end_positions = [
        match.start()
        for stop_label in stop_labels
        if (match := re.search(rf"(?:^|\s){re.escape(stop_label)}(?:\s|$)", tail))
    ]
    if end_positions:
        tail = tail[:min(end_positions)].strip()
    if not tail:
        return None
    if len(tail) > max_length:
        tail = tail[:max_length].rstrip() + "…"
    return tail


def _qualification_code(payload: str, normalized_name: str) -> str | None:
    """Resolve only an exact Q-Net search result; never connect a similar name."""
    for match in re.finditer(
        r"goJmDetail\(\s*['\"](?P<code>[0-9A-Za-z_-]+)['\"]\s*,\s*['\"](?P<name>[^'\"]+)['\"]\s*\)",
        payload,
        flags=re.I,
    ):
        candidate = normalize_text(html.unescape(match.group("name")))
        if comparison_key(candidate) == comparison_key(normalized_name):
            return match.group("code")
    return None


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
        self._schedule_lock = threading.Lock()

    def _fetch_text(self, url: str) -> tuple[str, str]:
        validated_url = validate_qnet_url(url)
        request = Request(validated_url, headers={"User-Agent": "CrewMateQualificationVerifier/1.0"})
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            with self._schedule_lock:
                wait = self.min_interval - (time.monotonic() - self._last_call)
                if wait > 0:
                    time.sleep(wait)
                self._last_call = time.monotonic()
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    final_url = validate_qnet_url(response.geturl())
                    payload = response.read(2_000_000)
                    encoding = response.headers.get_content_charset() or "utf-8"
                return final_url, payload.decode(encoding, errors="replace")
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(0.25 * (2**attempt), 1.0))
        raise RuntimeError(f"Q-Net request failed: {type(last_error).__name__}") from last_error

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
        try:
            _, search_payload = self._fetch_text(qnet_url)
            qualification_code = _qualification_code(search_payload, normalized_name)
            if not qualification_code:
                return QualificationEvidence(
                    normalized_name=normalized_name,
                    source_url=qnet_url,
                    checked_at=checked_at,
                    fetch_status="NAME_MISMATCH",
                    error="An exact qualification name/code match was not found in Q-Net search results.",
                )

            base_params = {
                "gSite": "Q",
                "gId": "",
                "jmCd": qualification_code,
                "jmNm": normalized_name,
            }
            detail_url = "https://www.q-net.or.kr/crf005.do?" + urlencode(
                {"id": "crf00503", **base_params}
            )
            basic_url = "https://www.q-net.or.kr/crf005.do?" + urlencode(
                {"id": "crf00503s01", **base_params, "jmInfoDivCcd": "A0"}
            )
            exam_url = "https://www.q-net.or.kr/crf005.do?" + urlencode(
                {"id": "crf00503s02", **base_params, "jmInfoDivCcd": "B0", "seriesCd": "04"}
            )
            _, basic_payload = self._fetch_text(basic_url)
            _, exam_payload = self._fetch_text(exam_url)

            issuing_organization = (
                _section_text(basic_payload, "실시기관명")
                or _section_text(basic_payload, "시행기관")
            )
            duties = _section_text(basic_payload, "개요") or _section_text(basic_payload, "수행직무")
            eligibility = (
                _section_text(basic_payload, "응시자격")
                or _section_text(exam_payload, "응시자격")
            )
            acquisition_method = (
                _section_text(exam_payload, "취득방법")
                or _section_text(exam_payload, "시험정보")
            )
            exam_schedule = _plain_section(
                exam_payload,
                "시험일정",
                ("검정형 자격 시험정보", "시험정보", "수수료", "출제경향", "취득방법"),
            ) or _plain_section(
                exam_payload,
                "검정형 자격 시험일정",
                ("검정형 자격 시험정보", "시험정보", "수수료", "출제경향", "취득방법"),
            )
            fees = _plain_section(
                exam_payload,
                "수수료",
                ("출제경향", "공개문제", "취득방법", "출제기준"),
                max_length=500,
            )
            qualification_status = (
                _section_text(basic_payload, "시행상태")
                or _section_text(basic_payload, "자격상태")
            )
            return QualificationEvidence(
                normalized_name=normalized_name,
                official_name=normalized_name,
                status=qualification_status,
                issuing_organization=issuing_organization,
                duties=duties,
                eligibility=eligibility,
                acquisition_method=acquisition_method,
                exam_schedule=exam_schedule,
                fees=fees,
                exam_information=acquisition_method,
                source_url=validate_qnet_url(detail_url),
                checked_at=checked_at,
                fetch_status="SUCCESS",
            )
        except (RuntimeError, ValueError) as exc:
            return QualificationEvidence(
                normalized_name=normalized_name,
                source_url=qnet_url,
                checked_at=checked_at,
                fetch_status="UNAVAILABLE",
                error=str(exc),
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
            try:
                cached = self.cache.get(name)
            except Exception:
                cached = None
            if cached is not None:
                return cached
        evidence = self.web_tool.fetch_qualification(name, qnet_url)
        if evidence.fetch_status in {"SUCCESS", "NAME_MISMATCH", "UNAVAILABLE"}:
            try:
                self.cache.put(evidence, int(time.time()) + self.ttl_seconds)
            except Exception as exc:
                message = f"Qualification cache write failed: {type(exc).__name__}"
                error = f"{evidence.error}; {message}" if evidence.error else message
                evidence = evidence.model_copy(update={"error": error})
        return evidence
