# CrewMate PRD — C: 프론트엔드 / 데모 (Kiro 입력용)

> **용도**: 이 문서를 Kiro Spec 모드에 입력하여 담당자 C 범위의 `requirements.md → design.md → tasks.md`를 생성한다.
> 지시문: **"이 PRD를 기반으로 requirements.md, design.md, tasks.md를 순서대로 생성해줘. 문서에 정의되지 않은 기능·화면을 임의로 추가하지 마. '담당 범위 밖' 섹션의 항목은 구현하지 마."**

---

## 0. 프로젝트 컨텍스트

CrewMate는 건설 일용직 작업조 편성을 디지털화하는 100% 서버리스 웹 서비스다.
역할은 `WORKER`(근로자), `OFFICE`(인력사무소), `COMPANY`(건설사) 3종이며, 각 역할이 완전히 다른 화면을 사용한다.
AI 구성 요소는 Crew Composition Agent 1개(담당자 B)이며, 승인은 항상 사람(OFFICE)이 한다.

이 문서의 담당 범위: **React SPA 전체(3역할 화면) + 폴링 상태 갱신 + mock API 레이어 + 데모 연출 + S3/CloudFront 배포.**

---

## 1. 공유 계약 (Shared Contracts) — 임의 변경 금지

이 섹션은 팀 3인의 PRD에 동일하게 포함된 계약이다. Kiro는 이 계약을 절대 변경하지 않는다.

### 1.1 절대 원칙

1. 별도 ML 모델 없음. AI는 Crew Composition Agent 1개.
2. Agent는 추천만 하고, 실제 배정은 인력사무소 승인 후 처리 (Human-in-the-Loop) — UI도 이 구조를 그대로 드러낸다.
3. GPS 지오펜스 없음. 노쇼는 화면의 시뮬레이션 버튼으로 발생시킨다.
4. P0 실시간성은 REST 폴링(3~5초)으로 구현한다. WebSocket은 P1.
5. 100% 서버리스 (프론트는 S3 + CloudFront 정적 호스팅).

### 1.2 근로자 상태 머신 (UI 표시 기준)

```text
INACTIVE → (대기 시작) → READY → (승인) → RESERVED → (배차 완료) → RUNNING → (작업 종료) → INACTIVE
RESERVED → (배차 취소/실패) → READY
```

- `RESERVED`/`RUNNING` 상태에서는 대기 취소 버튼을 비활성화한다.
- 승인 시 서버가 `STATE_CONFLICT`를 반환할 수 있다 — 다른 요청에 먼저 배정된 경우이며, UI는 이를 오류가 아닌 정상 흐름으로 안내한다.

### 1.3 기타 상태 enum

```text
WorkRequest: REQUESTED → COMPOSING → PROPOSED → APPROVED → RUNNING → COMPLETED (+CANCELLED)
Crew:        DRAFT → PROPOSED → APPROVED → RUNNING → COMPLETED (+CANCELLED)
GapEvent:    DETECTED → RECOMPOSING → PROPOSED → APPROVED → FILLED (+FAILED)
```

### 1.4 API 계약 (담당자 A·B 제공)

응답 형식:

```json
{ "success": true, "data": {} }
{ "success": false, "error": { "code": "WORKER_NOT_READY", "message": "..." } }
```

주요 Route:

```text
# Worker
POST /worker/application          PUT /worker/application
GET  /worker/me                   GET /worker/assignments
POST /worker/state/ready          POST /worker/state/inactive

# Company
POST /company/requests            GET /company/requests
GET  /company/requests/{id}       PUT /company/requests/{id}
POST /company/crews/{crewId}/gap-events

# Office
GET  /office/workers              GET /office/workers?state=READY
POST /office/crews/manual         POST /office/crews/{crewId}/approve
POST /office/requests/{requestId}/agent-compose
POST /office/gap-events/{eventId}/agent-recompose
POST /office/emergency/{eventId}/approve

# 공통
GET  /notifications
```

오류 코드: `UNAUTHORIZED, FORBIDDEN, WORKER_NOT_FOUND, WORKER_NOT_READY, WORKER_ALREADY_RUNNING, REQUEST_NOT_FOUND, REQUEST_ALREADY_ASSIGNED, CREW_INVALID, AGENT_OUTPUT_INVALID, AGENT_RETRY_FAILED, STATE_CONFLICT, GAP_EVENT_NOT_FOUND`

### 1.5 Agent 추천 응답 형식 (추천 카드 렌더링 대상)

