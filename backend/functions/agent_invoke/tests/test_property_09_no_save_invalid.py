"""Property 9 - 무효 출력은 절대 저장되지 않음 (invalid output is never saved).

**Validates: Requirements 7.9, 8.1**

Property (design.md -> "Correctness Properties" -> Property 9):
    검증에 실패하는 임의의 Agent 출력에 대해, 저장 경로는 **어떤 Crew도 저장하지 않으며 어떤
    WorkRequest 상태도 PROPOSED로 전이하지 않는다(저장 호출 0회)**. 본 테스트는 EMERGENCY
    경로까지 포함해 GapEvent ``RECOMPOSING -> PROPOSED`` 종료 전이도 발생하지 않음을 확인한다.

Assertion scope (design.md Property 9 및 tasks.md Notes "Property 9 단언 범위"와 정합)
------------------------------------------------------------------------------------
이 테스트는 무효 출력에 대해 **"저장 없음 + PROPOSED 전이 없음"** 만 단언한다:
  1. ``save_crew`` 가 **한 번도 호출되지 않음** (저장 호출 0회).
  2. WorkRequest ``COMPOSING -> PROPOSED`` 전이가 **호출되지 않음**.
  3. GapEvent ``RECOMPOSING -> PROPOSED`` 전이가 **호출되지 않음**.
  4. NORMAL·EMERGENCY **양쪽 저장 경로 모두 진입하지 않음**.

**"상태전이 호출 0회" 라는 넓은 표현은 사용하지 않는다.** NORMAL 재시도 소진 롤백
(``COMPOSING -> REQUESTED``)은 정당한 전이로서 이 테스트의 금지 대상이 아니며, 롤백 검증은
6.4에서 별도로 다룬다(5.4=저장/PROPOSED 전이 부재, 6.4=롤백 검증으로 책임 분리). 따라서 여기서는
전이 recorder 를 통틀어 검사하지 않고, **target 이 PROPOSED 인 전이가 없음** 만 단언한다.

Approach - drive ``compose_flow`` directly with an injected invalid ``compose_fn``
----------------------------------------------------------------------------------
가장 직접적으로 저장 경로를 검증하기 위해 라우팅/권한/상태가드 계층을 우회하고
``compose_flow`` 를 직접 구동한다. ``compose_fn`` 을 주입해 라이브 Bedrock 호출 없이
**검증에 실패하는 출력**을 반환하게 한다. ``compose_flow`` 는 자체적으로 ``build_validation_context``
(검증 직전 최신 스냅샷)로 ``ValidationContext`` 를 조립한 뒤 ``validate_output`` 을 호출하므로,
주입 출력은 그 컨텍스트에 대해 실제로 무효여야 한다.

Guaranteeing invalidity independent of db/snapshot state
--------------------------------------------------------
``compose_flow`` 가 조립하는 컨텍스트가 무엇이든 무효가 보장되도록, 각 예시의 출력에는
**후보(candidates)·고정 멤버(fixed_members) 어디에도 없는 미지(unknown) worker id** 를 최소 한
개 심는다. ``member_exists`` 검사(Property 1 / Req 7.2)는 이 컨텍스트의 candidates/fixed_members
집합만으로 판정하므로, freshest ``get_workers`` 스냅샷 내용과 무관하게 **항상 실패**한다.
(tasks.md 5.4 지침의 "가장 견고한 무효화기: 보장된 미지 멤버 id 주입" 을 따른다.)

- 이 구성 덕분에 테스트는 **공허하지 않다**: 만약 무효화기가 잘못되어 유효 출력이 나온다면
  ``compose_flow`` 는 저장을 수행하고 ``saved_crews == []`` 단언이 실패하며, 예외도 발생하지 않아
  ``pytest.raises`` 가 실패한다.
- 다양성(diverse invalid outputs): mode(NORMAL/EMERGENCY), 추천안 개수(1..3), 미지 id 를 실은
  추천안 조합, 함께 담기는 실제 후보/고정 id, 임의의 ``total_cost``/한글·특수문자 ``reason`` 등을
  Hypothesis 로 폭넓게 생성한다.

Making the "no PROPOSED transition" assertion meaningful
--------------------------------------------------------
NORMAL 은 WorkRequest 를 ``COMPOSING`` 으로, EMERGENCY(외부 경로)는 GapEvent 를 ``RECOMPOSING``
으로 **미리 시드**해 둔다. 이렇게 하면 만약 저장 경로가 잘못해서 PROPOSED 종료 전이를 시도했다면
그 조건부 전이가 **성공**했을 것이므로(= recorder 에 target=PROPOSED 로 기록), "PROPOSED 전이
없음" 단언이 실제로 무언가를 검출할 수 있게 된다. EMERGENCY 는 종료 전이를 실제로 소유하는
**외부(external) 경로**(``path="external"``)로 구동해, 그 경로에서조차 무효 출력이 전이를 막음을 확인한다.

Per-example isolation without a function-scoped fixture
-------------------------------------------------------
``@given`` 테스트에 함수 스코프 fixture(예: ``monkeypatch``/``install_shared``)를 주입하면 예시마다
초기화되지 않아 Hypothesis 의 ``function_scoped_fixture`` 헬스체크가 발생한다. 이를 피하기 위해
예시 본문에서 ``pytest.MonkeyPatch.context()`` 로 **예시마다 새 stub 을 설치·해제**한다 -
shared 스텁(db/auth/state/response)이 ``shared.*`` 로 설치되고 컨텍스트 종료 시 자동 복원된다.

shared 헬퍼는 소비만 한다: ``backend/shared/*`` 는 담당자 A 소유이며 여기서는 in-memory 스텁
(``FakeSharedDB``)으로 대체해 side-effect(``save_crew`` / ``transition_*``)를 기록·검증한다.

Uses Hypothesis with ``@settings(max_examples=100)`` (필수 PBT - 별표 없음, 최소 100회 반복, 축소 금지).
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agent.schemas import AgentInput, AgentOutput, Recommendation
from functions.agent_invoke.handler import _FlowError, compose_flow
from functions.agent_invoke.persistence import SaveContext
from tests.mocks.shared_stubs import (
    ErrorCode,
    GapStatus,
    RequestStatus,
    install_fake_db,
)
from tests.strategies import sufficient_agent_inputs, text_values

# Feature: crew-composition-agent, Property 9: 무효 출력은 절대 저장되지 않음

# External/direct route: compose_flow (the lock owner) is the one that WOULD perform the
# EMERGENCY terminal transition on save success, so it is the meaningful path to prove that
# an invalid output prevents it. See handler._PATH_EXTERNAL.
_PATH_EXTERNAL = "external"
_OFFICE_ID = "OFFICE001"
_GAP_EVENT_ID = "GAP_TARGET"
_CURRENT_CREW_ID = "CREW_TARGET"


@st.composite
def _invalid_output_for(draw, agent_input: AgentInput) -> AgentOutput:
    """Build an ``AgentOutput`` guaranteed to FAIL ``validate_output`` for ``agent_input``.

    Guarantee: at least one recommendation carries a ``member_id`` that is absent from
    ``agent_input.candidates`` AND ``agent_input.fixed_members`` (a freshly minted
    ``UNKNOWN_*`` id). ``compose_flow`` builds its own ``ValidationContext`` from those very
    pools, so the ``member_exists`` check always fails no matter what the freshest
    ``get_workers`` snapshot contains - the output is invalid by construction.

    Diversity: recommendation count (1..3), which recommendations carry the unknown id
    (>=1), how many real candidate ids ride along, and arbitrary cost / Korean-and-special
    text fields.
    """
    known = {c.worker_id for c in agent_input.candidates} | {
        f.worker_id for f in agent_input.fixed_members
    }
    seed = draw(st.integers(min_value=0, max_value=10**9))
    unknown_id = f"UNKNOWN_{seed}"
    while unknown_id in known:  # mint an id guaranteed absent from candidates + fixed
        seed += 1
        unknown_id = f"UNKNOWN_{seed}"

    fixed_ids = [f.worker_id for f in agent_input.fixed_members]
    candidate_ids = [c.worker_id for c in agent_input.candidates]

    n_recs = draw(st.integers(min_value=1, max_value=3))
    carriers = set(
        draw(
            st.lists(
                st.integers(min_value=0, max_value=n_recs - 1),
                min_size=1,
                max_size=n_recs,
                unique=True,
            )
        )
    )

    recommendations = []
    for i in range(n_recs):
        members = list(fixed_ids)  # realistic: EMERGENCY recs keep the fixed members
        if candidate_ids:
            k = draw(st.integers(min_value=0, max_value=len(candidate_ids)))
            members.extend(draw(st.permutations(candidate_ids))[:k])
        if i in carriers:  # >=1 rec carries the provenance-violating unknown id
            members.append(unknown_id)
        recommendations.append(
            Recommendation(
                rank=i + 1,
                member_ids=members,
                total_cost=draw(st.integers(min_value=0, max_value=10**7)),
                reason=draw(text_values()),
                considerations=draw(st.lists(text_values(), max_size=3)),
            )
        )

    return AgentOutput(
        mode=agent_input.mode,
        request_id=agent_input.request.request_id,
        recommendations=recommendations,
    )


@pytest.mark.property
@pytest.mark.parametrize("mode", ["NORMAL", "EMERGENCY"])
@settings(max_examples=100)
@given(data=st.data())
def test_invalid_output_is_never_saved(mode: str, data: st.DataObject) -> None:
    """For any validation-failing output, compose_flow saves nothing and does not transition to PROPOSED.

    Steps:
      1. Draw a coherent ``AgentInput`` for ``mode`` and synthesize an output guaranteed to
         fail validation (an unknown member id violates ``member_exists``).
      2. Install fresh shared stubs and seed the entity in its pre-terminal state
         (WorkRequest=COMPOSING / GapEvent=RECOMPOSING) so a wrongful PROPOSED transition
         COULD succeed - making the "no PROPOSED transition" assertion able to detect a bug.
      3. Run ``compose_flow`` with the injected invalid ``compose_fn`` (no live Bedrock).
      4. Assert it rejects the output as ``AGENT_OUTPUT_INVALID`` AND that:
         no crew was saved (save_crew call count 0), no WorkRequest ``COMPOSING->PROPOSED``,
         and no GapEvent ``RECOMPOSING->PROPOSED`` (Req 7.9, 8.1).
    """
    agent_input = data.draw(sufficient_agent_inputs(mode=mode), label="agent_input")
    invalid_output = data.draw(_invalid_output_for(agent_input), label="invalid_output")

    def _fake_compose(_agent_input: AgentInput, *, timeout_s: float) -> AgentOutput:
        # Injected so no live Bedrock call is made; returns the guaranteed-invalid output.
        return invalid_output

    with pytest.MonkeyPatch.context() as mp:
        db = install_fake_db(mp)

        if mode == "NORMAL":
            request_id = agent_input.request.request_id
            # Seed COMPOSING so a wrongful COMPOSING->PROPOSED would succeed if attempted.
            db.add_work_request(request_id, status=RequestStatus.COMPOSING)
            save_ctx = SaveContext(
                mode="NORMAL", request_id=request_id, office_id=_OFFICE_ID
            )
            event_id = None
        else:
            event_id = _GAP_EVENT_ID
            # Seed RECOMPOSING so a wrongful RECOMPOSING->PROPOSED would succeed if attempted.
            db.add_gap_event(event_id, status=GapStatus.RECOMPOSING)
            save_ctx = SaveContext(
                mode="EMERGENCY",
                request_id=agent_input.request.request_id,
                office_id=_OFFICE_ID,
                current_crew_id=_CURRENT_CREW_ID,
                gap_event_id=event_id,
            )

        with pytest.raises(_FlowError) as excinfo:
            compose_flow(
                agent_input,
                save_ctx,
                path=_PATH_EXTERNAL,
                event_id=event_id,
                compose_fn=_fake_compose,
            )

        # Rejected specifically as an invalid output (Req 7.9 -> AGENT_OUTPUT_INVALID),
        # confirming the rejection is validation-driven (not STATE_CONFLICT/CREW_INVALID/etc.).
        assert excinfo.value.code == ErrorCode.AGENT_OUTPUT_INVALID

        # (1) No Crew saved - neither the NORMAL nor the EMERGENCY save path was entered
        #     (save_crew call count 0).
        assert db.saved_crews == []
        assert db.method_calls("save_crew") == []

        # (2) No PROPOSED terminal transition on EITHER entity. Scoped to target==PROPOSED
        #     only (NOT "zero transitions"): the NORMAL retry rollback COMPOSING->REQUESTED is
        #     a legitimate transition covered by task 6.4, not forbidden here.
        assert not any(
            t["target"] == RequestStatus.PROPOSED for t in db.status_transitions
        ), "WorkRequest COMPOSING->PROPOSED must not occur for an invalid output"
        assert not any(
            t["target"] == GapStatus.PROPOSED for t in db.gap_status_transitions
        ), "GapEvent RECOMPOSING->PROPOSED must not occur for an invalid output"
