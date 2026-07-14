"""Observability log unit tests (task 9.3).

_Requirements: 12.1, 12.2_

These are **example / unit tests** (plain pytest functions, no Hypothesis) for the
structured, PII-free agent log defined in
``backend/functions/agent_invoke/observability.py`` (built in task 9.1). They pin the two
guarantees of Requirement 12 (design.md -> "Data Models" -> "관측성 로그 레코드" and
"Testing Strategy" -> "로그 레코드가 필수 필드를 포함하고 PII를 포함하지 않음"):

* **12.1 — required-field completeness.** Every :class:`AgentLogRecord` built via
  :func:`build_agent_log_record` carries the full field set the requirement mandates:
  ``agent_mode``, ``agent_execution_id``, ``input_candidate_count``,
  ``recommendation_count``, ``validation_passed`` (validation success/failure),
  ``retried`` (retry occurrence) and ``saved`` (final save result), plus the design's
  companion fields ``request_id``, ``validation_failed_checks``, ``fallback_used`` and
  ``crew_id``. Verified for a NORMAL record and an EMERGENCY record, and after a JSON
  serialize round-trip.
* **12.2 — no PII.** The serialized record never contains PII keys (``name`` / ``phone`` /
  ``email`` / ``address``); the model actively REJECTS an attempt to attach one because it
  is declared ``extra="forbid"``; and the builder exposes no ``**kwargs`` through which a
  caller could smuggle a PII field in (it is keyword-only with a fixed parameter list).

The logging-emission tests assert exactly one INFO line is written, that the line is valid
JSON which round-trips back to ``record.model_dump()``, and that the emitted line is
PII-free. The explicit-logger variant (a dedicated logger + a capturing handler) is the
primary, deterministic check; a ``caplog`` variant additionally exercises the module logger
named ``LOGGER_NAME``.
"""
from __future__ import annotations

import json
import logging
import re

import pytest
from pydantic import ValidationError

from functions.agent_invoke.observability import (
    LOGGER_NAME,
    AgentLogRecord,
    build_agent_log_record,
    get_logger,
    log_agent_execution,
    new_execution_id,
)

# The complete field set the design's 관측성 로그 레코드 must carry (Req 12.1). The subset
# named explicitly by Req 12.1 is called out in ``REQUIRED_BY_REQ_12_1`` below.
EXPECTED_LOG_FIELDS = {
    "agent_execution_id",
    "agent_mode",
    "request_id",
    "input_candidate_count",
    "recommendation_count",
    "validation_passed",
    "validation_failed_checks",
    "retried",
    "fallback_used",
    "saved",
    "crew_id",
}

# The fields Req 12.1 names verbatim: mode, execution id, candidate count, recommendation
# count, validation success/failure, retry occurrence, final save result.
REQUIRED_BY_REQ_12_1 = {
    "agent_mode",
    "agent_execution_id",
    "input_candidate_count",
    "recommendation_count",
    "validation_passed",
    "retried",
    "saved",
}

# Personal-information keys that must NEVER appear in a structured agent log (Req 12.2).
PII_KEYS = ["name", "phone", "email", "address"]


def _normal_success_record() -> AgentLogRecord:
    """A NORMAL run that passed validation and was saved (success flags exercised).

    Uses PII-free identifiers only (``REQ-001`` / ``CREW-123``) so the serialized line
    can be asserted to contain no PII tokens.
    """
    return build_agent_log_record(
        agent_execution_id=new_execution_id(),
        agent_mode="NORMAL",
        request_id="REQ-001",
        input_candidate_count=8,
        recommendation_count=3,
        validation_passed=True,
        validation_failed_checks=[],
        retried=False,
        fallback_used=False,
        saved=True,
        crew_id="CREW-123",
    )


def _emergency_failure_record() -> AgentLogRecord:
    """An EMERGENCY run that failed validation after a retry and was NOT saved.

    Chosen to exercise the *other* value of every boolean/optional field relative to
    :func:`_normal_success_record`: ``validation_passed=False`` (with named failed checks),
    ``retried=True``, ``fallback_used=True``, ``saved=False`` and ``crew_id=None``. The
    failed-check names (``member_exists`` / ``total_cost``) are validator check names, not
    worker data.
    """
    return build_agent_log_record(
        agent_execution_id=new_execution_id(),
        agent_mode="EMERGENCY",
        request_id="GAP-EVT-77",
        input_candidate_count=5,
        recommendation_count=0,
        validation_passed=False,
        validation_failed_checks=["member_exists", "total_cost"],
        retried=True,
        fallback_used=True,
        saved=False,
        crew_id=None,
    )


