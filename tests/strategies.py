"""Shared Hypothesis strategies for the crew-composition-agent property tests (담당자 B).

This is a **plain importable module, not a test module** - its filename does not match
``python_files = ["test_*.py"]`` in ``pyproject.toml``, so pytest never collects it as a
test. It lives at the neutral top-level ``tests`` package so it can be imported the same
way from every test location on the workspace-root ``sys.path`` (the Lambda-Layer
substitute path):

    from tests.strategies import agent_outputs, valid_scenarios, candidates, ...

Why this module exists
----------------------
Several property-based tests across the spec need the *same* generators for the Agent I/O
schemas and, crucially, for a **rule-compliant output paired with a matching
``ValidationContext``**. Rather than duplicate that generation logic per test file, it is
centralised here.

Consumers (current and planned)
-------------------------------
- ``agent/tests/test_property_10_schema_roundtrip.py`` (task 1.3) - uses
  :func:`agent_outputs` (arbitrary structurally-valid outputs) for the JSON round-trip /
  non-conformance property (Property 10).
- ``backend/functions/agent_invoke/tests/test_property_01..08_*.py`` (tasks 3.2-3.9) -
  use :func:`valid_scenarios` to get a valid ``AgentOutput`` + matching
  ``ValidationContext`` (the "soundness" baseline, Property 8), then mutate one facet to
  violate a single rule and assert the validator rejects it (Properties 1-7).
- ``backend/functions/agent_invoke/tests/test_property_13_fallback_valid.py`` (task 6.2) -
  uses :func:`sufficient_agent_inputs` to get an ``AgentInput`` whose candidate pool can
  satisfy the requirement, so the deterministic fallback composer can produce a valid
  recommendation.
- ``backend/functions/gap_event/tests`` (tasks 8.2-8.3) - can reuse the element builders
  (:func:`trade_requirements`, :func:`fixed_members`, :func:`candidates`).

Two families of generators
---------------------------
1. **Structural** (``agent_outputs``, ``recommendations``, ``candidates`` ...): produce
   values that only satisfy the Pydantic schema. They are intentionally *not* guaranteed
   to be rule-compliant - ideal for parsing / round-trip tests.
2. **Coherent scenarios** (``valid_scenarios`` / ``sufficient_agent_inputs``): produce a
   self-consistent bundle that is guaranteed to pass all seven validator checks
   (member provenance, READY, no dup, exact trade/headcount, total_cost, no conflict,
   EMERGENCY fixed_members preserved). Use these as the "valid baseline" to mutate from.

Design references: ``design.md`` → "Data Models", "Correctness Properties", "Testing
Strategy"; ``requirements.md`` → Requirements 2 and 7.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from hypothesis import strategies as st

from agent.schemas import (
    AgentInput,
    AgentOutput,
    Candidate,
    CollaborationPair,
    FixedMember,
    Priority,
    Recommendation,
    RequestSpec,
    TradeRequirement,
)
from functions.agent_invoke.validator import (
    ValidationContext,
    WorkerStateSnapshot,
)

__all__ = [
    # text / primitive generators
    "text_values",
    "identifiers",
    "worker_ids",
    "trade_names",
    "skill_levels",
    "wages",
    "certifications",
    "career_years",
    "priorities",
    # schema element generators (structural)
    "trade_requirements",
    "candidates",
    "fixed_members",
    "collaboration_pairs",
    "request_specs",
    "recommendations",
    "agent_inputs",
    "agent_outputs",
    "worker_state_snapshots",
    # coherent, rule-compliant scenario builders
    "Scenario",
    "valid_scenarios",
    "sufficient_agent_inputs",
    # constants
    "TRADE_NAMES",
    "PRIORITY_LEVELS",
    "WORKER_STATE_VALUES",
]

# Realistic-but-bounded value pools. The trade set matches the examples in
# ``design.md`` ("FORMWORK | REBAR | MASONRY | MATERIAL_CARRY | GENERAL ...").
TRADE_NAMES = [
    "FORMWORK",
    "REBAR",
    "MASONRY",
    "MATERIAL_CARRY",
    "GENERAL",
    "PLASTERING",
    "PAINTING",
]
PRIORITY_LEVELS = ["LOW", "MEDIUM", "HIGH"]
WORKER_STATE_VALUES = ["READY", "RESERVED", "RUNNING", "INACTIVE"]


# --------------------------------------------------------------------------- #
# Text generators - Korean + special characters + long strings                #
# --------------------------------------------------------------------------- #
# A wide alphabet drawn from safe (non-surrogate) codepoint ranges so JSON
# serialization/round-trip is always well defined:
#   - printable ASCII (0x20-0x7E)
#   - Hangul syllables (0xAC00-0xD7A3) - stops below the surrogate block (0xD800+)
#   - an explicit sprinkle of whitespace, punctuation and CJK/symbol characters
_CHAR_ALPHABET = st.one_of(
    st.characters(min_codepoint=0x20, max_codepoint=0x7E),
    st.characters(min_codepoint=0xAC00, max_codepoint=0xD7A3),
    st.sampled_from("\n\t\\\"'{}[]<>:,()@#%&*+=/가나다라마바사아자차한글특수문자テスト★☆♥→↑"),
)

# Explicit edge samples the task calls for: Korean prose, mixed ASCII+Korean,
# escape-heavy strings, empty string, and deliberately long strings.
_EXPLICIT_TEXT_SAMPLES = [
    "",
    "철근 배근과 형틀 작업의 직종 균형을 고려한 팀 조합",
    "예산 범위 내에서 협업 이력이 높은 구성으로 편성",
    'special chars: "quoted", back\\slash, new\nline, tab\tchar',
    "混合 텍스트 with ascii 123 and symbols !@#$%^&*()",
    "가" * 300,  # long single-character string
    "협업 이력을 고려한 편성 사유 " * 60,  # long mixed string
]


def text_values(max_size: int = 40) -> st.SearchStrategy[str]:
    """Free-text values for ``reason`` / ``considerations`` / ``site`` fields.

    Mixes random text over the wide alphabet, the explicit Korean/special/empty edge
    samples, and an occasional long string - exercising Unicode + escaping through the
    JSON round-trip (Property 10) and the free-text fields elsewhere.
    """
    return st.one_of(
        st.text(alphabet=_CHAR_ALPHABET, max_size=max_size),
        st.sampled_from(_EXPLICIT_TEXT_SAMPLES),
        st.text(alphabet=_CHAR_ALPHABET, min_size=150, max_size=300),
    )


def identifiers() -> st.SearchStrategy[str]:
    """Id-like strings (request_id etc.), also covering unicode/edge text."""
    return st.one_of(
        st.builds(lambda n: f"REQ{n:04d}", st.integers(min_value=0, max_value=9999)),
        text_values(max_size=16),
    )


def worker_ids() -> st.SearchStrategy[str]:
    """Worker id strings - mostly ``W####`` shaped, plus arbitrary short text."""
    return st.one_of(
        st.builds(lambda n: f"W{n:04d}", st.integers(min_value=0, max_value=9999)),
        st.text(alphabet=_CHAR_ALPHABET, min_size=1, max_size=12),
    )


