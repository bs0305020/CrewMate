"""Property 8 - 검증기 건전성(soundness): 완전 준수 출력은 수용.

**Validates: Requirements 7.1**

Property (design.md → "Correctness Properties" → Property 8):
    7종 규칙(Property 1~7)을 **모두 만족하도록 구성된** 임의의 Agent 출력과 컨텍스트에
    대해, 검증기는 그 출력을 **반드시 유효로 판정**한다. (모든 것을 거부하는 퇴화
    (degenerate) 검증기를 배제하기 위한 반대 방향 보장.)

Reverse direction of the mutation tests (핵심)
----------------------------------------------
Property 1~7 (``test_property_01..07``) 는 "유효 기준선 → 한 규칙만 위반하도록
변형(mutation) → 반드시 거부" 를 검증한다. Property 8 은 그 **반대 방향**이다: **변형
없이** 완전 준수 기준선을 그대로 검증기에 넣어 **반드시 수용**되는지 확인한다. 이 두 방향
(거부/수용)이 함께 성립해야 검증기가 건전(sound)하면서도 지나치게 엄격하지 않음이
보장된다.

Anti-degeneracy (퇴화 검증기 배제)
----------------------------------
"항상 True 를 반환" 하는 검증기도 이 성질의 ``valid is True`` 단언만으로는 통과할 수
있으므로, 그것만으로는 불충분하다. 반대로 "항상 False 를 반환" 하거나 일부 검사를 조용히
건너뛰는(no-op) 검증기도 배제해야 한다. 따라서 이 테스트는 세 가지를 함께 단언한다:

  1. ``result.valid is True``                         - 완전 준수 출력은 수용된다(건전성).
  2. ``result.failed_checks() == []``                 - 7종 중 어느 것도 실패하지 않았다
                                                        (아무것도 거부하지 않는지 확인 —
                                                        허위 거부 없음).
  3. ``{c.check for c in result.checks} == set(CHECK_NAMES)`` (그리고 정확히 7개)
                                                        - 검증기가 7종 검사를 **모두 실제로
                                                        수행**했다(어느 검사도 조용히
                                                        생략/no-op 되지 않음 — 공허한
                                                        통과가 아님).

즉, "모든 것을 거부하는 퇴화 검증기" 뿐 아니라 "검사를 건너뛰어 공허하게 통과시키는
검증기" 까지 배제해, Property 8 이 의도한 건전성 보장을 온전히 확보한다.

Both modes are generated (문서화)
---------------------------------
``valid_scenarios()`` 는 ``mode`` 인자 없이 호출되어 예시마다 NORMAL 또는 EMERGENCY 를
무작위로 뽑는다(``tests/strategies._composition_plan`` 참조). 따라서 이 단일 Correctness
Property 테스트는 100 회 이상 반복하는 동안 **두 모드를 모두** 생성해 검증한다. EMERGENCY
예시는 ``fixed_members`` 와 재편성 대상 ``current_crew_id`` 를 포함하도록 구성되므로,
fixed_members 보존(Property 7)·타 배정 비충돌(Property 6) 경로까지 완전 준수 상태로 함께
수용됨을 확인한다.

The baseline (``Scenario.output`` paired with ``Scenario.ctx``) is, by construction,
guaranteed to satisfy all seven rules; this test asserts it is accepted **unmodified** -
no mutation is applied.

Uses Hypothesis with ``@settings(max_examples=100)`` (>= 100 iterations).
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings

from functions.agent_invoke.validator import CHECK_NAMES, validate_output
from tests.strategies import valid_scenarios

# Feature: crew-composition-agent, Property 8: 검증기 건전성


@pytest.mark.property
@settings(max_examples=100)
@given(scenario=valid_scenarios())
def test_fully_compliant_output_is_accepted(scenario) -> None:
    """A fully rule-compliant output paired with its context must be accepted unmodified.

    ``valid_scenarios()`` yields the seven-check "soundness" baseline in BOTH NORMAL and
    EMERGENCY modes (mode is drawn per example). No mutation is applied - this is the
    reverse direction of Property 1~7.

    Assertions (soundness + anti-degeneracy):
      1. The output is valid (accepted for storage).                       [Req 7.1]
      2. No check failed - the validator does not spuriously reject a compliant output
         (rules out an "always-reject" degenerate validator).
      3. The validator genuinely ran ALL seven named checks, exactly once each - none was
         silently skipped or no-op'd (rules out a vacuous/degenerate pass).
    """
    result = validate_output(scenario.output, scenario.ctx)

    # (1) Soundness: a fully compliant output must be judged valid.
    assert result.valid is True

    # (2) Anti-degeneracy - no false rejection: every one of the seven checks passed.
    assert result.failed_checks() == []

    # (3) Anti-degeneracy - not a vacuous pass: the validator actually evaluated all seven
    # named checks (each exactly once), so ``valid`` reflects real checking, not a skip.
    ran_checks = [c.check for c in result.checks]
    assert set(ran_checks) == set(CHECK_NAMES)
    assert len(ran_checks) == len(CHECK_NAMES)