class _CapturingHandler(logging.Handler):
    """A minimal handler that keeps every emitted ``LogRecord`` for inspection."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401 - stdlib contract
        self.records.append(record)


def _dedicated_logger(name: str) -> tuple[logging.Logger, _CapturingHandler]:
    """Build an isolated INFO logger (no propagation) with a capturing handler attached."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = _CapturingHandler()
    # Start from a clean slate so repeated runs don't accumulate handlers/records.
    logger.handlers = [handler]
    return logger, handler


# --------------------------------------------------------------------------- #
# 12.1 — required-field completeness (NORMAL and EMERGENCY).                    #
# --------------------------------------------------------------------------- #
def test_normal_record_contains_all_required_fields_with_correct_values():
    """A NORMAL record carries every required field, with the values it was built from."""
    record = _normal_success_record()
    dumped = record.model_dump()

    # Every field the design mandates is present — and nothing extra.
    assert set(dumped.keys()) == EXPECTED_LOG_FIELDS
    # The subset Req 12.1 names verbatim is included.
    assert REQUIRED_BY_REQ_12_1.issubset(dumped.keys())

    # Values round-trip exactly as supplied.
    assert dumped["agent_mode"] == "NORMAL"
    assert dumped["request_id"] == "REQ-001"
    assert dumped["input_candidate_count"] == 8
    assert dumped["recommendation_count"] == 3
    assert dumped["validation_passed"] is True
    assert dumped["validation_failed_checks"] == []
    assert dumped["retried"] is False
    assert dumped["fallback_used"] is False
    assert dumped["saved"] is True
    assert dumped["crew_id"] == "CREW-123"
    assert isinstance(dumped["agent_execution_id"], str) and dumped["agent_execution_id"]


def test_emergency_record_contains_all_required_fields_with_correct_values():
    """An EMERGENCY (failed, retried, not saved) record carries every required field."""
    record = _emergency_failure_record()
    dumped = record.model_dump()

    assert set(dumped.keys()) == EXPECTED_LOG_FIELDS
    assert REQUIRED_BY_REQ_12_1.issubset(dumped.keys())

    assert dumped["agent_mode"] == "EMERGENCY"
    assert dumped["request_id"] == "GAP-EVT-77"
    assert dumped["input_candidate_count"] == 5
    assert dumped["recommendation_count"] == 0
    assert dumped["validation_passed"] is False
    assert dumped["validation_failed_checks"] == ["member_exists", "total_cost"]
    assert dumped["retried"] is True
    assert dumped["fallback_used"] is True
    assert dumped["saved"] is False
    assert dumped["crew_id"] is None


def test_required_fields_survive_json_serialization_round_trip():
    """model_dump_json() exposes the same keys/values as model_dump() for both modes."""
    for record in (_normal_success_record(), _emergency_failure_record()):
        line = record.model_dump_json()
        reparsed = json.loads(line)
        assert set(reparsed.keys()) == EXPECTED_LOG_FIELDS
        assert REQUIRED_BY_REQ_12_1.issubset(reparsed.keys())
        # The serialized line is a faithful, lossless view of the record.
        assert reparsed == record.model_dump()


# --------------------------------------------------------------------------- #
# 12.2 — PII exclusion.                                                        #
# --------------------------------------------------------------------------- #
def test_serialized_record_contains_no_pii_keys():
    """Neither serialized record exposes a name / phone / email / address key."""
    for record in (_normal_success_record(), _emergency_failure_record()):
        line = record.model_dump_json()
        keys = set(json.loads(line).keys())
        for pii in PII_KEYS:
            # Not present as a structured key ...
            assert pii not in keys
            # ... and not present as a JSON key token in the raw serialized line.
            assert '"%s"' % pii not in line