# --------------------------------------------------------------------------- #
# Primitive field generators                                                  #
# --------------------------------------------------------------------------- #
def trade_names() -> st.SearchStrategy[str]:
    return st.sampled_from(TRADE_NAMES)


def skill_levels() -> st.SearchStrategy[int]:
    return st.integers(min_value=1, max_value=5)


def wages() -> st.SearchStrategy[int]:
    return st.integers(min_value=80_000, max_value=400_000)


def certifications() -> st.SearchStrategy[list]:
    return st.lists(st.text(alphabet=_CHAR_ALPHABET, min_size=1, max_size=8), max_size=3)


def career_years() -> st.SearchStrategy[int]:
    return st.integers(min_value=0, max_value=40)


def priorities() -> st.SearchStrategy[Priority]:
    level = st.sampled_from(PRIORITY_LEVELS)
    return st.builds(Priority, cost=level, skill=level, teamwork=level)


# --------------------------------------------------------------------------- #
# Structural schema element generators                                        #
# --------------------------------------------------------------------------- #
def trade_requirements() -> st.SearchStrategy[TradeRequirement]:
    return st.builds(
        TradeRequirement,
        trade=trade_names(),
        count=st.integers(min_value=1, max_value=5),
    )


def candidates(
    *, trade: Optional[str] = None, worker_id: Optional[str] = None
) -> st.SearchStrategy[Candidate]:
    return st.builds(
        Candidate,
        worker_id=st.just(worker_id) if worker_id is not None else worker_ids(),
        trade=st.just(trade) if trade is not None else trade_names(),
        skill_level=skill_levels(),
        desired_daily_wage=wages(),
        certifications=certifications(),
        career_years=career_years(),
    )


