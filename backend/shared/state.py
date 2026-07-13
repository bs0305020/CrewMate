"""상태 머신 상수 및 전이 규칙 (공유 계약 1.2 / 1.3).

이 모듈은 상태 문자열을 하드코딩하지 않도록 단일 출처를 제공한다.
전이 규칙은 assignment 로직과 worker_api 상태 변경에서 사용한다.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# 아이템 타입 (단일 테이블 item_type 속성)
# ---------------------------------------------------------------------------
class ItemType:
    WORKER = "WORKER"
    WORK_REQUEST = "WORK_REQUEST"
    CREW = "CREW"
    GAP_EVENT = "GAP_EVENT"
    NOTIFICATION = "NOTIFICATION"
    COLLABORATION = "COLLABORATION"


# ---------------------------------------------------------------------------
# 근로자 상태 머신
#   INACTIVE -> READY -> RESERVED -> RUNNING -> INACTIVE
#   RESERVED -> READY (배차 취소/실패)
# ---------------------------------------------------------------------------
class WorkerState:
    INACTIVE = "INACTIVE"
    READY = "READY"
    RESERVED = "RESERVED"
    RUNNING = "RUNNING"

    ALL = frozenset({INACTIVE, READY, RESERVED, RUNNING})


# 허용된 근로자 상태 전이 (from -> {to})
WORKER_TRANSITIONS: dict[str, frozenset[str]] = {
    WorkerState.INACTIVE: frozenset({WorkerState.READY}),
    WorkerState.READY: frozenset({WorkerState.RESERVED, WorkerState.INACTIVE}),
    WorkerState.RESERVED: frozenset({WorkerState.RUNNING, WorkerState.READY}),
    WorkerState.RUNNING: frozenset({WorkerState.INACTIVE}),
}


def can_transition(current: str, target: str) -> bool:
    """근로자 상태 전이가 허용되는지 확인한다."""
    return target in WORKER_TRANSITIONS.get(current, frozenset())


# ---------------------------------------------------------------------------
# WorkRequest 상태
#   REQUESTED -> COMPOSING -> PROPOSED -> APPROVED -> RUNNING -> COMPLETED
#   (+ CANCELLED)
# ---------------------------------------------------------------------------
class RequestStatus:
    REQUESTED = "REQUESTED"
    COMPOSING = "COMPOSING"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

    ALL = frozenset(
        {REQUESTED, COMPOSING, PROPOSED, APPROVED, RUNNING, COMPLETED, CANCELLED}
    )


# ---------------------------------------------------------------------------
# Crew 상태
#   DRAFT -> PROPOSED -> APPROVED -> RUNNING -> COMPLETED (+ CANCELLED)
# ---------------------------------------------------------------------------
class CrewStatus:
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

    ALL = frozenset({DRAFT, PROPOSED, APPROVED, RUNNING, COMPLETED, CANCELLED})


# ---------------------------------------------------------------------------
# GapEvent 상태 및 유형
#   DETECTED -> RECOMPOSING -> PROPOSED -> APPROVED -> FILLED (+ FAILED)
# ---------------------------------------------------------------------------
class GapStatus:
    DETECTED = "DETECTED"
    RECOMPOSING = "RECOMPOSING"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    FILLED = "FILLED"
    FAILED = "FAILED"

    ALL = frozenset({DETECTED, RECOMPOSING, PROPOSED, APPROVED, FILLED, FAILED})


class GapType:
    NO_SHOW = "NO_SHOW"
    LEFT_SITE = "LEFT_SITE"
    UNAVAILABLE = "UNAVAILABLE"

    ALL = frozenset({NO_SHOW, LEFT_SITE, UNAVAILABLE})


# ---------------------------------------------------------------------------
# 직종(trade) enum
# ---------------------------------------------------------------------------
class Trade:
    FORMWORK = "FORMWORK"          # 형틀목공
    REBAR = "REBAR"               # 철근
    MASONRY = "MASONRY"           # 석재
    MATERIAL_CARRY = "MATERIAL_CARRY"  # 곰방
    GENERAL = "GENERAL"           # 보통인부

    ALL = frozenset({FORMWORK, REBAR, MASONRY, MATERIAL_CARRY, GENERAL})


# ---------------------------------------------------------------------------
# 사용자 역할
# ---------------------------------------------------------------------------
class Role:
    WORKER = "WORKER"
    OFFICE = "OFFICE"
    COMPANY = "COMPANY"

    ALL = frozenset({WORKER, OFFICE, COMPANY})
