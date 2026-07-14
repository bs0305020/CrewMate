"""Property 12 - 결원 계산: 직종별 부족분과 커버 보장.

**Validates: Requirements 10.5**

Property (design.md -> "Correctness Properties" -> Property 12):
    임의의 요구 직종/인원(``required_workers``)과 잔여 ``fixed_members``에 대해,
    ``compute_missing``이 산출하는 직종별 결원은 ``max(0, 요구 인원 - 잔여 보유 인원)``과
    같으며, 잔여 팀원과 결원 인원을 합치면 모든 직종의 요구 인원을 정확히 충족한다.

Function under test
-------------------
``functions.gap_event.gap_logic.compute_missing(required_workers, fixed_members)``
returns, per required trade, the shortage ``max(0, required - fixed_cover)`` where
``fixed_cover`` is the count of retained fixed members of that trade. Repeated trade
entries in ``required_workers`` are aggregated by summing their counts first.

Semantics this test mirrors (from ``gap_logic.py`` docstring - DOCUMENTED CONTRACT)
----------------------------------------------------------------------------------
The test asserts against the module's *capped-coverage* contract exactly, not against a
naive "remaining + missing == required" that would break under over-coverage:

  * ``TradeRequirement.count`` is constrained ``> 0`` (``Field(gt=0)``), so a zero
    shortage cannot be represented. ``compute_missing`` therefore **omits** any fully
    covered (``fixed_cover == required``) or over-covered (``fixed_cover > required``)
    trade from its result. Absence from the result means "shortage 0".
  * The coverage invariant is expressed against the *capped* fixed coverage: for every
    required trade, ``min(fixed_cover, required) + missing == required`` (with ``missing``
    taken as 0 when the trade is absent from the result). This holds in every regime:
      - under-covered: ``fixed_cover + (required - fixed_cover) == required``;
      - exactly/over-covered: ``required + 0 == required`` (excess ignored - a crew is
        never asked to shed workers just because a trade is over-staffed).
  * Fixed members of trades that are **not required** are counted per-trade but never
    looked up against any requirement, so they reduce no shortage and never appear in the
    result.

Test pattern (design.md -> "Testing Strategy")
----------------------------------------------
A single property-based test drives ``compute_missing`` over a coherent scenario and
recomputes the aggregated ``required[t]`` and ``fixed_cover[t]`` **locally** (mirroring
``_aggregate_required`` / ``_count_by_trade``) to check five facets in one shot:

  1. Shortfall equality: missing[t] (0 if absent) == ``max(0, required[t] - cover[t])``.
  2. Capped coverage guarantee: ``min(cover[t], required[t]) + missing[t] == required[t]``.
  3. Every returned entry has ``count > 0`` and a trade in the required set.
  4. No duplicate trades in the returned missing list.
  5. Fixed members of non-required trades never appear in - and never perturb - the result.

Generator design
----------------
Draws a set of unique required trades, then builds ``required_workers`` that may repeat a
trade across multiple entries (to exercise aggregation), and a ``fixed_members`` list that
covers each required trade to a varying degree (0 .. total+2, i.e. under / exact / over)
plus a sprinkle of fixed members of trades that are NOT required (which must not reduce any
shortage). Uses Hypothesis with ``@settings(max_examples=100)`` (>= 100 iterations).
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agent.schemas import FixedMember, TradeRequirement
from functions.gap_event.gap_logic import compute_missing
from tests.strategies import TRADE_NAMES, trade_names, wages

# Feature: crew-composition-agent, Property 12: 결원 계산 부족분·커버


@st.composite
def _missing_scenarios(draw):
    """Draw a coherent (required_workers, fixed_members) pair for the shortage property.

    - ``required_workers`` uses a set of unique required trades, but each trade may be
      split across 1..3 entries (each ``count`` >= 1) so the aggregation-by-summing path is
      exercised; the entries are shuffled so repeats are not adjacent.
    - ``fixed_members`` covers each required trade with ``0 .. aggregated_required + 2``
      members (spanning the under- / exact- / over-covered regimes) and optionally adds a
      few members of trades that are NOT required at all.
    """
    required_trades = draw(
        st.lists(trade_names(), min_size=1, max_size=4, unique=True)
    )

    # required_workers: possibly several entries per trade (aggregation coverage).
    required_workers = []
    for trade in required_trades:
        n_entries = draw(st.integers(min_value=1, max_value=3))
        for _ in range(n_entries):
            required_workers.append(
                TradeRequirement(
                    trade=trade, count=draw(st.integers(min_value=1, max_value=5))
                )
            )
    required_workers = list(draw(st.permutations(required_workers)))

    # aggregated requirement per trade (mirrors gap_logic._aggregate_required).
    aggregated_required = {}
    for req in required_workers:
        aggregated_required[req.trade] = aggregated_required.get(req.trade, 0) + req.count

    counter = {"n": 0}

    def _mint() -> str:
        counter["n"] += 1
        return f"FIX{counter['n']:04d}"

    # Fixed members covering the required trades: 0..total+2 spans under/exact/over-cover.
    fixed_members_list = []
    for trade in required_trades:
        total_req = aggregated_required[trade]
        cover = draw(st.integers(min_value=0, max_value=total_req + 2))
        for _ in range(cover):
            fixed_members_list.append(
                FixedMember(worker_id=_mint(), trade=trade, desired_daily_wage=draw(wages()))
            )

    # Fixed members of trades that are NOT required - must not reduce any shortage.
    non_required = [t for t in TRADE_NAMES if t not in set(required_trades)]
    if non_required:
        for _ in range(draw(st.integers(min_value=0, max_value=3))):
            fixed_members_list.append(
                FixedMember(
                    worker_id=_mint(),
                    trade=draw(st.sampled_from(non_required)),
                    desired_daily_wage=draw(wages()),
                )
            )

    fixed_members_list = list(draw(st.permutations(fixed_members_list)))
    return required_workers, fixed_members_list


@pytest.mark.property
@settings(max_examples=100)
@given(scenario=_missing_scenarios())
def test_missing_equals_shortfall_and_coverage_is_guaranteed(scenario) -> None:
    """Per-trade shortage == max(0, required - cover), with capped coverage exact.

    Recomputes the aggregated requirement and fixed coverage locally, then checks the five
    facets of Property 12 (see module docstring) against ``compute_missing``'s output.
    """
    required_workers, fixed_members_list = scenario

    missing = compute_missing(required_workers, fixed_members_list)

    # Local mirrors of gap_logic._aggregate_required / _count_by_trade.
    required_by_trade = {}
    for req in required_workers:
        required_by_trade[req.trade] = required_by_trade.get(req.trade, 0) + req.count

    fixed_cover_by_trade = {}
    for member in fixed_members_list:
        fixed_cover_by_trade[member.trade] = fixed_cover_by_trade.get(member.trade, 0) + 1

    missing_by_trade = {tr.trade: tr.count for tr in missing}
    required_trade_set = set(required_by_trade)

    # (4) No duplicate trades in the returned list.
    returned_trades = [tr.trade for tr in missing]
    assert len(returned_trades) == len(set(returned_trades))

    # (3) Every returned entry is a positive shortage for a required trade.
    for tr in missing:
        assert tr.count > 0
        assert tr.trade in required_trade_set

    # (1) + (2): per required trade, shortfall equality and capped-coverage guarantee.
    for trade, required_count in required_by_trade.items():
        cover = fixed_cover_by_trade.get(trade, 0)
        expected_missing = max(0, required_count - cover)
        actual_missing = missing_by_trade.get(trade, 0)  # absent => shortage 0

        # (1) 직종별 결원 = max(0, 요구 - 잔여).
        assert actual_missing == expected_missing
        # (2) 잔여(커버, 요구 상한) + 결원 = 요구 인원 정확 충족.
        assert min(cover, required_count) + actual_missing == required_count

    # (5) Fixed members of NON-required trades never appear in the missing list. Combined
    # with (1) - whose per-trade cover counts only that trade's own fixed members - this
    # confirms non-required fixed members neither surface nor perturb any required trade.
    non_required_fixed_trades = {
        member.trade for member in fixed_members_list if member.trade not in required_trade_set
    }
    for trade in non_required_fixed_trades:
        assert trade not in missing_by_trade