def fixed_members(
    *, trade: Optional[str] = None, worker_id: Optional[str] = None
) -> st.SearchStrategy[FixedMember]:
    return st.builds(
        FixedMember,
        worker_id=st.just(worker_id) if worker_id is not None else worker_ids(),
        trade=st.just(trade) if trade is not None else trade_names(),
        desired_daily_wage=wages(),
    )


def collaboration_pairs() -> st.SearchStrategy[CollaborationPair]:
    return st.builds(
        CollaborationPair,
        worker_a=worker_ids(),
        worker_b=worker_ids(),
        count=st.integers(min_value=1, max_value=50),
    )


def request_specs() -> st.SearchStrategy[RequestSpec]:
    return st.builds(
        RequestSpec,
        request_id=identifiers(),
        required_workers=st.lists(trade_requirements(), min_size=1, max_size=3),
        budget=st.integers(min_value=1, max_value=10_000_000),
        priority=priorities(),
        site=text_values(max_size=20),
        work_date=st.sampled_from(["2025-01-01", "2025-06-15", "2024-12-31"]),
        start_time=st.sampled_from(["08:00", "09:30", "07:00"]),
    )


def recommendations() -> st.SearchStrategy[Recommendation]:
    """Structurally-valid recommendations (not necessarily rule-compliant)."""
    return st.builds(
        Recommendation,
        rank=st.integers(min_value=-100, max_value=1000),
        member_ids=st.lists(worker_ids(), min_size=0, max_size=8),
        total_cost=st.integers(min_value=-(10**6), max_value=10**9),
        reason=text_values(),
        considerations=st.lists(text_values(), max_size=4),
    )


def agent_outputs(
    min_recommendations: int = 0, max_recommendations: int = 4
) -> st.SearchStrategy[AgentOutput]:
    """Arbitrary structurally-valid :class:`AgentOutput` values.

    Ideal for the JSON round-trip / parsing property (Property 10): the values conform to
    the schema (so they serialize and re-parse) but carry the full range of edge text and
    integer values. Recommendation count spans 0..4 by default to also exercise the empty
    and over-limit shapes at the parsing layer (count limits are a validator concern, not
    a schema concern).
    """
    return st.builds(
        AgentOutput,
        mode=st.sampled_from(["NORMAL", "EMERGENCY"]),
        request_id=identifiers(),
        recommendations=st.lists(
            recommendations(),
            min_size=min_recommendations,
            max_size=max_recommendations,
        ),
    )


def agent_inputs() -> st.SearchStrategy[AgentInput]:
    """Arbitrary structurally-valid :class:`AgentInput` values."""
    return st.builds(
        AgentInput,
        mode=st.sampled_from(["NORMAL", "EMERGENCY"]),
        request=request_specs(),
        fixed_members=st.lists(fixed_members(), max_size=3),
        candidates=st.lists(candidates(), max_size=5),
        collaboration_pairs=st.lists(collaboration_pairs(), max_size=3),
    )


def worker_state_snapshots(
    *, worker_id: Optional[str] = None
) -> st.SearchStrategy[WorkerStateSnapshot]:
    return st.builds(
        WorkerStateSnapshot,
        worker_id=st.just(worker_id) if worker_id is not None else worker_ids(),
        state=st.sampled_from(WORKER_STATE_VALUES),
        current_crew_id=st.one_of(
            st.none(),
            st.builds(lambda n: f"CREW{n:04d}", st.integers(min_value=0, max_value=999)),
        ),
    )


