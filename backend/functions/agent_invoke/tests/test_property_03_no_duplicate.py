"""Property 3 - 추천안 내 중복 멤버 금지.

**Validates: Requirements 7.4**

Property (design.md → "Correctness Properties" → Property 3):
    임의의 Agent 출력에 대해, 어떤 추천안의 ``member_ids`` 에 중복된 ``worker_id`` 가
    존재하면, 검증기는 그 출력을 반드시 거부한다.

Test pattern (design.md → "Testing Strategy" → "검증기 테스트 패턴"):
    유효 출력 생성(``valid_scenarios`` — Property 8 건전성 기준선. 기준선의 각 추천안은
    ``member_ids`` 에 중복이 없다) → 정확히 하나의 추천안에서 **이미 존재하는** ``member_id``
    를 하나 골라 그 리스트 끝에 다시 추가(append)해 중복을 주입(mutation) → 검증기가 반드시
    거부하고, 특히 ``no_dup`` 검사가 실패하는지 확인한다. 이로써 거부가 "추천안 내 중복
    금지" 규칙 때문임을 보장한다.

Why append (not replace) — DOCUMENTED
-------------------------------------
중복을 만드는 방법은 (a) 기존 member_id 를 다른 슬롯에 덮어써 치환하거나, (b) 기존
member_id 를 끝에 덧붙이는(append) 두 가지가 있다. 이 테스트는 **append** 를 택한다.
append 는 이미 알려진(후보/고정에 존재하는) id 를 그대로 재사용하므로 ``member_exists`` 는
그대로 통과하고, ``no_dup`` 이 명확히 발화한다. 다만 member_ids 가 1개 늘어나므로 그
멤버의 직종이 한 번 더 집계되고(임금도 한 번 더 합산되어) ``trade_headcount`` 와
``total_cost`` 검사가 **부수적으로** 함께 실패할 수 있다 — 이는 허용된다. 이 테스트의
load-bearing(핵심) 단언은 오직 두 가지다: (1) 출력이 거부되고(``valid is False``),
(2) 실패한 검사 목록에 ``no_dup`` 이 포함된다. 따라서 거부가 중복 때문임이 증명된다.
(치환 방식은 다른 슬롯의 멤버를 잃어 직종 미달까지 얽혀 원인 분리가 더 흐려지므로 택하지
않았다.)

The seven-check baseline (``Scenario.output`` paired with ``Scenario.ctx``) is guaranteed
to pass ``validate_output`` unmodified; this test asserts that baseline first, then injects
a single duplicate so the rejection is attributable to the duplicate.

Uses Hypothesis with ``@settings(max_examples=100)`` (>= 100 iterations).
"""
from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from functions.agent_invoke.validator import CHECK_NO_DUP, validate_output
from tests.strategies import valid_scenarios

# Feature: crew-composition-agent, Property 3: 추천안 내 중복 멤버 금지


@pytest.mark.property
@settings(max_examples=100)
@given(scenario=valid_scenarios(), data=st.data())
def test_duplicate_member_id_is_rejected(scenario, data) -> None:
    """Injecting a duplicate worker_id into one recommendation must be rejected.

    Steps:
      1. Confirm the generated scenario is the Property-8 soundness baseline (unmutated,
         it passes all seven checks) so the rejection below is caused solely by the
         duplicate injection.
      2. Pick one recommendation. Baseline recommendations always carry >= 1 member (each
         required trade has count >= 1), but guard defensively with ``assume`` so there is
         always an existing id to duplicate.
      3. Take an id already present in that recommendation and APPEND it, so ``member_ids``
         now contains that id twice (see the module docstring for why append over replace).
      4. Assert the validator rejects the output and that the ``no_dup`` check specifically
         failed (Req 7.4). ``trade_headcount`` / ``total_cost`` may also fail as a side
         effect of the extra slot; that is acceptable.
    """
    output = scenario.output
    ctx = scenario.ctx

    # (1) Baseline must be valid: the duplicate injected below is the sole cause of the
    # rejection.
    assert validate_output(output, ctx).valid is True

    # (2) Pick exactly one recommendation to corrupt.
    rec_index = data.draw(
        st.integers(min_value=0, max_value=len(output.recommendations) - 1),
        label="rec_index",
    )
    target_rec = output.recommendations[rec_index]

    # A recommendation needs >= 1 member to duplicate. Baseline recs always satisfy this
    # (required counts are >= 1); assume defensively rather than mis-target an empty list.
    assume(target_rec.member_ids)

    # (3) Choose an id that is ALREADY in the recommendation and append it, creating a
    # duplicate. Reusing an existing (known) id keeps member_exists satisfied so the
    # rejection is attributable to the duplicate, not to provenance.
    member_index = data.draw(
        st.integers(min_value=0, max_value=len(target_rec.member_ids) - 1),
        label="member_index",
    )
    duplicate_id = target_rec.member_ids[member_index]

    # Build the mutated output (deep copy so the baseline object is untouched).
    mutated_output = output.model_copy(deep=True)
    mutated_member_ids = list(target_rec.member_ids)
    mutated_member_ids.append(duplicate_id)
    mutated_output.recommendations[rec_index] = target_rec.model_copy(
        update={"member_ids": mutated_member_ids}
    )

    # (4) The validator must reject the output, and specifically the no_dup check must
    # fail - proving the rejection is due to the duplicate member_id. Other checks
    # (trade_headcount, total_cost) may also fail because the extra slot perturbs the
    # trade tally and wage sum; that is acceptable.
    result = validate_output(mutated_output, ctx)
    assert result.valid is False
    assert CHECK_NO_DUP in result.failed_checks()
