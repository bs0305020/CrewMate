"""Property 13 - 폴백 산출물의 유효성(model-based).

**Validates: Requirements 9.4, 9.3**

Property (design.md → "Correctness Properties" → Property 13):
    충분한 후보를 포함해 조립된 임의의 ``AgentInput`` 에 대해, ``demo_fallback`` 이
    생성한 ``AgentOutput`` 은 동일한 ``validate_output`` 검증(Property 1~7)을 **항상
    통과**한다. (Bedrock 실패·타임아웃 시에도 데모 폴백 경로가 유효한 추천을 저장하도록
    보증 — Req 9.4 / 9.3.)

Model-based, not mutation-based
-------------------------------
Property 1~7 (``test_property_01..07``) 은 "유효 기준선 → 한 규칙 위반 변형 → 거부" 를,
Property 8 (``test_property_08``) 은 그 반대(변형 없는 완전 준수 출력 수용)를 확인한다.
Property 13 은 검증기 자체가 아니라 **폴백 컴포저의 산출물**을 대상으로 한다: 실제 코드
(``demo_fallback``)가 만든 출력을, 실제 검증기(``validate_output``)의 7종 검사에 그대로
통과시키는 **모델 기반(model-based)** 보장이다. 즉 두 실 구현이 계약(Property 1~7)에서
맞물림을 확인한다.

How the ValidationContext is built for fallback output
------------------------------------------------------
검증기는 순수 함수이므로 handler/assembler 가 **검증 직전 최신 스냅샷**으로 조립해 주입하는
``ValidationContext`` 가 필요하다(design.md → "검증 직전 최신 스냅샷"). 이 테스트는 그
실제 invoke 흐름의 컨텍스트 조립을 그대로 재현한다 — ``tests/strategies._composition_plan``
이 검증기용 컨텍스트를 만드는 방식과 **동일하게** 구성한다:

  - 모든 ``candidate`` → ``WorkerStateSnapshot(state="READY", current_crew_id=None)``
    (``sufficient_agent_inputs`` 의 후보는 정의상 READY 이므로 신규 멤버 READY 재확인
    (Property 2)·타 배정 비충돌(Property 6)이 성립).
  - 모든 EMERGENCY ``fixed_member`` → ``WorkerStateSnapshot(state="RUNNING",
    current_crew_id=<재편성 대상 crew>)`` 로 모델링하고, **같은** ``current_crew_id`` 를
    컨텍스트에 실어 no-conflict 검사가 현재 crew 를 예외 처리하도록 한다(NORMAL 은 현재
    crew 가 없으므로 ``None``). fixed_members 는 검사에서 이미 예외지만 실제 흐름과
    동일하게 RUNNING@현재crew 로 스냅샷을 구성해 대칭을 유지한다.
  - ``ValidationContext.build`` 가 후보/고정 멤버 풀에서 ``trade_by_worker`` /
    ``wage_by_worker`` 를 파생하므로, 폴백이 계산한 ``total_cost`` 는 검증기의 서버 계산
    임금 합(Property 5)과 정확히 일치한다.

Sufficiency
-----------
``sufficient_agent_inputs()`` 는 요구 직종별로 (EMERGENCY 는 고정 멤버 반영 후) 필요 인원을
채우고도 남는 READY 후보와 넉넉한 예산을 보장한다(task 1.3 에서 확인). Property 13 은 후보가
**충분할 때** 의 유효성만 요구하므로 이 전략을 그대로 사용하며, 단언은 약화하지 않는다.

Uses Hypothesis with ``@settings(max_examples=100)`` (>= 100 iterations).
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings

from agent.schemas import AgentInput
from functions.agent_invoke.fallback import demo_fallback
from functions.agent_invoke.validator import (
    ValidationContext,
    WorkerStateSnapshot,
    validate_output,
)
from tests.strategies import sufficient_agent_inputs

# Feature: crew-composition-agent, Property 13: 폴백 산출물의 유효성

# Re-composition target crew id used to model EMERGENCY fixed members as RUNNING in the
# current crew, mirroring ``tests/strategies._composition_plan``. The same id is threaded
# into the ValidationContext so the no-conflict check exempts the current crew.
_CURRENT_CREW_ID = "CREW_FALLBACK"


def _build_fallback_context(agent_input: AgentInput) -> ValidationContext:
    """Assemble the freshest-snapshot ``ValidationContext`` the invoke flow uses for
    fallback output, mirroring ``tests/strategies._composition_plan``.

    Every candidate is modelled as a READY worker attached to no crew; every EMERGENCY
    fixed member as RUNNING in the re-composition target crew. The same ``current_crew_id``
    is passed to the context (``None`` for NORMAL) so the no-conflict check exempts the
    current crew. ``ValidationContext.build`` derives ``trade_by_worker`` / ``wage_by_worker``
    from the candidate + fixed-member pools.
    """
    current_crew_id = _CURRENT_CREW_ID if agent_input.mode == "EMERGENCY" else None

    worker_states = {
        cand.worker_id: WorkerStateSnapshot(
            worker_id=cand.worker_id, state="READY", current_crew_id=None
        )
        for cand in agent_input.candidates
    }
    for fm in agent_input.fixed_members:
        worker_states[fm.worker_id] = WorkerStateSnapshot(
            worker_id=fm.worker_id, state="RUNNING", current_crew_id=current_crew_id
        )

    return ValidationContext.build(
        mode=agent_input.mode,
        candidates=agent_input.candidates,
        fixed_members=agent_input.fixed_members,
        required_workers=agent_input.request.required_workers,
        worker_states=worker_states,
        current_crew_id=current_crew_id,
    )


@pytest.mark.property
@settings(max_examples=100)
@given(agent_input=sufficient_agent_inputs())
def test_fallback_output_passes_validation(agent_input: AgentInput) -> None:
    """demo_fallback always yields a validator-passing recommendation given sufficient candidates.

    Steps:
      1. Run the deterministic local composer (no LLM / Bedrock) on a sufficient AgentInput.
      2. Assemble the same freshest-snapshot ValidationContext the invoke Lambda builds
         before validation (candidates READY; EMERGENCY fixed members RUNNING@current crew).
      3. Assert the seven-check validator accepts the fallback output (Property 1~7),
         proving the demo path stays safe even when Bedrock is unavailable (Req 9.4, 9.3).
    """
    output = demo_fallback(agent_input)
    ctx = _build_fallback_context(agent_input)

    result = validate_output(output, ctx)

    assert (
        result.valid is True
    ), f"demo_fallback output failed validation; failed checks: {result.failed_checks()}"
