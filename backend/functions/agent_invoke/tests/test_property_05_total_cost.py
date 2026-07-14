"""Property 5 - total_cost는 서버 계산 임금 합과 일치.

**Validates: Requirements 7.6**

Property (design.md → "Correctness Properties" → Property 5):
    임의의 추천안에 대해, ``total_cost`` 가 그 ``member_ids`` 의 ``desired_daily_wage``
    서버 계산 합과 정확히 일치할 때에만 이 검사를 통과하며, 값이 다르면 검증기는 그
    출력을 반드시 거부한다.

Test pattern (design.md → "Testing Strategy" → "검증기 테스트 패턴"):
    유효 출력 생성(``valid_scenarios`` — Property 8 건전성 기준선. 기준선에서는 모든
    추천안의 ``total_cost`` 가 그 ``member_ids`` 의 서버 계산 임금 합과 정확히 같다) →
    정확히 하나의 추천안 ``total_cost`` 를 임금 합과 다른 값으로 변형(mutation) → 검증기가
    반드시 거부하고, 특히 ``total_cost`` 검사가 실패하는지 확인한다. 이로써 거부가
    "total_cost = 서버 계산 임금 합" 규칙 때문임을 보장한다.

Isolation of the total_cost failure (핵심 — DOCUMENTED)
------------------------------------------------------
검증기의 일곱 검사 중 ``Recommendation.total_cost`` 필드를 읽는 검사는 정확히 하나뿐이다:
  * ``total_cost`` — ``rec.total_cost`` 를 서버 계산 임금 합과 비교한다.
나머지 여섯 검사(member_exists / new_ready / no_dup / trade_headcount /
no_conflict_assignment / fixed_preserved)는 ``total_cost`` 를 전혀 읽지 않고
``member_ids`` · 스냅샷 · 직종/임금 맵만 참조한다. 이 변형은 오직 하나의 추천안의
``total_cost`` 정수값만 바꾸고 ``member_ids`` 는 그대로 두므로:
  * ``total_cost`` 검사의 "임금 미상(missing wage)" 분기에 도달하지 않고
    (``member_ids`` 불변 → 모든 멤버가 여전히 ``wage_by_worker`` 에 존재),
    서버 합 비교 분기에서 불일치로 **실패**한다.
  * 다른 여섯 검사는 ``total_cost`` 를 읽지 않으므로 영향받지 않는다.
따라서 **유일하게 실패하는 검사는 ``total_cost``** 이며, 테스트는 이를 명시적으로
단언한다(격리 증명).

The seven-check baseline (``Scenario.output`` paired with ``Scenario.ctx``) is guaranteed
to pass ``validate_output`` unmodified; this test asserts that baseline first, then mutates
exactly one recommendation's ``total_cost`` by a nonzero delta so the *only* rule broken is
"total_cost equals the server-computed wage sum". Because the baseline sets total_cost to
the true wage sum, adding any nonzero delta guarantees a mismatch.

Uses Hypothesis with ``@settings(max_examples=100)`` (>= 100 iterations).
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from functions.agent_invoke.validator import CHECK_TOTAL_COST, validate_output
from tests.strategies import valid_scenarios

# Feature: crew-composition-agent, Property 5: total_cost는 서버 계산 임금 합과 일치


@pytest.mark.property
@settings(max_examples=100)
@given(scenario=valid_scenarios(), data=st.data())
def test_total_cost_mismatch_is_rejected(scenario, data) -> None:
    """Mutating one recommendation's total_cost off the server wage sum must be rejected.

    Steps:
      1. Confirm the generated scenario is the Property-8 soundness baseline (unmutated,
         it passes all seven checks) so the rejection below is caused solely by the
         total_cost mutation.
      2. Pick exactly one recommendation and perturb its ``total_cost`` by a nonzero delta
         so it no longer equals the server-computed sum of its members' desired_daily_wage
         (member_ids are left untouched, so every member still has a known wage).
      3. Assert the validator rejects the output, that ``total_cost`` specifically failed,
         and that it is the *only* failed check (Req 7.6).
    """
    output = scenario.output
    ctx = scenario.ctx

    # (1) Baseline must be valid: every recommendation's total_cost equals the server wage
    # sum in the unmutated scenario, so the mutation below is the sole cause of rejection.
    assert validate_output(output, ctx).valid is True

    # (2) Pick exactly one recommendation to corrupt.
    rec_index = data.draw(
        st.integers(min_value=0, max_value=len(output.recommendations) - 1),
        label="rec_index",
    )
    target_rec = output.recommendations[rec_index]

    # A nonzero delta guarantees the new total_cost differs from the (baseline) server sum.
    # Drawn as magnitude(>=1) * sign so it is nonzero by construction (no filtering, so no
    # wasted/aborted examples). total_cost is an unbounded int in the schema, so the
    # negative-sign case yields a representable value too.
    magnitude = data.draw(
        st.integers(min_value=1, max_value=10**6), label="cost_delta_magnitude"
    )
    sign = data.draw(st.sampled_from([-1, 1]), label="cost_delta_sign")
    delta = sign * magnitude
    mutated_total_cost = target_rec.total_cost + delta
    assert mutated_total_cost != target_rec.total_cost  # mismatch precondition

    # Build the mutated output (deep copy so the baseline object is untouched across
    # Hypothesis examples). Only the target recommendation's total_cost changes.
    mutated_output = output.model_copy(deep=True)
    mutated_output.recommendations[rec_index] = target_rec.model_copy(
        update={"total_cost": mutated_total_cost}
    )

    # (3) The validator must reject the output, and total_cost must be the failing check -
    # proving the rejection is due to the "total_cost = server wage sum" rule (Req 7.6).
    result = validate_output(mutated_output, ctx)
    assert result.valid is False
    assert CHECK_TOTAL_COST in result.failed_checks()
    # Isolation proof: total_cost is the SOLE failure. The mutation touched only one
    # recommendation's total_cost integer, leaving member_ids, snapshots, trade/headcount,
    # the conflict exemption, and fixed-member preservation entirely undisturbed - and no
    # other check reads total_cost.
    assert result.failed_checks() == [CHECK_TOTAL_COST]
