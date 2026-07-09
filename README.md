# CrewMate

건설 일용직 인력 중개를 디지털화하는 서버리스 AI 플랫폼.
인력사무소의 전화·종이 기반 작업조 편성을 **상태 기반 배치 시스템 + Crew Composition Agent**로 대체한다.

---

## 1. 문제와 솔루션

한국 건설 일용직 시장에서 인력사무소는 매일 새벽 전화로 근로자를 모으고, 수기로 조를 짜고, 노쇼가 발생하면 급하게 대체 인력을 수소문한다. CrewMate는 이 과정을 다음 세 가지로 대체한다.

1. **상태 기반 인력 풀** — 근로자가 대기 버튼을 누르면 `READY` 상태가 되어 편성 후보로 조회된다.
2. **Crew Composition Agent** — 건설사 요청 조건(직종·인원·예산·우선순위)과 READY 후보를 종합 평가해 **팀 단위 조합**을 추천한다. 인력사무소가 승인해야만 실제 배정된다 (Human-in-the-Loop).
3. **긴급 재편성** — 노쇼/이탈 발생 시 **동일한 Agent**를 EMERGENCY 모드로 재호출한다. 남은 팀원은 고정하고, READY 후보 중 팀 전체 조합이 가장 좋아지는 인원을 추천한다.

핵심 차별점: 개인 점수 랭킹이 아니라 **기존 팀 + 후보의 조합**을 평가하는 팀 단위 편성.

---

## 2. 사용자 역할

| 역할 | 설명 | 주요 기능 |
|---|---|---|
| `WORKER` | 인력사무소에 등록한 근로자 | 지원서 등록, 대기 시작/취소, 배정 확인 |
| `OFFICE` | 근로자 풀을 관리하고 작업조를 편성 | 후보 조회, 수동/Agent 편성, 승인, 긴급 재편성 |
| `COMPANY` | 현장 인력을 요청하는 건설사 | 인력 요청 생성, 확정 작업조 확인, 결원 등록 |

---

## 3. 핵심 흐름

```text
근로자 지원서 등록
    → state = INACTIVE
근로자 대기 버튼
    → state = READY
건설사 인력 요청 생성
    → 인력사무소 요청 확인
수동 편성  또는  Crew Composition Agent 자동 편성 (NORMAL 모드)
    → 인력사무소 승인 클릭
    → READY 상태 재검증 (조건부 쓰기)
    → state = RESERVED
    → 배차 완료
    → state = RUNNING

[노쇼 / 중도 이탈]
    → Gap Event 생성
    → 기존 정상 팀원 RUNNING 유지 (fixed_members)
    → READY 후보 조회
    → 동일 Agent 재호출 (EMERGENCY 모드)
    → 긴급 작업조 추천
    → 인력사무소 승인 (READY 재검증 → RESERVED)
    → 대체 인력 RUNNING, 작업조 갱신
```

### 근로자 상태 머신

```text
               대기 시작              승인(조건부 쓰기)        배차 완료
INACTIVE ──────────────▶ READY ──────────────────▶ RESERVED ─────────▶ RUNNING
   ▲                       ▲                          │                   │
   │                       └──── 배차 취소/실패 ───────┘                   │
   └─────────────────────────────── 작업 종료 ────────────────────────────┘
```

중복 배치 방지 규칙:

- 신규 편성 후보는 `READY` 상태만 사용한다.
- 승인 순간 `state == READY` 조건부 쓰기로 `RESERVED` 전환. 조원 전체를 `TransactWriteItems`로 원자 처리한다.
- 한 명이라도 조건 실패 시 전체 승인을 중단하고 `STATE_CONFLICT`를 반환한다.

---

## 4. 시스템 아키텍처 (100% 서버리스)

```text
React SPA (S3 + CloudFront)
        │
     Cognito (시드 데모 계정 3종)
        │
   API Gateway REST
        │
   ├─ Core Lambda (worker / company / office / assignment / notification)
   └─ Agent Invoke Lambda
           │
     Crew Composition Agent (Strands Agents SDK + Amazon Bedrock)
           │  Tools: get_request_detail / get_ready_workers /
           │         get_worker_history / get_current_crew
           ▼
     DynamoDB 단일 테이블 (CrewMate)
           │
      EventBridge ─▶ Gap Event Lambda / Notification Lambda
```

사용하지 않는 것: EC2, ECS, SageMaker, 별도 ML 모델, GPS 지오펜스, Amazon Location Service, WebSocket(P0는 폴링).

AI 구성 요소는 **Crew Composition Agent 1개**뿐이며, 모든 상태 변경은 인증된 사용자 요청을 받은 Lambda가 수행한다. Agent는 조회·추천만 한다.

---

## 5. 데이터 모델 — DynamoDB 단일 테이블

테이블 1개(`CrewMate`)에 모든 엔터티를 `item_type`으로 구분해 저장한다.
건설사별 물리 테이블을 만들지 않는다.