```json
{
  "recommendations": [
    {
      "rank": 1,
      "member_ids": ["W001", "W014", "W027"],
      "members": [ { "worker_id": "W001", "name": "홍길동", "trade": "FORMWORK", "skill_level": 4, "desired_daily_wage": 170000 } ],
      "total_cost": 430000,
      "reason": "필요 직종 구성을 충족하며 예산 범위 안에서 숙련도와 기존 협업 경험의 균형이 가장 좋습니다.",
      "considerations": ["필수 직종 충족", "예산 범위 충족", "A-B 공동 작업 이력 존재"]
    }
  ]
}
```

### 1.6 데이터 노출 원칙

- 건설사(COMPANY) 화면에는 근로자의 `name, trade, skill_level`만 표시한다. 노쇼 횟수·신뢰도 등 내부 데이터는 표시하지 않는다.
- AI 결과는 "AI 추천 1안/2안/3안"으로 표기한다. "최적 조합", "출근 확률 97%" 같은 확률·보장 표현을 UI에 쓰지 않는다.
- 주민등록번호 입력 필드를 만들지 않는다.

---

## 2. 담당 범위

### 포함 (이 PRD로 구현)

1. `frontend/` React SPA — worker / office / company 화면 전체
2. Cognito 시드 계정 로그인 + 역할별 라우팅
3. mock API 레이어 (환경 변수로 mock ↔ 실 API 전환)
4. 폴링 기반 상태 갱신 훅
5. S3 + CloudFront 배포 스크립트
6. 데모 시나리오 연출 요소 (노쇼 시뮬레이션 버튼 등)

### 담당 범위 밖 (구현 금지 — 다른 팀원 담당)

- Lambda·DynamoDB·Cognito 리소스 정의 → 담당자 A
- Agent·gap_event 로직 → 담당자 B
- 프론트는 오직 1.4의 API 계약만 호출한다. 백엔드 미완성 구간은 mock으로 대체한다.

---

## 3. 기능 요구사항

### F-C1. 로그인 및 역할 라우팅 [P0]

- 시드 계정 3종(worker/office/company)으로 로그인하는 단순 로그인 화면.
- WHEN 로그인에 성공하면, THE SYSTEM SHALL 역할에 따라 `/worker`, `/office`, `/company`로 라우팅한다.
- 역할에 맞지 않는 경로 접근 시 자기 홈으로 리다이렉트한다.

### F-C2. 근로자 화면 [P0]

화면: 로그인 / 지원서 작성·수정 / 홈(상태 표시) / 배정 정보 / 알림 목록

- 지원서 폼: 이름, 전화번호, 인력사무소 선택, 분야(trade), 숙련도, 경력 연차, 나이, 지역, 희망 일당, 자격증, 자기소개. **주민번호 필드 없음.**
- 홈에 현재 상태를 크게 표시하고 상태별 버튼을 제공한다:

```text
현재 상태: INACTIVE  → [대기 시작]
현재 상태: READY     → [대기 취소]
현재 상태: RUNNING   → 현재 현장: 해운대 A현장 / 작업 시작: 2026-07-10 07:00 (버튼 없음)
```

- WHEN 배정이 확정되면(폴링 감지), THE SYSTEM SHALL 장소·날짜·시간을 포함한 배정 카드를 표시한다.

### F-C3. 건설사 화면 [P0]

화면: 인력 요청 생성 / 요청 목록 / 요청 상세(진행 상태) / 확정 작업조 / 노쇼 시뮬레이션 / 긴급 재편성 진행 상태

- 요청 생성 폼: 현장명, 작업일, 시작 시간, 위치, 직종별 필요 인원(동적 행 추가), 예산, 우선순위(cost/skill/teamwork 각 HIGH/MEDIUM/LOW), 비고.
- 요청 상세에서 상태 배지(REQUESTED → … → RUNNING)를 표시하고 폴링으로 갱신한다.
- 확정 작업조에는 근로자 이름·직종·숙련도만 표시한다 (1.6 원칙).
- **노쇼 시뮬레이션 버튼**: 확정 작업조의 특정 근로자 옆 버튼으로 `POST /company/crews/{crewId}/gap-events` 호출 (type: NO_SHOW 또는 LEFT_SITE 선택). 데모 핵심 트리거이므로 눈에 잘 띄게 배치한다.
- 긴급 재편성 진행 상태: GapEvent 상태(DETECTED → RECOMPOSING → PROPOSED → APPROVED → FILLED)를 단계 표시로 렌더링하고 폴링으로 갱신, FILLED 시 갱신된 작업조를 강조 표시한다.

### F-C4. 인력사무소 화면 [P0] — 데모의 중심 화면

