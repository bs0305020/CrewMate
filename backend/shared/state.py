"""상태 머신 상수 및 전이 규칙 (계약 v2).

이 모듈은 상태 문자열을 하드코딩하지 않도록 단일 출처를 제공한다.
전이 규칙은 assignment 로직과 worker_api 상태 변경에서 사용한다.

상태 머신 v2
-----------
Worker:   INACTIVE → READY → NOTIFIED → RESERVED → RUNNING → INACTIVE
          NOTIFIED → READY (거절/제안취소), RESERVED → READY (수락 후 취소/편성 취소)
Request:  REQUESTED → COMPOSING → PROPOSED → APPROVED → DISPATCHED → RUNNING → COMPLETED
          (+ CANCELLED, REJECTED)
Crew:     DRAFT → PROPOSED → APPROVED → NOTIFIED → DISPATCHED → RUNNING → COMPLETED (+ CANCELLED)
GapEvent: DETECTED → RECOMPOSING → PROPOSED → APPROVED → FILLED (+ FAILED)
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# 근로자 상태 머신 (v2)
# ---------------------------------------------------------------------------
class WorkerState:
    INACTIVE = "INACTIVE"
    READY = "READY"
    NOTIFIED = "NOTIFIED"
    RESERVED = "RESERVED"
    RUNNING = "RUNNING"

    ALL = frozenset({INACTIVE, READY, NOTIFIED, RESERVED, RUNNING})


# 허용된 근로자 상태 전이 (from -> {to})
WORKER_TRANSITIONS: dict[str, frozenset[str]] = {
    WorkerState.INACTIVE: frozenset({WorkerState.READY}),
    WorkerState.READY: frozenset({WorkerState.NOTIFIED, WorkerState.INACTIVE}),
    WorkerState.NOTIFIED: frozenset({WorkerState.RESERVED, WorkerState.READY}),
    WorkerState.RESERVED: frozenset({WorkerState.RUNNING, WorkerState.READY}),
    WorkerState.RUNNING: frozenset({WorkerState.INACTIVE}),
}


def can_transition(current: str, target: str) -> bool:
    """근로자 상태 전이가 허용되는지 확인한다."""
    return target in WORKER_TRANSITIONS.get(current, frozenset())


# ---------------------------------------------------------------------------
# WorkRequest 상태 (v2)
# ---------------------------------------------------------------------------
class RequestStatus:
    REQUESTED = "REQUESTED"
    COMPOSING = "COMPOSING"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

    ALL = frozenset(
        {
            REQUESTED,
            COMPOSING,
            PROPOSED,
            APPROVED,
            DISPATCHED,
            RUNNING,
            COMPLETED,
            CANCELLED,
            REJECTED,
        }
    )


# ---------------------------------------------------------------------------
# Crew 상태 (v2)
# ---------------------------------------------------------------------------
class CrewStatus:
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    NOTIFIED = "NOTIFIED"
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

    ALL = frozenset(
        {DRAFT, PROPOSED, APPROVED, NOTIFIED, DISPATCHED, RUNNING, COMPLETED, CANCELLED}
    )


# ---------------------------------------------------------------------------
# GapEvent 상태 및 유형 (v2)
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
    DECLINED = "DECLINED"

    ALL = frozenset({NO_SHOW, LEFT_SITE, UNAVAILABLE, DECLINED})


# ---------------------------------------------------------------------------
# Assignment (CrewMember 원본) 상태 및 수락 상태
# ---------------------------------------------------------------------------
class Acceptance:
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"

    ALL = frozenset({PENDING, ACCEPTED, DECLINED})


class AssignmentStatus:
    RESERVED = "RESERVED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"
    LEFT_SITE = "LEFT_SITE"
    LEFT = "LEFT_SITE"  # 별칭
    DECLINED = "DECLINED"

    ALL = frozenset(
        {RESERVED, RUNNING, COMPLETED, CANCELLED, NO_SHOW, LEFT_SITE, DECLINED}
    )


# ---------------------------------------------------------------------------
# 직종(trade) enum
# ---------------------------------------------------------------------------
class Trade:
    FORMWORK = "FORMWORK"          # 형틀목공
    REBAR = "REBAR"               # 철근
    MASONRY = "MASONRY"           # 조적
    MATERIAL_CARRY = "MATERIAL_CARRY"  # 자재운반(곰방)
    GENERAL = "GENERAL"           # 보통인부

    ALL = frozenset({FORMWORK, REBAR, MASONRY, MATERIAL_CARRY, GENERAL})

    # 요청(required_workers) 전용 마커: 직종 무관. 근로자 preferred/excluded_trades 에는 쓰지 않는다.
    ANY = "ANY"
    REQUIRED_ALL = frozenset({FORMWORK, REBAR, MASONRY, MATERIAL_CARRY, GENERAL, ANY})


# ---------------------------------------------------------------------------
# 사용자 역할
# ---------------------------------------------------------------------------
class Role:
    WORKER = "WORKER"
    OFFICE = "OFFICE"
    COMPANY = "COMPANY"

    ALL = frozenset({WORKER, OFFICE, COMPANY})