# --------------------------------------------------------------------------- #
# Coherent, rule-compliant scenario builders                                  #
# --------------------------------------------------------------------------- #
@dataclass
class Scenario:
    """A self-consistent, rule-compliant bundle for validator property tests.

    ``output`` paired with ``ctx`` is guaranteed to pass all seven checks in
    ``validate_output`` (the Property 8 "soundness" baseline). Downstream tests mutate one
    facet (an id, a state snapshot, a count, a wage, ...) to violate exactly one rule and
    assert the validator then rejects the output.
    """

    mode: str
    required_workers: list
    candidates: list
    fixed_members: list
    worker_states: dict
    output: AgentOutput
    ctx: ValidationContext
    current_crew_id: Optional[str]


@dataclass
class _Plan:
    """Internal: the shared pool of trades/candidates/fixed members that both
    :func:`valid_scenarios` and :func:`sufficient_agent_inputs` build on."""

    mode: str
    required: list
    fixed_members: list
    candidates: list
    pool_by_trade: dict
    fixed_by_trade: dict
    current_crew_id: Optional[str]
    worker_states: dict
    wage_by_worker: dict


@st.composite
def _composition_plan(
    draw,
    *,
    mode: Optional[str] = None,
    min_trades: int = 1,
    max_trades: int = 3,
    max_count: int = 3,
    max_extra: int = 2,
) -> _Plan:
    """Draw a coherent composition plan.

    For each required trade with headcount ``count`` we (optionally, in EMERGENCY) reserve
    ``0..count`` fixed members and generate ``(count - fixed) + extra`` READY candidates,
    all with globally-unique worker ids. This guarantees the requirement can be satisfied
    exactly while leaving spare candidates to exercise the "extra candidates present but
    not chosen" path.
    """
    mode = mode if mode is not None else draw(st.sampled_from(["NORMAL", "EMERGENCY"]))
    trades = draw(
        st.lists(trade_names(), min_size=min_trades, max_size=max_trades, unique=True)
    )
    required = [
        TradeRequirement(trade=t, count=draw(st.integers(min_value=1, max_value=max_count)))
        for t in trades
    ]

    counter = {"n": 0}

    def _mint(prefix: str) -> str:
        counter["n"] += 1
        return f"{prefix}{counter['n']:04d}"

    current_crew_id = (
        f"CREW{draw(st.integers(min_value=0, max_value=9999)):04d}"
        if mode == "EMERGENCY"
        else None
    )

    fixed_by_trade: dict = {}
    fixed_members_list: list = []
    for tr in required:
        f = draw(st.integers(min_value=0, max_value=tr.count)) if mode == "EMERGENCY" else 0
        fixed_by_trade[tr.trade] = f
        for _ in range(f):
            fixed_members_list.append(
                FixedMember(
                    worker_id=_mint("FIX"),
                    trade=tr.trade,
                    desired_daily_wage=draw(wages()),
                )
            )

    pool_by_trade: dict = {}
    candidates_list: list = []
    for tr in required:
        need = tr.count - fixed_by_trade[tr.trade]
        extra = draw(st.integers(min_value=0, max_value=max_extra))
        pool: list = []
        for _ in range(need + extra):
            cand = Candidate(
                worker_id=_mint("CND"),
                trade=tr.trade,
                skill_level=draw(skill_levels()),
                desired_daily_wage=draw(wages()),
                certifications=draw(certifications()),
                career_years=draw(career_years()),
            )
            candidates_list.append(cand)
            pool.append(cand)
        pool_by_trade[tr.trade] = pool

    worker_states: dict = {}
    for cand in candidates_list:
        worker_states[cand.worker_id] = WorkerStateSnapshot(
            worker_id=cand.worker_id, state="READY", current_crew_id=None
        )
    # Fixed-member snapshots are ignored by the checks (fixed members are exempt from the
    # READY and no-conflict checks) but modelled realistically as RUNNING in the target crew.
    for fm in fixed_members_list:
        worker_states[fm.worker_id] = WorkerStateSnapshot(
            worker_id=fm.worker_id, state="RUNNING", current_crew_id=current_crew_id
        )

    wage_by_worker = {c.worker_id: c.desired_daily_wage for c in candidates_list}
    wage_by_worker.update({f.worker_id: f.desired_daily_wage for f in fixed_members_list})

    return _Plan(
        mode=mode,
        required=required,
        fixed_members=fixed_members_list,
        candidates=candidates_list,
        pool_by_trade=pool_by_trade,
        fixed_by_trade=fixed_by_trade,
        current_crew_id=current_crew_id,
        worker_states=worker_states,
        wage_by_worker=wage_by_worker,
    )