화면: 요청 목록 / 요청 상세 / READY 후보 목록 / 수동 편성 / AI 자동 편성 / 추천안 카드 / 승인 / 현재 작업조 / 긴급 재편성

- READY 후보 목록: 필터(직종, 숙련도, 희망 일당 범위, 지역). 기본 필터 `state = READY`.
- 수동 편성: 후보 체크 선택 → 직종별 충족 현황 표시(부족 시 승인 버튼 비활성) → 임시 저장 → 승인.
- **AI 자동 편성 버튼**: `agent-compose` 호출, 로딩 상태 표시. 실패(`AGENT_RETRY_FAILED`) 시 "수동 편성으로 진행" 안내와 함께 수동 화면으로 유도.
- 추천안 카드 (1~3안):

```text
AI 추천 1안
홍길동 / 형틀목공 / 숙련 4
김철수 / 보통인부 / 숙련 3
이영희 / 보통인부 / 숙련 4
예상 총 일당: 430,000원
추천 이유: 필수 직종 구성을 충족하며 예산 범위 안에서 기존 협업 경험과 숙련도의 균형이 가장 좋습니다.
[승인] [다른 안 보기]
```

- WHEN 승인이 `STATE_CONFLICT`로 실패하면, THE SYSTEM SHALL "일부 근로자가 이미 다른 작업에 배정되었습니다" 안내 후 재편성(AI 재실행 또는 수동)을 제안한다.
- 긴급 재편성 화면: GapEvent 알림 → 잔여 팀원(고정) + 추천 대체 조합 카드 → 승인 → 갱신된 작업조 표시. 기존 팀원과 신규 투입자를 시각적으로 구분한다.

### F-C5. 폴링 및 알림 [P0]

- 공용 폴링 훅: 화면별 필요 데이터(내 상태, 요청 상태, GapEvent 상태, 알림)를 3~5초 간격 폴링. 탭 비활성 시 중단.
- 알림 목록: `GET /notifications` 폴링, 새 알림 뱃지 표시.

### F-C6. mock API 레이어 [P0]

- 모든 API 호출은 단일 client 모듈을 경유하고, `VITE_API_MODE=mock|real` 환경 변수로 전환한다.
- mock 모드는 1.4·1.5 계약과 동일한 형태의 응답을 반환하며, 시나리오 진행(요청 생성 → 추천 → 승인 → 노쇼 → 재편성)을 메모리 상태로 재현할 수 있어야 한다.
- 목적: 백엔드 완성 전 화면 개발, 데모 당일 백엔드 장애 시 최후 폴백.

### F-C7. 배포 [P0] / 반응형 [P1]

- 빌드 산출물을 S3에 업로드하고 CloudFront로 서빙하는 배포 스크립트를 제공한다.
- P1: 근로자 화면은 모바일 우선 반응형, 사무소·건설사 화면은 데스크톱 우선.

---

## 4. 마일스톤 (담당자 C)

| Day | 목표 | 완료 기준 |
|---|---|---|
| 1 | 프로젝트 셋업, 로그인, 역할 라우팅, mock 레이어 설계 | 3계정 로그인 → 역할별 빈 홈 |
| 2 | 근로자 화면 + 건설사 요청 화면 (mock) | 지원서→대기→READY, 요청 생성이 mock으로 동작 |
| 3 | 사무소 요청 목록·후보·수동 편성 + 실 API 전환 시작 | 수동 편성→승인이 실 API로 동작 |
| 4 | AI 편성 버튼 + 추천 카드 + 승인 + 폴링 | 시나리오 2 화면 전체 통과 |
| 5 | 노쇼 시뮬레이션 + 긴급 재편성 + 건설사 상태 화면 + 알림 | 시나리오 3 화면 전체 통과 |
| 6 | 배포, UI 다듬기, 데모 리허설 주도 | CloudFront URL에서 E2E 3시나리오 시연 |

---

## 5. 금지 사항

1. 백엔드 Lambda·인프라 코드를 작성하지 않는다 — 담당자 A·B 영역.
2. API 계약(경로·응답 형식·필드명)을 임의로 변경하지 않는다. 필요 시 팀 합의로 세 PRD를 동시 수정한다.
3. WebSocket을 P0에 도입하지 않는다 (폴링 사용).
4. localStorage 의존적 인증 대신 세션 메모리 + Cognito 토큰 갱신을 사용한다.
5. GPS·지도 SDK를 추가하지 않는다. 위치는 텍스트로만 표시한다.
6. UI에 확률 수치, "최적 보장" 표현, 근로자 부정적 데이터(노쇼 횟수 등)를 건설사 화면에 노출하지 않는다.
7. 주민등록번호 입력 필드를 만들지 않는다.
