"""Property 6 - 타 RUNNING/RESERVED 배정과 비충돌.

**Validates: Requirements 7.7**

Property (design.md → "Correctness Properties" → Property 6):
    임의의 Agent 출력과 워커 상태 스냅샷에 대해, 신규 추천 멤버 중 (현재 재편성 대상 Crew를
    제외한) 다른 RUNNING 또는 RESERVED 배정에 이미 포함된 워커가 하나라도 있으면, 검증기는
    그 출력을 반드시 거부한다. EMERGENCY 의 ``fixed_members`` (현재 Crew 에서 RUNNING 유지)는
    이 검사에서 예외로 취급한다.

Test pattern (design.md → "Testing Strategy" → "검증기 테스트 패턴")
------------------------------------------------------------------
유효 출력 생성(``valid_scenarios`` — Property 8 건전성 기준선)을 기준으로 워커 상태 스냅샷만
변형(mutation)해 검증기 동작을 확인한다. 기준선에서 신규 후보 스냅샷은 READY / current_crew_id
=None 이고, EMERGENCY 의 fixed_members 스냅샷은 RUNNING@current_crew_id 다. 이 **단일**
property test 는 두 방향(branch)을 검증한다:

(A) 거부 방향 — 핵심(신규 멤버가 존재하면 항상 수행):
    신규(비 fixed) 멤버 하나의 스냅샷을 RUNNING/RESERVED 이면서 current_crew_id 를
    ``ctx.current_crew_id`` 와 **다른** crew 로 변형(타 배정 충돌)하면, 검증기는 반드시
    거부하고 ``no_conflict_assignment`` 검사가 실패한다.

(B) 예외 방향 — 강화(EMERGENCY + fixed_members 일 때만 수행):
    fixed_member 하나의 스냅샷을 RUNNING/RESERVED @ 다른 crew 로 변형해도, fixed_members 는
    충돌 검사에서 예외이므로 출력은 **여전히 유효**하다(``no_conflict_assignment`` 실패 없음).
    이로써 예외(exemption)가 실재함을 증명한다.

new_ready 와의 결합 (DOCUMENTED — 격리 불가, 의도된 결합)
--------------------------------------------------------
검증기에서 ``worker_states`` 스냅샷을 읽는 검사는 정확히 둘뿐이다: ``new_ready`` (state 를
읽음)와 ``no_conflict_assignment`` (state 와 current_crew_id 를 읽음). 타 배정 충돌은 스냅샷
상태가 RUNNING/RESERVED 임을 요구하는데, 이는 정의상 READY 가 아니다. 따라서 신규 멤버를
충돌 상태로 변형하면 ``new_ready`` 검사도 **함께** 실패한다. 이 둘은 충돌 방향에서 상호
결합되어 있어 — Property 2 가 current_crew_id 를 현재 crew 로 고정해 no_conflict 를 통과시키고
new_ready 만 격리한 것과는 반대로 — no_conflict 를 **단독** 실패로 격리할 수 없다. 그러므로
(A)에서는 ``no_conflict_assignment`` 가 실패 목록에 **포함**됨을 단언하되 단독 실패로 단언하지
않으며, 정밀성을 위해 실패 검사 집합이 정확히 {new_ready, no_conflict_assignment} (스냅샷을
읽는 두 검사)임을 단언해 나머지 다섯 검사가 교란되지 않았음을 증명한다.

The seven-check baseline (``Scenario.output`` paired with ``Scenario.ctx``) is guaranteed
to pass ``validate_output`` unmodified; this test asserts that baseline first, then mutates
exactly one snapshot per branch so the observed outcome is caused solely by that mutation.

Uses Hypothesis with ``@settings(max_examples=100)`` (>= 100 iterations).
"""
from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from functions.agent_invoke.validator import (
    CHECK_NEW_READY,
    CHECK_NO_CONFLICT_ASSIGNMENT,
    validate_output,
)
from tests.strategies import valid_scenarios

# Feature: crew-composition-agent, Property 6: 타 RUNNING/RESERVED 배정과 비충돌

# The two assignment states that constitute a conflicting RUNNING/RESERVED assignment.
# Both are, by definition, non-READY - so mutating a NEW member into one of these also
# trips the new_ready check (documented coupling; see the module docstring).
_ASSIGNMENT_STATES = ["RUNNING", "RESERVED"]


def _other_crew_id(data, current_crew_id) -> str:
    """Mint a non-None crew id guaranteed different from ``current_crew_id``.

    ``current_crew_id`` is either None (NORMAL) or ``"CREW####"`` (EMERGENCY); the
    ``"OTHER_CREW_"`` prefix cannot collide with either, so the guard loop is a defensive
    mirror of the provenance-minting pattern used in Property 1. The result is guaranteed
    ``!= current_crew_id`` (including ``!= None``), i.e. a genuinely *other* crew.
    """
    seed = data.draw(st.integers(min_value=0, max_value=10**9), label="other_crew_seed")
    other = f"OTHER_CREW_{seed}"
    while other == current_crew_id:
        seed += 1
        other = f"OTHER_CREW_{seed}"
    return other


