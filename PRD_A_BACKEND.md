# CrewMate PRD — A: 플랫폼 / 백엔드 코어 (Kiro 입력용)

> **용도**: 이 문서를 Kiro Spec 모드에 입력하여 담당자 A 범위의 `requirements.md → design.md → tasks.md`를 생성한다.
> 지시문: **"이 PRD를 기반으로 requirements.md, design.md, tasks.md를 순서대로 생성해줘. 문서에 정의되지 않은 기능·ML 모델·인프라를 임의로 추가하지 마. '담당 범위 밖' 섹션의 항목은 구현하지 마."**

---

## 0. 프로젝트 컨텍스트

CrewMate는 건설 일용직 작업조 편성을 디지털화하는 100% 서버리스 웹 서비스다.
역할은 `WORKER`(근로자), `OFFICE`(인력사무소), `COMPANY`(건설사) 3종이다.
AI 구성 요소는 Crew Composition Agent 1개뿐이며(담당자 B 구현), **모든 상태 변경은 Lambda가 수행하고 Agent는 조회·추천만 한다.**

이 문서의 담당 범위: **인프라(SAM·DynamoDB·Cognito·API Gateway) + 코어 비즈니스 Lambda 5종 + 상태머신 + 시드 스크립트.**

---

## 1. 공유 계약 (Shared Contracts) — 임의 변경 금지

이 섹션은 팀 3인의 PRD에 동일하게 포함된 계약이다. Kiro는 이 계약을 절대 변경하지 않는다.

### 1.1 절대 원칙

1. 별도 ML 모델 없음 (SageMaker, XGBoost, 확률 예측 모델 금지)
2. 일반 편성과 긴급 재편성은 **동일한 Crew Composition Agent** 사용
3. Agent는 추천만 하고, 실제 배정·상태 변경은 인력사무소 승인 후 Lambda가 수행 (Human-in-the-Loop)
4. 100% 서버리스: EC2/ECS/상시 서버 금지
5. GPS 지오펜스·Amazon Location Service 금지, 노쇼는 시뮬레이션 버튼으로 발생
6. P0 알림은 DynamoDB 인앱 알림 + 프론트 폴링 (WebSocket은 P1)

### 1.2 근로자 상태 머신

```text
INACTIVE → (대기 시작) → READY → (승인·조건부 쓰기) → RESERVED → (배차 완료) → RUNNING → (작업 종료) → INACTIVE
RESERVED → (배차 취소/실패) → READY
```

- 신규 편성 후보는 `READY`만 사용한다.
- `RESERVED`/`RUNNING` 근로자는 신규 후보로 사용하지 않는다.
- 긴급 재편성 시 기존 정상 팀원은 `RUNNING` 유지.
- 승인 시점에 `state == READY` 조건부 쓰기로 전환하며, 조원 전체를 원자 처리한다.

### 1.3 기타 상태 enum

```text
WorkRequest: REQUESTED → COMPOSING → PROPOSED → APPROVED → RUNNING → COMPLETED (+CANCELLED)
Crew:        DRAFT → PROPOSED → APPROVED → RUNNING → COMPLETED (+CANCELLED)
GapEvent:    DETECTED → RECOMPOSING → PROPOSED → APPROVED → FILLED (+FAILED)
GapEvent 유형: NO_SHOW / LEFT_SITE / UNAVAILABLE
```

### 1.4 DynamoDB 단일 테이블 `CrewMate`

건설사별 물리 테이블을 만들지 않는다. 모든 엔터티는 한 테이블에 `item_type` 속성으로 구분한다.

| 엔터티        | PK                   | SK                         | GSI1PK / GSI1SK                                      | GSI2PK / GSI2SK                             |
| ------------- | -------------------- | -------------------------- | ---------------------------------------------------- | ------------------------------------------- |
| Worker        | `WORKER#{worker_id}` | `PROFILE`                  | `OFFICE#{office_id}` / `STATE#{state}#W#{worker_id}` | —                                           |
| WorkRequest   | `REQ#{request_id}`   | `META`                     | `OFFICE#{office_id}` / `REQ#{status}#{request_id}`   | `COMPANY#{company_id}` / `REQ#{request_id}` |
| Crew          | `CREW#{crew_id}`     | `META`                     | `OFFICE#{office_id}` / `CREW#{status}#{crew_id}`     | —                                           |
| GapEvent      | `GAP#{event_id}`     | `META`                     | `OFFICE#{office_id}` / `GAP#{status}#{event_id}`     | —                                           |
| Notification  | `USER#{user_id}`     | `NOTI#{created_at}#{id}`   | —                                                    | —                                           |
| Collaboration | `WORKER#{worker_id}` | `COLLAB#{other_id}#{date}` | —                                                    | —                                           |

