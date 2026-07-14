"""Property 4 - 필수 직종·인원 충족과 추천안 개수.

**Validates: Requirements 7.5, 1.4**

Property (design.md → "Correctness Properties" → Property 4):
    임의의 요청 조건(``required_workers``)과 Agent 출력에 대해, 검증기가 유효로 판정하는
    것은 (a) 추천안 개수가 1개 이상 3개 이하이고, (b) 각 추천안이 직종별 필요 인원을 정확히
    충족할 때에 한한다. 직종·인원이 미달·초과이거나 추천안이 0개 또는 4개 이상이면 반드시
    거부한다.

Two directions, both covered
----------------------------
Property 4 is bidirectional. The *accept-when-satisfied* direction ((a) ∧ (b) ⇒ valid) is
guaranteed by the Property-8 soundness baseline: ``valid_scenarios`` always emits 1..3
recommendations, each exactly satisfying the full per-trade headcount, and this test
asserts that baseline is ``valid`` before every mutation. This file then covers the
*reject-when-violated* direction with four mutation kinds, drawn per example via
``st.data()`` so a single property-based test exercises all sub-cases (design.md → "Testing
Strategy": 각 Correctness Property는 단일 속성 기반 테스트로 구현):

  * ``shortfall``            - remove one member from one recommendation → that trade is
                              short by one → headcount mismatch → reject.
  * ``excess``              - add one extra *known, READY* candidate (present in
                              ``ctx.candidates`` but not already used) to one
                              recommendation → that trade is over by one → reject.
  * ``zero_recommendations`` - replace the recommendation list with ``[]`` → count 0 is not
                              in 1..3 → reject.
  * ``four_recommendations`` - duplicate the (individually valid) recommendations to length
                              4 → count 4 is not in 1..3 → reject. Duplicating valid recs
                              keeps each one exactly satisfying the trades, isolating the
                              COUNT violation.

Test pattern (design.md → "Testing Strategy" → "검증기 테스트 패턴")
-----------------------------------------------------------------
유효 출력 생성(``valid_scenarios`` — Property 8 건전성 기준선) → 정확히 하나의 규칙(직종·인원
또는 추천안 개수)만 위반하도록 변형(mutation) → 검증기가 반드시 거부하고 ``trade_headcount``
검사가 실패하는지 확인한다.

Isolation of the trade_headcount failure (핵심 — DOCUMENTED)
-----------------------------------------------------------
The ``trade_headcount`` check reads ``rec.member_ids`` (tallied by ``ctx.trade_by_worker``)
and ``len(recommendations)``; it does not read the worker-state snapshots. Each mutation is
built so that ``trade_headcount`` is the load-bearing failure:

  * ``zero`` / ``four`` — only the recommendation *count* changes. Every retained
    recommendation still exactly satisfies the trades, so ``trade_headcount`` is the SOLE
    failing check (asserted via ``== [CHECK_TRADE_HEADCOUNT]``).
  * ``excess`` — the added worker is a known candidate of a required trade with a READY
    snapshot, and ``total_cost`` is recomputed to include it. Provenance, READY, no-dup,
    cost, conflict and fixed-preservation all still hold, so ``trade_headcount`` is again
    the SOLE failure (``== [CHECK_TRADE_HEADCOUNT]``).
  * ``shortfall`` — ``total_cost`` is recomputed for the shortened recommendation so the
    cost check stays satisfied; removing *any* member always drops one required trade below
    its headcount, so ``trade_headcount`` fails. Because a dropped member may be an
    EMERGENCY ``fixed_members`` entry, ``fixed_preserved`` can also fail as an acceptable
    side effect; this sub-case therefore asserts membership (``in failed_checks()``) rather
    than sole failure. The load-bearing guarantee is unchanged: the output is rejected AND
    ``trade_headcount`` is among the failed checks.

The seven-check baseline (``Scenario.output`` paired with ``Scenario.ctx``) is guaranteed to
pass ``validate_output`` unmodified; this test asserts that baseline first, then applies one
mutation so the rejection below is caused solely by the headcount / count violation.

Uses Hypothesis with ``@settings(max_examples=100)`` (>= 100 iterations).
"""
from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from functions.agent_invoke.validator import CHECK_TRADE_HEADCOUNT, validate_output
from tests.strategies import valid_scenarios

# Feature: crew-composition-agent, Property 4: 필수 직종·인원 충족과 추천안 개수

# The four ways to violate Property 4: (a) recommendation count outside 1..3 (zero / four),
# and (b) a recommendation whose per-trade headcount is short or over.
_MUTATION_KINDS = ["shortfall", "excess", "zero_recommendations", "four_recommendations"]


