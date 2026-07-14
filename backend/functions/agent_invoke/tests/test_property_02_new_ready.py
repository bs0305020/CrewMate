"""Property 2 - 신규 멤버는 READY 상태.

**Validates: Requirements 7.3, 1.8**

Property (design.md → "Correctness Properties" → Property 2):
    임의의 Agent 출력과 워커 상태 스냅샷에 대해, ``fixed_members`` 가 아닌 신규 추천 멤버
    중 상태가 READY 가 아닌 워커가 하나라도 있으면, 검증기는 그 출력을 반드시 거부한다.

Test pattern (design.md → "Testing Strategy" → "검증기 테스트 패턴"):
    유효 출력 생성(``valid_scenarios`` — Property 8 건전성 기준선. 기준선에서는 모든
    신규(비 fixed) 추천 멤버의 스냅샷 상태가 READY 다) → 정확히 하나의 신규 멤버 스냅샷
    상태를 비(非)READY 로 변형(mutation) → 검증기가 반드시 거부하고, 특히 ``new_ready``
    검사가 실패하는지 확인한다. 이로써 거부가 "신규 멤버 READY" 규칙 때문임을 보장한다.

Isolation of the new_ready failure (핵심 — DOCUMENTED)
-----------------------------------------------------
검증기에서 ``worker_states`` 스냅샷을 읽는 검사는 정확히 둘뿐이다:
  * ``new_ready``              — ``snapshot.state`` 를 읽는다.
  * ``no_conflict_assignment`` — ``snapshot.state`` 와 ``snapshot.current_crew_id`` 를 읽는다.
나머지 다섯 검사(member_exists / no_dup / trade_headcount / total_cost / fixed_preserved)는
``worker_states`` 를 전혀 읽지 않으므로, 스냅샷 상태만 바꾸는 이 변형은 그 다섯 검사에
영향을 주지 않는다.

따라서 신규 멤버 하나의 스냅샷 상태를 비READY 로 바꿀 때 ``new_ready`` 만 실패하도록
격리하려면 ``no_conflict_assignment`` 만 함께 관리하면 된다. 이 테스트는 변형 스냅샷의
``current_crew_id`` 를 ``ctx.current_crew_id`` 로 **고정**한다:
  * 상태가 RESERVED/RUNNING 이면 ``no_conflict_assignment`` 는
    ``current_crew_id == ctx.current_crew_id`` (현재 재편성 대상 Crew)를 예외로 취급하므로
    충돌로 잡히지 않는다.
  * 상태가 INACTIVE 이면 애초에 배정 상태(RESERVED/RUNNING)가 아니므로 충돌 검사의
    문제 분기에 도달하지 않는다.
결과적으로 세 가지 비READY 상태(RESERVED / RUNNING / INACTIVE) 모두에서 **유일하게
실패하는 검사는 ``new_ready``** 이며, 테스트는 이를 명시적으로 단언한다(격리 증명).

The seven-check baseline (``Scenario.output`` paired with ``Scenario.ctx``) is guaranteed
to pass ``validate_output`` unmodified; this test asserts that baseline first, then mutates
exactly one new member's snapshot so the *only* rule broken is "new members are READY".

Uses Hypothesis with ``@settings(max_examples=100)`` (>= 100 iterations).
"""
from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from functions.agent_invoke.validator import CHECK_NEW_READY, validate_output
from tests.strategies import valid_scenarios

# Feature: crew-composition-agent, Property 2: 신규 멤버는 READY 상태

# Non-READY worker states used for the mutation. Every value here is "not READY", so the
# new_ready check must reject each one. RESERVED/RUNNING are the assignment states the
# no_conflict check also inspects (handled via current_crew_id pinning, see docstring);
# INACTIVE is neither READY nor an assignment state.
_NON_READY_STATES = ["RESERVED", "RUNNING", "INACTIVE"]


@pytest.mark.property
@settings(max_examples=100)
@given(scenario=valid_scenarios(), data=st.data())
def test_new_member_not_ready_is_rejected(scenario, data) -> None:
    """Mutating one NEW member's snapshot to a non-READY state must be rejected.

    Steps:
      1. Confirm the generated scenario is the Property-8 soundness baseline (unmutated,
         it passes all seven checks) so the rejection below is caused solely by the
         READY-state mutation.
      2. Collect the NEW (non-fixed) recommended members. EMERGENCY scenarios can be fully
         covered by fixed_members (no new members), which are skipped via ``assume``.
      3. Flip exactly one new member's snapshot to a non-READY state, pinning
         ``current_crew_id`` to ``ctx.current_crew_id`` so the no_conflict check stays
         satisfied and ``new_ready`` is the sole failure (see the module docstring).
      4. Assert the validator rejects the output, that ``new_ready`` specifically failed,
         and that it is the *only* failed check (Req 7.3, 1.8).
    """
    output = scenario.output
    ctx = scenario.ctx

    # (1) Baseline must be valid: every new member is READY in the unmutated snapshot, so
    # the mutation below is the sole cause of the rejection.
    assert validate_output(output, ctx).valid is True

    # (2) NEW (non-fixed) members appearing in any recommendation - the candidates that
    # must be READY. fixed_members are exempt from the new_ready check by design.
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
    # trade), leaving no new member to mutate; skip such scenarios rather than mis-target.
    assume(new_ids)

    target_id = data.draw(st.sampled_from(new_ids), label="target_new_member")
    non_ready_state = data.draw(st.sampled_from(_NON_READY_STATES), label="non_ready_state")

    # (3) Mutate ONLY the target new member's snapshot to a non-READY state. Copies are
    # made so the baseline scenario objects are untouched across Hypothesis examples.
    # Pinning current_crew_id to ctx.current_crew_id keeps the no_conflict check satisfied
    # (RESERVED/RUNNING are exempt for the current re-composition crew; INACTIVE never
    # reaches the conflict branch) - isolating new_ready as the sole failing check.
    old_snapshot = ctx.worker_states[target_id]
    mutated_snapshot = old_snapshot.model_copy(
        update={"state": non_ready_state, "current_crew_id": ctx.current_crew_id}
    )
    mutated_states = dict(ctx.worker_states)
    mutated_states[target_id] = mutated_snapshot
    mutated_ctx = ctx.model_copy(update={"worker_states": mutated_states})

    # (4) The validator must reject the output, and new_ready must be the failing check -
    # proving the rejection is due to the "new members are READY" rule (Req 7.3, 1.8).
    result = validate_output(output, mutated_ctx)
    assert result.valid is False
    assert CHECK_NEW_READY in result.failed_checks()
    # Isolation proof: new_ready is the SOLE failure. The mutation touched only a
    # snapshot's state (and a pinned current_crew_id), disturbing neither provenance,
    # dedup, trade/headcount, cost, the conflict exemption, nor fixed-member preservation.
    assert result.failed_checks() == [CHECK_NEW_READY]
