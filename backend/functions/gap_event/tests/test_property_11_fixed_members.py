"""Property 11 - 결원 계산: fixed_members = 활성 - 이탈, 비변경.

**Validates: Requirements 10.3, 10.4**

Property (design.md -> "Correctness Properties" -> Property 11):
    임의의 Crew 활성 멤버 목록과 이탈자 집합에 대해, ``compute_fixed_members`` 는
      (a) 활성 멤버에서 이탈자를 제외한 집합을 **정확히**(순서 보존) 반환하고,
      (b) 이탈자를 하나도 포함하지 않으며,
      (c) 입력 멤버 객체(및 그 ``state``)를 **변경하지 않는다**.

즉 ``compute_fixed_members`` 는 순수한 "worker_id 기준 차집합"이며, 잔여 팀원을
:class:`~agent.schemas.FixedMember` (worker_id / trade / desired_daily_wage)로 변환해
반환하되 어떤 입력도 mutate 하지 않는다(Req 10.3 이탈자 제외 목록은 계산만, 워커 state
비변경 / Req 10.4 잔여 팀원 = fixed_members, RUNNING 유지).

Test pattern (design.md -> "Testing Strategy")
----------------------------------------------
경계(빈 이탈 집합, 전원 이탈, 활성에 없는 미지 이탈 id, 부분 이탈)를 포함하도록 생성기를
구성한다. 활성 멤버는 worker_id 가 **유일**하도록 ``unique_by`` 로 생성하고, 이탈 집합은
활성 id 의 임의 부분집합 + 활성에 존재하지 않는 유령(ghost) id 를 섞어 만든다.

Load-bearing 단언
-----------------
1. 결과 worker_id 열 == 이탈에 없는 활성 멤버의 worker_id 열(집합 차이 + 순서 보존).
2. 결과에 이탈 id 가 하나도 등장하지 않음.
3. 입력 비변경: 호출 전 스냅샷(worker_id/trade/desired_daily_wage/state)과 호출 후가 동일하고,
   같은 Member 객체가 원래 state 를 그대로 보유하며, 입력 리스트의 객체 동일성·순서가 보존됨.
4. 각 결과 :class:`FixedMember` 의 worker_id/trade/desired_daily_wage 가 대응하는 잔여 입력
   :class:`Member` 와 일치.

Uses Hypothesis with ``@settings(max_examples=100)`` (>= 100 iterations).
Python 3.9: ``from __future__ import annotations`` keeps annotations lazy.
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agent.schemas import FixedMember
from functions.gap_event.gap_logic import Member, compute_fixed_members
from tests.strategies import trade_names, wages, worker_ids

# Feature: crew-composition-agent, Property 11: 결원 계산 fixed_members

# Member.state 는 gap 로직이 판단에 사용하지 않고(비변경 대상) 문맥으로만 보유하는 값이다
# (design.md -> "Data Models" -> "GapEvent 처리 모델": "RUNNING(정상)/이탈 대상 등").
# 상태 값과 무관하게 보존되어야 하므로 정상/이탈 유형을 두루 섞어 뽑는다.
_MEMBER_STATES = [
    "RUNNING",
    "READY",
    "RESERVED",
    "INACTIVE",
    "NO_SHOW",
    "LEFT_SITE",
    "UNAVAILABLE",
]


def _members() -> st.SearchStrategy[Member]:
    """A single active :class:`Member` with a positive wage and an arbitrary state."""
    return st.builds(
        Member,
        worker_id=worker_ids(),
        trade=trade_names(),
        desired_daily_wage=wages(),  # always > 0, satisfies Member's Field(gt=0)
        state=st.sampled_from(_MEMBER_STATES),
    )


@st.composite
def _members_and_departed(draw):
    """Draw (active_members, departed_ids) covering the Property-11 edge cases.

    - ``active_members``: list of Members with GLOBALLY UNIQUE worker_ids (``unique_by``).
    - ``departed_ids``: a mix of
        * a random (possibly empty, possibly full) subset of the active worker_ids, and
        * zero or more ghost ids that are NOT present in active_members.
      The order is shuffled so the function's order-preservation cannot be accidentally
      satisfied by positional coupling. This naturally spans the empty-departed,
      all-departed, partial, and unknown-id regimes across 100+ examples.
    """
    active = draw(
        st.lists(_members(), unique_by=lambda m: m.worker_id, max_size=8)
    )
    active_ids = [m.worker_id for m in active]

    # A subset of active ids marked as departed (unique; may be [] or the whole set).
    if active_ids:
        departed_from_active = draw(
            st.lists(
                st.sampled_from(active_ids),
                unique=True,
                max_size=len(active_ids),
            )
        )
    else:
        departed_from_active = []

    # Ghost ids that are guaranteed absent from active_members (departed_ids may reference
    # ids that are not in the active list - those must simply not appear in the result).
    active_id_set = set(active_ids)
    n_ghost = draw(st.integers(min_value=0, max_value=3))
    ghosts: list = []
    counter = 0
    for _ in range(n_ghost):
        ghost = f"GHOST_{counter:04d}"
        while ghost in active_id_set or ghost in ghosts:
            counter += 1
            ghost = f"GHOST_{counter:04d}"
        ghosts.append(ghost)
        counter += 1

    departed = draw(st.permutations(departed_from_active + ghosts))
    return active, list(departed)


@pytest.mark.property
@settings(max_examples=100)
@given(payload=_members_and_departed())
def test_fixed_members_is_active_minus_departed_without_mutation(payload) -> None:
    """compute_fixed_members returns active-minus-departed and never mutates its input.

    Steps:
      1. Snapshot the input Members' fields and state BEFORE the call, and independently
         compute the expected retained (worker_id, fields) so the assertions do not depend
         on any post-call mutation.
      2. Call ``compute_fixed_members``.
      3. Assert exact set-difference-by-worker-id with order preserved (Req 10.4), that no
         departed id appears (Req 10.3/10.4), and that each returned FixedMember mirrors the
         corresponding retained input Member.
      4. Assert the input Members (fields, state, object identity, list order) are unchanged
         (Req 10.3 - the departed exclusion is computed only, worker state is not modified).
    """
    active, departed_ids = payload
    departed_set = set(departed_ids)

    # (1) Snapshots taken BEFORE the call - independent of any (dis)allowed mutation.
    before_snapshot = [
        (m.worker_id, m.trade, m.desired_daily_wage, m.state) for m in active
    ]
    before_states = [m.state for m in active]
    original_refs = list(active)  # references, to detect list reorder/replacement
    expected_retained = [m for m in active if m.worker_id not in departed_set]
    expected_ids = [m.worker_id for m in expected_retained]
    expected_fields = [
        (m.worker_id, m.trade, m.desired_daily_wage) for m in expected_retained
    ]

    # (2) Exercise the function under test.
    result = compute_fixed_members(active, departed_ids)

    # (3a) Exact set difference by worker_id, ORDER PRESERVED (Req 10.4).
    assert [fm.worker_id for fm in result] == expected_ids

    # (3b) No departed id ever appears in the result (Req 10.3 / 10.4).
    assert {fm.worker_id for fm in result}.isdisjoint(departed_set)

    # (3c) Each retained entry is a FixedMember mirroring the corresponding input Member.
    assert all(isinstance(fm, FixedMember) for fm in result)
    assert [
        (fm.worker_id, fm.trade, fm.desired_daily_wage) for fm in result
    ] == expected_fields

    # (4) INPUT NOT MUTATED: fields, per-object state, object identity, and list order all
    # unchanged. The gap logic computes the exclusion only and must not touch worker state.
    after_snapshot = [
        (m.worker_id, m.trade, m.desired_daily_wage, m.state) for m in active
    ]
    assert after_snapshot == before_snapshot
    assert [m.state for m in active] == before_states  # states preserved exactly
    assert len(active) == len(original_refs)
    assert all(a is b for a, b in zip(active, original_refs))  # same objects, same order
