"""Property 1 - 멤버 출처(provenance) 강제.

**Validates: Requirements 7.2, 1.6**

Property (design.md → "Correctness Properties" → Property 1):
    임의의 Agent 출력과 검증 컨텍스트에 대해, 어떤 추천안의 ``member_ids`` 에
    ``candidates`` 와 ``fixed_members`` 어디에도 존재하지 않는 ``worker_id`` 가 하나라도
    포함되면, 검증기는 그 출력을 반드시 거부한다.

Test pattern (design.md → "Testing Strategy" → "검증기 테스트 패턴"):
    유효 출력 생성(``valid_scenarios`` — Property 8 건전성 기준선) → 정확히 하나의
    ``member_id`` 를 후보/고정 어디에도 없는 미지(unknown) id 로 변형(mutation) → 검증기가
    반드시 거부하고, 특히 ``member_exists`` 검사가 실패하는지 확인한다. 이로써 거부가
    출처(provenance) 위반 때문임을 보장한다(부수 효과가 아니라 provenance 규칙 자체).

The seven-check baseline (``Scenario.output`` paired with ``Scenario.ctx``) is guaranteed
to pass ``validate_output`` unmodified; this test asserts that baseline first, then mutates
exactly one facet so the *only* rule broken is member provenance. Because an unknown id has
no trade/wage in the context, ``trade_headcount`` / ``total_cost`` may also fail as a side
effect — that is acceptable; the load-bearing assertions are that the output is rejected AND
that ``member_exists`` is among the failed checks.

Uses Hypothesis with ``@settings(max_examples=100)`` (>= 100 iterations).
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from functions.agent_invoke.validator import CHECK_MEMBER_EXISTS, validate_output
from tests.strategies import valid_scenarios

# Feature: crew-composition-agent, Property 1: 멤버 출처 강제


@pytest.mark.property
@settings(max_examples=100)
@given(scenario=valid_scenarios(), data=st.data())
def test_unknown_member_id_is_rejected(scenario, data) -> None:
    """Mutating one member_id to an id absent from candidates+fixed_members is rejected.

    Steps:
      1. Confirm the generated scenario is the Property-8 soundness baseline (unmutated,
         it passes all seven checks) so the rejection below is caused solely by the
         provenance mutation.
      2. Replace exactly one member_id in one recommendation with a freshly minted id that
         is guaranteed absent from both candidates and fixed_members.
      3. Assert the validator rejects the output and that the ``member_exists`` check
         specifically failed (Req 7.2, 1.6).
    """
    output = scenario.output
    ctx = scenario.ctx

    # (1) Baseline must be valid: the mutation is the sole cause of the rejection below.
    assert validate_output(output, ctx).valid is True

    # Provenance set: every worker_id the validator accepts as "known".
    known = {c.worker_id for c in ctx.candidates} | {f.worker_id for f in ctx.fixed_members}

    # (2) Pick exactly one recommendation and one member position to corrupt.
    rec_index = data.draw(
        st.integers(min_value=0, max_value=len(output.recommendations) - 1),
        label="rec_index",
    )
    target_rec = output.recommendations[rec_index]
    member_index = data.draw(
        st.integers(min_value=0, max_value=len(target_rec.member_ids) - 1),
        label="member_index",
    )

    # Mint an id guaranteed absent from candidates AND fixed_members. Because every
    # member_id in a valid scenario comes from that known set, this id is also absent from
    # the ids already used in the output, so the mutation cannot trip the no-dup check.
    seed = data.draw(st.integers(min_value=0, max_value=10**9), label="unknown_seed")
    unknown_id = f"UNKNOWN_{seed}"
    while unknown_id in known:
        seed += 1
        unknown_id = f"UNKNOWN_{seed}"
    assert unknown_id not in known  # provenance precondition

    # Build the mutated output (deep copy so the baseline object is untouched).
    mutated_output = output.model_copy(deep=True)
    mutated_member_ids = list(target_rec.member_ids)
    mutated_member_ids[member_index] = unknown_id
    mutated_output.recommendations[rec_index] = target_rec.model_copy(
        update={"member_ids": mutated_member_ids}
    )

    # (3) The validator must reject the output, and specifically the member_exists check
    # must fail - proving the rejection is due to provenance. Other checks may also fail
    # as a side effect (the unknown id has no trade/wage); that is acceptable.
    result = validate_output(mutated_output, ctx)
    assert result.valid is False
    assert CHECK_MEMBER_EXISTS in result.failed_checks()