| 엔터티 | PK | SK | GSI1PK / GSI1SK |
|---|---|---|---|
| Worker | `WORKER#{worker_id}` | `PROFILE` | `OFFICE#{office_id}` / `STATE#{state}#W#{worker_id}` |
| Work Request | `REQ#{request_id}` | `META` | `OFFICE#{office_id}` / `REQ#{status}#{request_id}` |
| Crew(추천 포함) | `CREW#{crew_id}` | `META` | `OFFICE#{office_id}` / `CREW#{status}#{crew_id}` |
| Gap Event | `GAP#{event_id}` | `META` | `OFFICE#{office_id}` / `GAP#{status}#{event_id}` |
| Notification | `USER#{user_id}` | `NOTI#{created_at}#{id}` | — |
| Collaboration | `WORKER#{worker_id}` | `COLLAB#{other_id}#{date}` | — |

- **GSI1** 하나로 "이 사무소의 READY 후보 / 요청 목록 / 작업조 목록"을 모두 조회한다.
- **GSI2** (`COMPANY#{company_id}` / `REQ#{request_id}`)로 건설사 자기 요청 목록을 조회한다.
- 상태 전환은 항상 `ConditionExpression`(예: `state = READY`) 기반 조건부 쓰기로 수행한다.

### Worker 핵심 속성

```json
{
  "worker_id": "UUID",
  "name": "홍길동",
  "phone": "010-....",
  "office_id": "OFFICE001",
  "state": "READY",
  "trade": "FORMWORK",
  "skill_level": 4,
  "career_years": 7,
  "age": 42,
  "region": "BUSAN_HAEUNDAE",
  "desired_daily_wage": 170000,
  "certifications": ["비계기능사"],
  "completed_count": 48,
  "no_show_count": 1,
  "current_crew_id": null,
  "state_changed_at": "...",
  "created_at": "...", "updated_at": "..."
}
```

설계 원칙:

- `worker_id`는 UUID. **주민등록번호는 해시 포함 어떤 형태로도 저장하지 않는다** (주민등록번호 수집 법정주의).
- 신뢰도는 비율이 아니라 `completed_count` / `no_show_count` 원시값으로 저장하고 필요 시 계산한다.
- 노쇼 이력 등 부정적 데이터는 인력사무소 내부 운영용으로만 사용하며, 건설사 화면·Agent 추천 사유 텍스트에 노출하지 않는다.
- 다중 인력사무소 소속은 P1. P0는 `office_id` 단일 값.

---

## 6. 기술 스택

| 영역 | 기술 |
|---|---|
| Frontend | React SPA, S3 + CloudFront |
| Auth | Amazon Cognito (시드 계정) |
| API | Amazon API Gateway REST |
| Compute | AWS Lambda (Python) |
| Agent | Strands Agents SDK + Amazon Bedrock |
| DB | Amazon DynamoDB (단일 테이블) |
| Event | Amazon EventBridge |
| IaC | AWS SAM |
| 알림 | DynamoDB 인앱 알림 + 프론트 폴링 (P0) |

---

## 7. 레포 구조

```text
CrewMate/
├── frontend/            # C 담당 — React SPA (worker/office/company 화면)
├── backend/
│   ├── functions/       # A 담당 — worker_api / company_request / office_core /
│   │                    #          assignment / notification
│   │   ├── agent_invoke/    # B 담당
│   │   └── gap_event/       # B 담당
│   └── shared/          # A 담당 — db / auth / schemas / state
├── agent/               # B 담당 — crew_agent.py / system_prompt.md / tools/
├── scripts/seed/        # A 담당 — 시드 및 데모 시나리오 데이터
├── tests/
├── template.yaml        # A 담당 — SAM
└── README.md
```

---

## 8. 팀 구성 (3인)

| 담당 | 영역 | 산출물 |
|---|---|---|
| **A — 플랫폼/백엔드** | SAM, DynamoDB, Cognito, 코어 API 5종, 상태머신·조건부 쓰기, 시드 스크립트 | `PRD_A_BACKEND.md` |
| **B — Agent/이벤트** | Crew Composition Agent, Agent Invoke Lambda, 응답 검증·폴백, Gap Event Lambda | `PRD_B_AGENT.md` |
| **C — 프론트엔드/데모** | 3역할 React 화면, 폴링, 노쇼 시뮬레이션, 배포, 데모 리허설 | `PRD_C_FRONTEND.md` |

상세 일정과 통합 지점은 `TEAM_PLAN.md` 참고.

---

## 9. 최종 데모 시나리오

1. **등록과 대기** — WORKER 로그인 → 지원서 등록 → 대기 버튼 → `READY` 확인
2. **요청과 AI 편성** — COMPANY 인력 요청 → OFFICE가 AI 자동 편성 실행 → 추천 3안 카드 확인 → 1안 승인 → 조원 `RUNNING`
3. **노쇼와 긴급 재편성** — 작업조 A+B+C에서 C 노쇼 시뮬레이션 → Gap Event → 동일 Agent EMERGENCY 호출 → A+B+E 추천 → 승인 → E `RUNNING`, 작업조 A+B+E 갱신 → COMPANY 화면에서 변경 확인

---

## 10. Out of Scope (현재 버전)

GPS 지오펜스 출결, 출근 잔디 그래프, 출근/노쇼 확률 예측 ML, SageMaker, 개인별 위험 점수, 자동 급여 정산, 전자 근로계약, SMS/푸시 필수 연동, 사무소 간 경쟁 입찰, Agent 자동 승인.