@pytest.mark.property
@settings(max_examples=100)
@given(scenario=valid_scenarios(), data=st.data())
def test_trade_headcount_and_count_violations_are_rejected(scenario, data) -> None:
    """Any per-trade headcount mismatch or out-of-range recommendation count is rejected.

    Steps:
      1. Confirm the generated scenario is the Property-8 soundness baseline (unmutated, it
         passes all seven checks and has 1..3 recommendations each exactly satisfying the
         trades) so the rejection below is caused solely by the mutation.
      2. Draw one mutation kind and apply it (see the module docstring for each branch).
      3. Assert the validator rejects the output and that ``trade_headcount`` failed
         (Req 7.5, 1.4). For the count-only and excess mutations, assert it is the SOLE
         failing check; for shortfall, assert membership (fixed_preserved may also fail).
    """
    output = scenario.output
    ctx = scenario.ctx

    # (1) Baseline must be valid: 1..3 recs, each exactly satisfying the per-trade
    # headcount. The mutation below is therefore the sole cause of the rejection.
    baseline = validate_output(output, ctx)
    assert baseline.valid is True

    kind = data.draw(st.sampled_from(_MUTATION_KINDS), label="mutation_kind")

    if kind == "shortfall":
        # (2a) SHORTFALL: drop exactly one member from one recommendation. That member's
        # trade falls one below the requirement, so trade_headcount fails. total_cost is
        # recomputed for the shortened recommendation so the cost check stays satisfied.
        rec_index = data.draw(
            st.integers(min_value=0, max_value=len(output.recommendations) - 1),
            label="shortfall_rec_index",
        )
        target = output.recommendations[rec_index]
        member_index = data.draw(
            st.integers(min_value=0, max_value=len(target.member_ids) - 1),
            label="dropped_member_index",
        )
        shortened_ids = [
            mid for j, mid in enumerate(target.member_ids) if j != member_index
        ]
        recomputed_cost = sum(ctx.wage_by_worker[m] for m in shortened_ids)
        mutated_output = output.model_copy(deep=True)
        mutated_output.recommendations[rec_index] = target.model_copy(
            update={"member_ids": shortened_ids, "total_cost": recomputed_cost}
        )

        result = validate_output(mutated_output, ctx)
        assert result.valid is False
        # fixed_preserved may also fail when the dropped member is an EMERGENCY fixed
        # member; the load-bearing assertion is that trade_headcount detected the shortfall.
        assert CHECK_TRADE_HEADCOUNT in result.failed_checks()

    elif kind == "excess":
        # (2b) EXCESS: add one known, unused candidate to one recommendation. It belongs to
        # a required trade (every generated candidate does), pushing that trade one over the
        # requirement, so trade_headcount fails. The added worker is a valid candidate with
        # a READY snapshot and a known wage, and total_cost is recomputed - so every other
        # check still passes and trade_headcount is the sole failure.
        rec_index = data.draw(
            st.integers(min_value=0, max_value=len(output.recommendations) - 1),
            label="excess_rec_index",
        )
        target = output.recommendations[rec_index]
        used_ids = set(target.member_ids)
        spare_ids = sorted(
            {c.worker_id for c in ctx.candidates if c.worker_id not in used_ids}
        )
        # A spare only exists when the scenario generated extra (unused) candidates for some
        # trade. Scenarios with no spare cannot express the "extra known candidate" mutation
        # without a provenance violation, so skip them (mirrors Property 2's ``assume``).
        assume(spare_ids)
        spare_id = data.draw(st.sampled_from(spare_ids), label="extra_candidate")
        over_ids = list(target.member_ids) + [spare_id]
        recomputed_cost = sum(ctx.wage_by_worker[m] for m in over_ids)
        mutated_output = output.model_copy(deep=True)
        mutated_output.recommendations[rec_index] = target.model_copy(
            update={"member_ids": over_ids, "total_cost": recomputed_cost}
        )

        result = validate_output(mutated_output, ctx)
        assert result.valid is False
        assert result.failed_checks() == [CHECK_TRADE_HEADCOUNT]

    elif kind == "zero_recommendations":
        # (2c) ZERO: an empty recommendation list. Count 0 is not in 1..3, so
        # trade_headcount fails on the count rule; no per-recommendation check runs, so it
        # is the sole failure.
        mutated_output = output.model_copy(update={"recommendations": []})

        result = validate_output(mutated_output, ctx)
        assert result.valid is False
        assert result.failed_checks() == [CHECK_TRADE_HEADCOUNT]

    else:  # four_recommendations
        # (2d) FOUR+: duplicate the (individually valid) recommendations up to length 4 and
        # re-rank them 1..4. Each recommendation still exactly satisfies the trades, so the
        # only violation is the count (4 not in 1..3) - isolating trade_headcount.
        base_recs = output.recommendations
        four_recs = [
            base_recs[i % len(base_recs)].model_copy(update={"rank": i + 1})
            for i in range(4)
        ]
        mutated_output = output.model_copy(update={"recommendations": four_recs})

        result = validate_output(mutated_output, ctx)
        assert result.valid is False
        assert result.failed_checks() == [CHECK_TRADE_HEADCOUNT]
