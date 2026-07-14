"""Property 7 - EMERGENCY에서 fixed_members 보존.

**Validates: Requirements 7.8, 1.5, 1.3**

Property (design.md → "Correctness Properties" → Property 7):
    mode=EMERGENCY 인 임의의 Agent 출력에 대해, 검증기가 유효로 판정하는 것은 **모든
    추천안이 모든 ``fixed_members`` 의 ``worker_id`` 를 빠짐없이 그대로 포함**할 때에
    한한다. 어떤 추천안이라도 ``fixed_members`` 를 누락하거나 치환하면 반드시 거부한다.

Test pattern (design.md → "Testing Strategy" → "검증기 테스트 패턴"):
    유효 출력 생성(``valid_scenarios(mode="EMERGENCY")`` — Property 8 건전성 기준선. 기준선
    에서는 모든 추천안이 모든 fixed_members 를 포함한다) → 정확히 하나의 추천안에서
    fixed_members 중 하나를 **제거(drop)** 하도록 변형(mutation) → 검증기가 반드시 거부하고,
    특히 ``fixed_preserved`` 검사가 실패하는지 확인한다. 이로써 거부가 "EMERGENCY 에서
    fixed_members 보존" 규칙 때문임을 보장한다.

Why EMERGENCY is forced (핵심)
------------------------------
``fixed_preserved`` 검사는 **EMERGENCY 에서만** 적용된다(NORMAL 은 자동 통과, N/A). 그리고
보존을 위반하려면 애초에 보존할 ``fixed_members`` 가 존재해야 한다. 따라서 이 테스트는
``valid_scenarios(mode="EMERGENCY")`` 로 EMERGENCY 모드를 강제한다. 다만 EMERGENCY 시나리오
라도 각 직종의 고정 인원이 0 으로 뽑히면 ``fixed_members`` 가 비어 제거할 대상이 없을 수
있으므로, ``assume(scenario.fixed_members)`` 로 최소 한 명의 고정 팀원이 있는 예시만 사용
한다(대상이 없는 예시는 오도(mis-target)하지 않고 건너뛴다).

변형(drop) 방식과 동반 실패 (DOCUMENTED)
----------------------------------------
고정 팀원을 추천안에서 **제거**하면 그 추천안은 해당 fixed 멤버를 누락하므로
``fixed_preserved`` 가 실패한다. 동시에 그 직종의 인원이 하나 줄고(``trade_headcount``),
``total_cost`` 는 갱신하지 않으므로 남은 멤버 임금 합과 어긋나(``total_cost``) 함께 실패할 수
있다 — 이는 순수 drop 변형의 불가피한 부수 효과로 **허용**된다. 치환(replace) 대신 제거를
택하는 이유는, 미지 id 로 치환하면 ``member_exists`` 까지 걸려 초점이 흐려지기 때문이다.
따라서 이 테스트의 핵심(load-bearing) 단언은 "출력이 거부되고 ``fixed_preserved`` 가 실패한
검사에 **포함**된다"이며(유일 실패로 요구하지 않음), 이로써 거부가 fixed_members 보존
위반에서 비롯됨을 보장한다.

The seven-check baseline (``Scenario.output`` paired with ``Scenario.ctx``) is guaranteed
to pass ``validate_output`` unmodified; this test asserts that baseline first, then drops
exactly one fixed member from one recommendation so the fixed-member preservation rule is
broken.

Uses Hypothesis with ``@settings(max_examples=100)`` (>= 100 iterations).
"""
from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from functions.agent_invoke.validator import CHECK_FIXED_PRESERVED, validate_output
from tests.strategies import valid_scenarios

# Feature: crew-composition-agent, Property 7: EMERGENCY에서 fixed_members 보존


@pytest.mark.property
@settings(max_examples=100)
@given(scenario=valid_scenarios(mode="EMERGENCY"), data=st.data())
def test_dropping_fixed_member_is_rejected(scenario, data) -> None:
    """Dropping a fixed member from one EMERGENCY recommendation must be rejected.

    Steps:
      1. Confirm the generated EMERGENCY scenario is the Property-8 soundness baseline
         (unmutated, it passes all seven checks) so the rejection below is caused solely
         by the fixed-member drop.
      2. Ensure at least one fixed member exists to drop (EMERGENCY scenarios can draw 0
         fixed members per trade, leaving none); skip such scenarios via ``assume``.
      3. Drop exactly one fixed member from exactly one recommendation's member_ids.
      4. Assert the validator rejects the output and that ``fixed_preserved`` is among the
         failed checks - proving the rejection is due to the EMERGENCY fixed-member
         preservation rule (Req 7.8, 1.5, 1.3). Other checks (trade_headcount, total_cost)
         may also fail as a documented side effect of removing a member.
    """
    output = scenario.output
    ctx = scenario.ctx

    # Precondition: this property only applies in EMERGENCY (NORMAL auto-passes / N/A).
    assert ctx.mode == "EMERGENCY"

    # (1) Baseline must be valid: every recommendation includes every fixed member in the
    # unmutated output, so the drop below is the sole cause of the rejection.
    assert validate_output(output, ctx).valid is True

    # (2) Need at least one fixed member to drop. In EMERGENCY a trade's fixed count can be
    # 0 for every trade, leaving fixed_members empty; skip those examples.
    fixed_ids = [f.worker_id for f in ctx.fixed_members]
    assume(fixed_ids)

    # (3) Pick exactly one fixed member and one recommendation to corrupt. A fixed member's
    # worker_id appears exactly once per recommendation (added once, then permuted with the
    # new candidates), so filtering it out drops precisely that one entry.
    target_fixed_id = data.draw(st.sampled_from(fixed_ids), label="target_fixed_id")
    rec_index = data.draw(
        st.integers(min_value=0, max_value=len(output.recommendations) - 1),
        label="rec_index",
    )
    target_rec = output.recommendations[rec_index]

    # Build the mutated output (deep copy so the baseline object is untouched across
    # Hypothesis examples). Drop the target fixed member from the chosen recommendation.
    mutated_output = output.model_copy(deep=True)
    dropped_member_ids = [m for m in target_rec.member_ids if m != target_fixed_id]
    # Sanity: the drop actually removed the fixed member from this recommendation.
    assert target_fixed_id in target_rec.member_ids
    assert target_fixed_id not in dropped_member_ids
    mutated_output.recommendations[rec_index] = target_rec.model_copy(
        update={"member_ids": dropped_member_ids}
    )

    # (4) The validator must reject the output, and specifically the fixed_preserved check
    # must fail - proving the rejection is due to EMERGENCY fixed-member preservation.
    # trade_headcount / total_cost may also fail as a side effect of removing a member;
    # that is acceptable, so we assert membership (not sole failure).
    result = validate_output(mutated_output, ctx)
    assert result.valid is False
    assert CHECK_FIXED_PRESERVED in result.failed_checks()