@st.composite
def valid_scenarios(
    draw,
    *,
    mode: Optional[str] = None,
    min_recs: int = 1,
    max_recs: int = 3,
    min_trades: int = 1,
    max_trades: int = 3,
    max_count: int = 3,
    max_extra: int = 2,
) -> Scenario:
    """A valid ``AgentOutput`` + matching ``ValidationContext`` (passes all 7 checks).

    Each recommendation contains every fixed member plus exactly the still-needed new
    candidates per trade, with ``total_cost`` set to the true wage sum. In EMERGENCY mode
    fixed members are present in every recommendation and the context carries the
    re-composition ``current_crew_id`` (so the no-conflict check exempts the current crew).
    """
    plan = draw(
        _composition_plan(
            mode=mode,
            min_trades=min_trades,
            max_trades=max_trades,
            max_count=max_count,
            max_extra=max_extra,
        )
    )
    fixed_ids = [f.worker_id for f in plan.fixed_members]
    n_recs = draw(st.integers(min_value=min_recs, max_value=max_recs))

    recs: list = []
    for i in range(n_recs):
        member_ids = list(fixed_ids)
        for tr in plan.required:
            need = tr.count - plan.fixed_by_trade[tr.trade]
            if need > 0:
                pool = plan.pool_by_trade[tr.trade]
                chosen = draw(st.permutations(pool))[:need]
                member_ids.extend(c.worker_id for c in chosen)
        member_ids = list(draw(st.permutations(member_ids)))
        total_cost = sum(plan.wage_by_worker[m] for m in member_ids)
        recs.append(
            Recommendation(
                rank=i + 1,
                member_ids=member_ids,
                total_cost=total_cost,
                reason=draw(text_values()),
                considerations=draw(st.lists(text_values(), max_size=3)),
            )
        )

    output = AgentOutput(
        mode=plan.mode, request_id=draw(identifiers()), recommendations=recs
    )
    ctx = ValidationContext.build(
        mode=plan.mode,
        candidates=plan.candidates,
        fixed_members=plan.fixed_members,
        required_workers=plan.required,
        worker_states=plan.worker_states,
        current_crew_id=plan.current_crew_id,
    )
    return Scenario(
        mode=plan.mode,
        required_workers=plan.required,
        candidates=plan.candidates,
        fixed_members=plan.fixed_members,
        worker_states=plan.worker_states,
        output=output,
        ctx=ctx,
        current_crew_id=plan.current_crew_id,
    )


@st.composite
def sufficient_agent_inputs(
    draw,
    *,
    mode: Optional[str] = None,
    min_trades: int = 1,
    max_trades: int = 3,
    max_count: int = 3,
    max_extra: int = 2,
) -> AgentInput:
    """An :class:`AgentInput` whose candidate pool can satisfy the requirement.

    Budget is generous so a cost-first fallback composer can fit a full crew. Suitable for
    the fallback validity property (task 6.2)."""
    plan = draw(
        _composition_plan(
            mode=mode,
            min_trades=min_trades,
            max_trades=max_trades,
            max_count=max_count,
            max_extra=max_extra,
        )
    )
    request = RequestSpec(
        request_id=draw(identifiers()),
        required_workers=plan.required,
        budget=draw(st.integers(min_value=10**6, max_value=10**8)),
        priority=draw(priorities()),
        site=draw(text_values(max_size=20)),
        work_date="2025-01-01",
        start_time="08:00",
    )
    return AgentInput(
        mode=plan.mode,
        request=request,
        fixed_members=plan.fixed_members,
        candidates=plan.candidates,
        collaboration_pairs=[],
    )