- 사무소의 READY 후보 조회: GSI1에서 `GSI1PK = OFFICE#{id}` AND `begins_with(GSI1SK, 'STATE#READY#')`
- state 변경 시 GSI1SK도 함께 갱신한다.
- Collaboration은 한 협업 건당 양방향 2개 아이템으로 저장한다.

### 1.5 Worker 속성

```json
{
  "item_type": "WORKER",
  "worker_id": "UUID",
  "user_id": "cognito sub",
  "name": "홍길동",
  "phone": "010-0000-0000",
  "office_id": "OFFICE001",
  "state": "READY",
  "trade": "FORMWORK",
  "skill_level": 4,
  "career_years": 7,
  "age": 42,
  "region": "BUSAN_HAEUNDAE",
  "desired_daily_wage": 170000,
  "certifications": [],
  "completed_count": 48,
  "no_show_count": 1,
  "current_crew_id": null,
  "state_changed_at": "ISO8601",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

데이터 원칙:

- `worker_id`는 UUID. **주민등록번호는 해시 포함 어떤 형태로도 수집·저장하지 않는다.**
- 신뢰도는 비율이 아닌 `completed_count`/`no_show_count` 원시값으로 저장한다.
- `no_show_count` 등 부정적 데이터는 OFFICE 내부 조회에만 제공하고 COMPANY 응답·Agent 추천 사유 텍스트에 포함하지 않는다.
- `trade` enum 예시: `FORMWORK`(형틀목공), `REBAR`(철근), `MASONRY`(석재), `MATERIAL_CARRY`(곰방), `GENERAL`(보통인부)
- `skill_level`은 1~5 정수.

### 1.6 API 응답 형식

```json
{ "success": true, "data": {} }
{ "success": false, "error": { "code": "WORKER_NOT_READY", "message": "..." } }
```

오류 코드: `UNAUTHORIZED, FORBIDDEN, WORKER_NOT_FOUND, WORKER_NOT_READY, WORKER_ALREADY_RUNNING, REQUEST_NOT_FOUND, REQUEST_ALREADY_ASSIGNED, CREW_INVALID, AGENT_OUTPUT_INVALID, AGENT_RETRY_FAILED, STATE_CONFLICT, GAP_EVENT_NOT_FOUND`

### 1.7 권한 요약

- WORKER: 자기 프로필·상태·배정·알림만. 타 근로자 조회 불가.
- OFFICE: 자기 사무소 근로자 조회, 수동/Agent 편성, 승인, 긴급 재편성. 타 사무소 접근 불가.
- COMPANY: 자기 요청 CRUD, 확정 작업조 조회, 결원 이벤트 등록. 근로자 풀 직접 조회 불가.

---

## 2. 담당 범위

### 포함 (이 PRD로 구현)

1. AWS SAM 프로젝트 (`template.yaml`) — DynamoDB, Cognito, API Gateway, Lambda 전체 리소스 정의
2. DynamoDB 테이블 `CrewMate` + GSI1 + GSI2
3. Cognito User Pool + 시드 데모 계정 3종 (worker / office / company)
4. Lambda: `worker_api`, `company_request`, `office_core`, `assignment`, `notification`
5. `backend/shared/` 공용 모듈: DB 접근 헬퍼, 응답 포맷터, 상태 상수, 권한 체크
6. 시드 스크립트 3종 (`scripts/seed/`)

### 담당 범위 밖 (구현 금지 — 다른 팀원 담당)

- Crew Composition Agent, Agent Tools, `agent_invoke` Lambda, `gap_event` Lambda → 담당자 B
- React 프론트엔드 전체 → 담당자 C
- 단, B·C가 호출할 수 있도록 **API 계약과 shared 모듈 인터페이스는 이 문서대로 제공해야 한다.**

---

## 3. 기능 요구사항

우선순위: `[P0]` 데모 필수 / `[P1]` 가능 시 / `[P2]` 여유 시

### F-A1. 인증 및 역할 라우팅 [P0]

- WHEN 사용자가 로그인하면, THE SYSTEM SHALL Cognito JWT를 검증하고 custom claim(`role`, `office_id`/`company_id`)을 추출한다.
- THE SYSTEM SHALL 역할에 맞지 않는 API 호출에 `FORBIDDEN`을 반환한다.
- 시드 데모 계정 3종을 제공한다. 복잡한 회원가입 플로우는 P0 범위가 아니다.

### F-A2. Worker API Lambda [P0]

Route:

```text
POST   /worker/application        지원서 생성 (state = INACTIVE로 시작)
PUT    /worker/application        지원서 수정
GET    /worker/me                 내 프로필·상태 조회
POST   /worker/state/ready        대기 시작 (INACTIVE → READY)
POST   /worker/state/inactive     대기 취소 (READY → INACTIVE)
GET    /worker/assignments        내 배정 조회
```

수용 기준:

- WHEN 근로자가 지원서를 제출하면, THE SYSTEM SHALL Worker 아이템을 생성하고 `state = INACTIVE`로 저장한다.
- WHEN 동일 user_id의 지원서가 이미 존재하면, THE SYSTEM SHALL 중복 생성 대신 수정을 안내하는 오류를 반환한다.
- WHEN 대기 시작 요청 시, THE SYSTEM SHALL `state = INACTIVE` 조건부 쓰기로 `READY` 전환하고 `state_changed_at`·GSI1SK를 갱신한다.
- `RESERVED`/`RUNNING` 상태에서 대기 취소 요청 시 `STATE_CONFLICT`를 반환한다.

### F-A3. Company Request Lambda [P0]

Route:

```text
POST   /company/requests
PUT    /company/requests/{requestId}
GET    /company/requests
GET    /company/requests/{requestId}
POST   /company/crews/{crewId}/gap-events     결원 이벤트 등록(저장 후 EventBridge 발행 → B의 gap_event가 처리)
```

수용 기준:

- 요청 항목: `request_id, company_id, office_id, site_name, work_date, start_time, location_text, required_workers[], budget, priority{}, notes, status`
- WHEN 건설사가 요청을 생성하면, THE SYSTEM SHALL `status = REQUESTED`로 저장하고 GSI1로 해당 사무소에서 조회 가능하게 한다.
- COMPANY의 확정 작업조 조회 응답에는 근로자의 `name, trade, skill_level`만 포함하고 `no_show_count` 등 내부 데이터는 제외한다.

### F-A4. Office Core Lambda [P0]

Route:

```text
GET    /office/workers                  소속 근로자 조회 (필터: state, trade, skill_level, wage 범위, region)
GET    /office/workers?state=READY      READY 후보 조회 (수동 편성 기본 필터)
POST   /office/crews/manual             수동 작업조 생성 (Crew status = DRAFT)
POST   /office/crews/{crewId}/approve   작업조 승인 → assignment 로직 실행
POST   /office/emergency/{eventId}/approve   긴급 작업조 승인 → 대체 인력 배차
```

수용 기준:

- OFFICE는 자신의 `office_id` 소속 근로자만 조회한다.
- 수동 편성 후보는 동일 사무소 + `READY`여야 하며, 동일 근로자 중복 선택을 거부한다.
- 필수 직종 인원 미충족 작업조는 승인 요청 자체를 `CREW_INVALID`로 거부한다.

### F-A5. Assignment 처리 (승인 → RESERVED → RUNNING) [P0] — 이 PRD의 핵심

승인 흐름:

```text
승인 클릭
→ 요청 유효성·미중복 확정 검증
→ 신규 조원 전체 state == READY 재검증
→ TransactWriteItems: 전원 READY → RESERVED (ConditionExpression: state = READY)
→ Crew status = APPROVED, Request status = APPROVED
→ 배차 완료 처리: 전원 RESERVED → RUNNING, current_crew_id 기록
→ Crew·Request status = RUNNING
→ 조원별 Notification 아이템 생성 (장소·날짜·시간 포함)
```

수용 기준:

- WHEN 조원 중 한 명이라도 조건부 쓰기에 실패하면, THE SYSTEM SHALL 트랜잭션 전체를 중단하고 `STATE_CONFLICT`를 반환하며 어떤 근로자도 상태가 변하지 않는다.
- WHEN 배차가 취소되거나 실패하면, THE SYSTEM SHALL `RESERVED` 근로자를 `READY`로 복구한다.
- 일부만 RUNNING인 불완전 작업조를 만들지 않는다.
- 긴급 승인(`/office/emergency/...`)도 동일한 조건부 쓰기 경로를 재사용하며, 대체 인력만 READY → RESERVED → RUNNING 전환하고 기존 팀원은 건드리지 않는다. 이탈자는 `INACTIVE` + `current_crew_id = null` 처리하고, Crew의 `member_ids`를 새 조합으로 갱신하며 GapEvent를 `FILLED`로 마친다.

### F-A6. Notification Lambda [P0]

- 배정/긴급 배정/신규 요청/작업조 변경 알림을 Notification 아이템으로 생성한다.
- `GET /notifications` 로 자기 알림 목록을 반환한다 (프론트 폴링 대상).
- P0는 DB 인앱 알림만. SMS/푸시 금지.

### F-A7. 시드 스크립트 [P0]

`scripts/seed/` 에 3종:

1. `seed_workers.py` — 근로자 50~100명 (사무소 2곳 분산, trade·skill·wage 다양화), 건설사 2~3곳, 인력 요청 5~10건. `seed = 42` 고정.
2. `seed_history.py` — 작업 이력 100건 내외, 협업 이력 50~100쌍 (양방향 아이템).
3. `seed_demo_scenario.py` — 긴급 데모 세트 보장:

```text
A: RUNNING (기존 작업조), B: RUNNING (기존 작업조), C: RUNNING (노쇼 대상)
D: READY, E: READY (A·B와 협업 이력 있음), F: READY
```

- 스크립트는 재실행 시 기존 데모 데이터를 초기화하는 리셋 모드를 지원한다 (데모 반복 리허설용).

### F-A8. 로그 및 관측성 [P1]

- CloudWatch에 `request_id, crew_id, office_id, lambda_request_id, processing_status, error_code`를 구조화 로그로 남긴다.
- 이름·전화번호 등 개인정보는 로그에 남기지 않는다.

---

## 4. 마일스톤 (담당자 A)

| Day | 목표                                              | 완료 기준                                                |
| --- | ------------------------------------------------- | -------------------------------------------------------- |
| 1   | SAM + DynamoDB + Cognito + API 골격 + shared 모듈 | 시드 계정 3종 로그인 토큰 발급, 테이블·GSI 생성 확인     |
| 2   | worker_api + company_request + 시드 v1            | curl로 지원서→READY→요청 생성→사무소 조회 성공           |
| 3   | office_core + assignment (조건부 쓰기)            | 수동 편성→승인→전원 RUNNING, 동시 승인 시 STATE_CONFLICT |
| 4   | notification + 시드 v2 + B·C 통합 지원            | B의 추천 저장·C의 화면이 실 API로 동작                   |
| 5   | 긴급 승인·배차 API                                | 시나리오 3의 승인 이후 구간 동작                         |
| 6   | 통합·리셋 스크립트·리허설                         | E2E 3시나리오 반복 통과                                  |

---

## 5. 금지 사항

1. ML 모델·SageMaker·확률 예측 기능을 추가하지 않는다.
2. 건설사별 물리 테이블을 만들지 않는다.
3. 승인 시점 상태 재검증과 조건부 쓰기를 생략하지 않는다.
4. Agent 관련 코드(`agent/`, `agent_invoke`, `gap_event`)를 작성하지 않는다 — 담당자 B 영역.
5. 프론트엔드 코드를 작성하지 않는다 — 담당자 C 영역.
6. 요구사항에 없는 마이크로서비스·큐·캐시 계층을 추가하지 않는다.
7. 주민등록번호 필드를 어떤 형태로도 추가하지 않는다.
