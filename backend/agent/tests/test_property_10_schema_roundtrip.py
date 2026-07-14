"""Property 10 - 출력 스키마 라운드트립과 비적합 거부.

**Validates: Requirements 2.5, 2.6, 2.7**

Property (design.md → "Correctness Properties" → Property 10):
    유효한 ``AgentOutput`` 값은 JSON 직렬화 후 다시 파싱하면 동등한 객체가 산출된다.
    그리고 스키마에 부합하지 않는 JSON(누락 필드·잘못된 타입·혼합 텍스트·불명 키)은
    파싱 단계에서 반드시 거부된다.

This is realised as a single property-based test per direction:
  1. Round-trip fidelity - every valid AgentOutput survives model_dump_json →
     model_validate_json unchanged (and serialization is stable/idempotent).
  2. Non-conformance rejection - JSON with a missing required field, a wrong-typed
     field, an unknown key (schemas are strict + extra="forbid"), or mixed/non-JSON
     text is rejected at parse time with a ``ValidationError``.

Reusable Hypothesis strategies live in ``tests/strategies.py`` (a plain importable
module, not collected by pytest); this file keeps only the Property-10-specific
non-conformance mutations. Uses Hypothesis with ``@settings(max_examples=...)`` >= 100.
"""
from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from agent.schemas import AgentOutput
from tests.strategies import agent_outputs

# Feature: crew-composition-agent, Property 10: 출력 스키마 라운드트립과 비적합 거부


# --------------------------------------------------------------------------- #
# Direction 1: valid output round-trips unchanged (Req 2.5, 2.6)              #
# --------------------------------------------------------------------------- #
@pytest.mark.property
@settings(max_examples=100)
@given(output=agent_outputs())
def test_valid_agent_output_json_roundtrip(output: AgentOutput) -> None:
    """A valid AgentOutput serialized to JSON and re-parsed yields an equal object.

    Covers Korean text, special/escape characters and long strings in the
    reason/considerations fields (see ``tests.strategies.text_values``), plus the full
    ``mode``/``request_id``/``recommendations`` structure (Req 2.5, 2.6).
    """
    dumped = output.model_dump_json()
    reparsed = AgentOutput.model_validate_json(dumped)

    # Pydantic model equality compares type + all field values recursively.
    assert reparsed == output
    # Serialization is stable: re-dumping the re-parsed object reproduces the JSON.
    assert reparsed.model_dump_json() == dumped


# --------------------------------------------------------------------------- #
# Direction 2: non-conforming JSON is rejected at parse time (Req 2.7)        #
# --------------------------------------------------------------------------- #

# Non-whitespace tokens for the "mixed text" cases. Leading/trailing *whitespace* is
# tolerated by JSON parsers, so we deliberately use uppercase-letter tokens to make the
# combined payload invalid JSON regardless of position.
_MIXING_TOKENS = st.text(
    alphabet=st.characters(min_codepoint=0x41, max_codepoint=0x5A),  # A-Z
    min_size=3,
    max_size=12,
)

_CORRUPTIONS = [
    # missing required fields
    "drop_mode",
    "drop_request_id",
    "drop_recommendations",
    "rec_drop_field",
    # wrong types
    "bad_mode_type",
    "bad_recommendations_type",
    "rec_bad_total_cost_type",
    "rec_bad_member_ids_type",
    # valid type but non-conforming value (Literal violation)
    "bad_mode_value",
    # unknown keys (strict + extra="forbid")
    "extra_top_key",
    "extra_rec_key",
    # mixed / non-JSON text
    "mixed_text_prefix",
    "mixed_text_suffix",
    "plain_prose",
]

_REC_REQUIRED_FIELDS = ["rank", "member_ids", "total_cost", "reason", "considerations"]


@st.composite
def nonconforming_json(draw) -> str:
    """Produce a JSON/text payload that must NOT parse as an ``AgentOutput``.

    Starts from a valid output (with >= 1 recommendation so recommendation-level
    corruptions have a target), converts it to its plain dict/JSON form, then applies
    exactly one corruption drawn from :data:`_CORRUPTIONS`.
    """
    base = draw(agent_outputs(min_recommendations=1, max_recommendations=3))
    data = json.loads(base.model_dump_json())
    kind = draw(st.sampled_from(_CORRUPTIONS))

    if kind == "drop_mode":
        data.pop("mode")
        return json.dumps(data)
    if kind == "drop_request_id":
        data.pop("request_id")
        return json.dumps(data)
    if kind == "drop_recommendations":
        data.pop("recommendations")
        return json.dumps(data)
    if kind == "rec_drop_field":
        field = draw(st.sampled_from(_REC_REQUIRED_FIELDS))
        data["recommendations"][0].pop(field)
        return json.dumps(data)
    if kind == "bad_mode_type":
        # int where a Literal[str] is expected - rejected under strict parsing.
        data["mode"] = draw(st.integers())
        return json.dumps(data)
    if kind == "bad_recommendations_type":
        # scalar where a list is expected.
        data["recommendations"] = draw(st.one_of(st.integers(), st.text(max_size=8)))
        return json.dumps(data)
    if kind == "rec_bad_total_cost_type":
        # a string is never a valid strict int (even a numeric-looking one).
        data["recommendations"][0]["total_cost"] = draw(
            st.text(alphabet="abcXYZ가나", min_size=1, max_size=6)
        )
        return json.dumps(data)
    if kind == "rec_bad_member_ids_type":
        # string where list[str] is expected.
        data["recommendations"][0]["member_ids"] = draw(st.text(max_size=8))
        return json.dumps(data)
    if kind == "bad_mode_value":
        data["mode"] = draw(
            st.text(max_size=10).filter(lambda s: s not in ("NORMAL", "EMERGENCY"))
        )
        return json.dumps(data)
    if kind == "extra_top_key":
        data["__unexpected__"] = draw(st.integers())
        return json.dumps(data)
    if kind == "extra_rec_key":
        data["recommendations"][0]["__unexpected__"] = draw(st.integers())
        return json.dumps(data)
    if kind == "mixed_text_prefix":
        return draw(_MIXING_TOKENS) + " " + json.dumps(data)
    if kind == "mixed_text_suffix":
        return json.dumps(data) + " " + draw(_MIXING_TOKENS)
    # plain_prose: no JSON object at all.
    return draw(_MIXING_TOKENS)


@pytest.mark.property
@settings(max_examples=150)
@given(payload=nonconforming_json())
def test_nonconforming_json_is_rejected(payload: str) -> None:
    """Non-conforming JSON (missing/typo'd fields, unknown keys, mixed text) fails parse.

    Because the schemas use ``strict=True`` + ``extra="forbid"``, none of these payloads
    can be coerced into a valid ``AgentOutput``; each raises ``ValidationError`` (Req 2.7).
    """
    with pytest.raises(ValidationError):
        AgentOutput.model_validate_json(payload)