@pytest.mark.property
@settings(max_examples=100)
@given(scenario=valid_scenarios(), data=st.data())
def test_conflicting_assignment_rejected_and_fixed_members_exempt(scenario, data) -> None:
    """A NEW member in another RUNNING/RESERVED crew is rejected; fixed members are exempt.

    Steps:
      1. Confirm the generated scenario is the Property-8 soundness baseline (unmutated,
         it passes all seven checks) so any outcome below is caused solely by the mutation.
      2. (A - core) Flip exactly one NEW member's snapshot to RUNNING/RESERVED in a
         DIFFERENT crew and assert the validator rejects the output with
         ``no_conflict_assignment`` among the failed checks (Req 7.7). ``new_ready``
         co-fails by construction (non-READY), so the failed set is exactly
         {new_ready, no_conflict_assignment}.
      3. (B - exemption, EMERGENCY only) Flip one fixed member's snapshot to
         RUNNING/RESERVED in a DIFFERENT crew and assert the output STAYS valid, proving
         fixed_members are exempt from the conflict check.
    """
    output = scenario.output
    ctx = scenario.ctx

    # (1) Baseline must be valid: the mutations below are the sole cause of any rejection.
    assert validate_output(output, ctx).valid is True

    # NEW (non-fixed) members appearing in any recommendation - the ones subject to the
    # conflict check. fixed_members are exempt by design (branch B proves it).
    fixed_ids = {f.worker_id for f in ctx.fixed_members}
    new_ids = sorted(
        {
            mid
            for rec in output.recommendations
            for mid in rec.member_ids
            if mid not in fixed_ids
        }
    )
    # In EMERGENCY a requirement may be fully covered by fixed_members (need == 0 for every
    # trade), leaving no new member to mutate for branch A; skip such scenarios.
    assume(new_ids)

    # ------------------------------------------------------------------ #
    # (A) CORE - a NEW member in another RUNNING/RESERVED crew is rejected #
    # ------------------------------------------------------------------ #
    target_new_id = data.draw(st.sampled_from(new_ids), label="target_new_member")
    conflict_state = data.draw(st.sampled_from(_ASSIGNMENT_STATES), label="conflict_state")
    conflict_crew = _other_crew_id(data, ctx.current_crew_id)
    # Conflict precondition: assigned state in a crew that is NOT the re-composition target
    # (and not None) - exactly what the no_conflict check must flag.
    assert conflict_crew != ctx.current_crew_id

    conflict_snapshot = ctx.worker_states[target_new_id].model_copy(
        update={"state": conflict_state, "current_crew_id": conflict_crew}
    )
    conflict_states = dict(ctx.worker_states)
    conflict_states[target_new_id] = conflict_snapshot
    conflict_ctx = ctx.model_copy(update={"worker_states": conflict_states})

    result = validate_output(output, conflict_ctx)
    assert result.valid is False
    # Load-bearing: the rejection is due to the conflicting other-crew assignment (Req 7.7).
    assert CHECK_NO_CONFLICT_ASSIGNMENT in result.failed_checks()
    # Documented coupling: a conflicting member is RUNNING/RESERVED, hence not READY, so
    # new_ready co-fails. These are exactly the two snapshot-reading checks; the other five
    # (member_exists / no_dup / trade_headcount / total_cost / fixed_preserved) stay
    # undisturbed - proving no_conflict_assignment fired for the right reason (not a side
    # effect) while acknowledging that no_conflict cannot be isolated as the SOLE failure.
    assert set(result.failed_checks()) == {CHECK_NEW_READY, CHECK_NO_CONFLICT_ASSIGNMENT}

    # ------------------------------------------------------------------ #
    # (B) EXEMPTION - a fixed member in another RUNNING/RESERVED crew is  #
    #     exempt, so the output stays valid (EMERGENCY + fixed only).     #
    # ------------------------------------------------------------------ #
    # fixed_members are exempt from the conflict check (they are expected to stay RUNNING),
    # so placing one in a DIFFERENT crew with an assignment state must NOT trip the check.
    # Built from the pristine ``ctx`` (branch A used a separate copy), this proves the
    # exemption is about being a fixed member - independent of the crew match.
    if ctx.mode == "EMERGENCY" and ctx.fixed_members:
        fixed_target_id = data.draw(
            st.sampled_from([f.worker_id for f in ctx.fixed_members]),
            label="fixed_target_member",
        )
        exempt_state = data.draw(st.sampled_from(_ASSIGNMENT_STATES), label="exempt_state")
        exempt_crew = _other_crew_id(data, ctx.current_crew_id)
        assert exempt_crew != ctx.current_crew_id

        exempt_snapshot = ctx.worker_states[fixed_target_id].model_copy(
            update={"state": exempt_state, "current_crew_id": exempt_crew}
        )
        exempt_states = dict(ctx.worker_states)
        exempt_states[fixed_target_id] = exempt_snapshot
        exempt_ctx = ctx.model_copy(update={"worker_states": exempt_states})

        exempt_result = validate_output(output, exempt_ctx)
        # Fixed members are exempt from the conflict check regardless of crew, so the output
        # remains fully valid and no_conflict_assignment does not fire.
        assert exempt_result.valid is True
        assert CHECK_NO_CONFLICT_ASSIGNMENT not in exempt_result.failed_checks()