def test_expected_field_set_shares_nothing_with_pii_keys():
    """The whole log schema is disjoint from the PII key set (defense in depth)."""
    assert EXPECTED_LOG_FIELDS.isdisjoint(set(PII_KEYS))


@pytest.mark.parametrize("pii_key", PII_KEYS)
def test_model_rejects_attempt_to_attach_pii_field(pii_key):
    """Attaching a PII field (extra="forbid") raises ValidationError, so PII can't leak."""
    valid_fields = dict(
        agent_execution_id=new_execution_id(),
        agent_mode="NORMAL",
        request_id="REQ-001",
        input_candidate_count=1,
        recommendation_count=1,
        validation_passed=True,
        retried=False,
        fallback_used=False,
        saved=True,
    )
    # Sanity: the valid fields alone construct fine.
    AgentLogRecord(**valid_fields)

    # Adding any PII key is rejected at construction time.
    with pytest.raises(ValidationError):
        AgentLogRecord(**valid_fields, **{pii_key: "should-be-rejected"})


def test_builder_has_no_kwargs_channel_to_inject_pii():
    """build_agent_log_record is keyword-only with a fixed param list (no **kwargs).

    A ``**kwargs`` passthrough would be a hole through which a PII field could reach the
    record, so its absence is part of the Req 12.2 guarantee.
    """
    import inspect

    sig = inspect.signature(build_agent_log_record)
    kinds = {p.kind for p in sig.parameters.values()}
    assert inspect.Parameter.VAR_KEYWORD not in kinds  # no **kwargs
    assert inspect.Parameter.VAR_POSITIONAL not in kinds  # no *args
    # Every declared parameter is keyword-only, and none is a PII field name.
    assert kinds == {inspect.Parameter.KEYWORD_ONLY}
    assert set(sig.parameters).isdisjoint(set(PII_KEYS))


# --------------------------------------------------------------------------- #
# 12.1 / 12.2 — logging emission (explicit logger, then module logger).        #
# --------------------------------------------------------------------------- #
def test_log_agent_execution_emits_single_json_line_that_round_trips():
    """A record logged through an explicit logger emits one INFO JSON line == the record."""
    record = _normal_success_record()
    logger, handler = _dedicated_logger("test.observability.explicit")

    returned = log_agent_execution(record, logger=logger)

    # Exactly one line, emitted at INFO.
    assert len(handler.records) == 1
    emitted = handler.records[0]
    assert emitted.levelno == logging.INFO
    message = emitted.getMessage()

    # The function returns exactly what it logged.
    assert returned == message

    # The line is valid JSON and round-trips to the record's own dump.
    assert json.loads(message) == record.model_dump()

    # And the emitted line carries no PII keys.
    for pii in PII_KEYS:
        assert '"%s"' % pii not in message


def test_log_agent_execution_uses_module_logger_by_default(caplog):
    """With no explicit logger, the line is emitted on LOGGER_NAME and round-trips."""
    record = _emergency_failure_record()

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        returned = log_agent_execution(record)

    # Find the line emitted on the module's structured logger.
    lines = [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME]
    assert lines == [returned]

    parsed = json.loads(lines[0])
    assert parsed == record.model_dump()
    for pii in PII_KEYS:
        assert pii not in parsed


def test_get_logger_returns_module_logger_named_logger_name():
    """get_logger() exposes the INFO-level module logger identified by LOGGER_NAME."""
    logger = get_logger()
    assert logger.name == LOGGER_NAME
    assert logger.isEnabledFor(logging.INFO)


# --------------------------------------------------------------------------- #
# Light sanity — execution-id uniqueness.                                      #
# --------------------------------------------------------------------------- #
def test_new_execution_id_returns_unique_nonempty_strings():
    """Two calls to new_execution_id() differ; ids are non-empty strings."""
    a = new_execution_id()
    b = new_execution_id()
    assert isinstance(a, str) and isinstance(b, str)
    assert a and b
    assert a != b
    # Many draws stay unique (no accidental constant).
    ids = {new_execution_id() for _ in range(50)}
    assert len(ids) == 50
    # UUID4 hex/hyphen shape (light structural sanity, not a strict contract).
    assert re.fullmatch(r"[0-9a-fA-F-]{36}", a)
