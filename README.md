# CrewMate

건설 일용직 인력 중개를 디지털화하는 AWS 서버리스 플랫폼이다.
인력사무소의 전화·종이 기반 작업조 편성을 **상태 기반 배치 + Strands Crew Composition Agent + 근로자 수락 플로우**로 대체하고, 근로자에게 구조화 규칙·Bedrock Knowledge Base·Q-Net 근거를 연결한 스펙 보완 보고서를 제공한다.

프론트엔드 배포: https://d1872k8ivu18th.cloudfront.net

---

## 1. 핵심 컨셉

1. **상태 기반 인력 풀** — 근로자가 대기 버튼을 누르면 `READY`가 되어 편성 후보로 조회된다.
2. **Crew Composition Agent** — 요청 조건(직종·인원·예산·우선순위)과 READY 후보를 종합해 **팀 단위 조합**을 추천한다. AI는 추천만 하고 배정하지 않는다.
3. **이중 Human-in-the-Loop** — 인력사무소가 승인하고, 근로자가 직접 수락해야 배차가 확정된다.
4. **추가 편성(긴급 배차)** — 거절·취소·노쇼·이탈로 결원이 생기면, 수락을 유지한 인원은 고정하고 결원만 동일 Agent(EMERGENCY) 또는 수동으로 충원한다.
5. **스펙 보완 보고서** — Lambda가 자격·NCS Gap을 확정 계산하고 Strands Report Agent가 KB/Q-Net 근거를 조합한다. 완료 보고서는 사용자별 목록에서 다시 연다.

---

## 2. 사용자 역할

| 역할 | 주요 기능 |
|---|---|
| `WORKER` | 회원가입, 지원서 등록, 대기 시작/취소, 배정 제안 수락/거절, 완료 작업 이력, 스펙 보고서 생성·재조회 |
| `OFFICE` | 회원가입(사무소 자동 생성), 후보 조회·정렬, 수동/AI 편성, 임금 조절, 승인, 요청 거절, 추가 편성, 지원서 상세 조회 |
| `COMPANY` | 회원가입, 인력 요청, 확정 작업조 확인, 출근/퇴근 처리, 결원 등록 |

---

## 3. 근로자 상태 머신 (v2)

```text
            대기 시작        사무소 승인(제안 발송)     근로자 수락        건설사 출근 처리      건설사 퇴근 처리
INACTIVE ──────────▶ READY ──────────────▶ NOTIFIED ──────────▶ RESERVED ──────────▶ RUNNING ──────────▶ INACTIVE
                       ▲                      │                     │                                  (이력 적립)
                       │   거절 / 무응답 취소   │                     │
                       └──────────────────────┘◀── 수락 후 취소 ──────┘
```

- 모든 전환은 `ConditionExpression` 조건부 쓰기. 승인 시 조원 전원을 `TransactWriteItems`로 원자 처리(READY→NOTIFIED).
- 근로자는 동시에 하나의 제안(`current_offer`)만 가질 수 있다 — 중복 배치 방지의 새 관문.
- 거절·취소·노쇼·이탈은 GapEvent를 발생시켜 추가 편성 흐름으로 이어진다.

### 기타 상태 enum

```text
WorkRequest: REQUESTED → COMPOSING → PROPOSED → APPROVED → DISPATCHED → RUNNING → COMPLETED (+CANCELLED, REJECTED)
             COMPOSING은 비동기 AI 편성 중 상태이며, 결원 시 요청 진행 상태에도 사용
Crew:        DRAFT → PROPOSED → APPROVED → NOTIFIED → DISPATCHED → RUNNING → COMPLETED (+CANCELLED)
GapEvent:    DETECTED → RECOMPOSING → PROPOSED → APPROVED → FILLED (+FAILED)
GapEvent 유형: NO_SHOW(노쇼) / LEFT_SITE(중도 이탈) / UNAVAILABLE(수락 후 취소) / DECLINED(제안 거절)
CrewMember acceptance: PENDING / ACCEPTED / DECLINED
```

---

## 4. 성실도 (신뢰 지표)

부정적 낙인 없이 이행률만 중립적으로 보여준다.

- 저장: `completed_count`(근무 완료 수), `dispatched_count`(배차 확정 수) — 원시값 2개만.
- 표기: **성실도 10/11** (완료/배차). 수락으로 배차 확정 시 분모 +1, 정상 퇴근 시 분자 +1. 노쇼·이탈·수락 후 취소는 분자가 오르지 않는다. 제안 단계 거절은 배차 확정 전이므로 집계 제외.
- 노출 범위: **인력사무소 내부 화면 한정.** 건설사 응답·화면, Agent 추천 사유 텍스트에는 절대 포함하지 않는다.
- "노쇼", "탈주", "블랙리스트" 등 부정 라벨을 UI·API 응답 문구에 사용하지 않는다 (근로기준법 제40조 취업 방해 금지 관련 리스크 회피 설계).

---

## 5. 직종·임금 모델

- 직종: 단일 `trade` 대신 `preferred_trades[]`(희망) + `excluded_trades[]`(비희망). 배정 직종은 `assigned_trade`로 별도 기록하며 비희망 직종 배정은 검증에서 거부.
- 임금: 사무소가 편성 시 인원별 `offered_wage`를 조절한다(기본값 `desired_daily_wage`). 승인은 항상 `sum(offered_wage) ≤ request.budget`을 검증한다. 예산만 초과하는 AI 조합은 폐기하지 않고 초과 금액 경고와 함께 제시하며, 사무소가 임금을 수정한 뒤 승인한다.
- `GENERAL`(보통인부)은 별도 선호 태그가 필요한 전문 직종이 아니다. 근로자가 `GENERAL`을 명시적으로 제외하지 않았다면 보통인부 슬롯에 배치할 수 있다.

---

## 6. 핵심 흐름

### 일반 편성

```text
건설사 요청 → 사무소 확인 → 비동기 Agent(NORMAL) 추천 또는 수동 편성 → 추천 선택/임금 조절 → 승인
→ 조원 전원 READY→NOTIFIED (원자 전환) + 제안 발송
→ 근로자 개별 수락(NOTIFIED→RESERVED) → 전원 수락 시 DISPATCHED
→ 작업일 건설사 출근 처리(RESERVED→RUNNING) → 퇴근 처리(RUNNING→INACTIVE, 이력 적립)
```

### 추가 편성 (긴급 배차)

```text
결원 발생 (거절 / 수락 후 취소 / 노쇼 / 이탈)
→ GapEvent 생성, 요청 상태 COMPOSING(재편성 중)
→ 수락 유지 인원 = fixed_members 고정 (상태 불변)
→ 잔여 예산 = budget − 고정 인원 offered_wage 합
→ 동일 Agent EMERGENCY 추천 또는 수동 fill-gap
→ 사무소 승인 → 대체자 제안 → 대체자 수락 시 GapEvent FILLED, 작업조 갱신
→ 이 요청에 거절 이력 있는 근로자(declined_worker_ids)는 후보에서 제외
```

---

## 7. 시스템 아키텍처 (100% 서버리스)

```text
React SPA (S3 + CloudFront, 모바일 반응형)
        │
   /auth/signup·login (Cognito 래핑, 백엔드 제공)
        │
   API Gateway REST  ← 폴링 3~5초
        │
   ├─ Core Lambda (auth / worker / company / office / assignment / notification)
   ├─ Agent Invoke Lambda ─ Strands Crew Composition Agent ─ Bedrock
   └─ Spec Report Lambda ─ Strands Report Agent ┬─ Bedrock Knowledge Base
                                                 └─ Q-Net HTTP Adapter + DynamoDB cache
        │
   DynamoDB 8개 코어 테이블 + 보고서 작업/자격 캐시
   S3 Knowledge Source + SSE-KMS Report Output
```

사용하지 않는 것: EC2/ECS, SageMaker·별도 ML, GPS/지오펜스, Amazon Location Service, WebSocket(P0는 폴링).

---

## 8. 데이터 모델 — 엔터티별 테이블

같은 유형의 데이터끼리 테이블을 분리한다. 승인·수락·출근 등 다중 테이블 상태 전환은 **하나의 `TransactWriteItems`**로 원자 처리한다.

| 테이블 | 내용 | PK / SK | GSI |
|---|---|---|---|
| Workers | 지원서·상태·성실도·직종 배열 | `worker_id` | GSI1: `office_id` + `state#worker_id` |
| Offices | 사무소 마스터 (가입 시 자동 생성) | `office_id` | — |
| Companies | 건설사 마스터 | `company_id` | — |
| Requests | 건설사→사무소 요청 로그 (+거절 사유, 거절 근로자 목록) | `request_id` | GSI1: `office_id`+`status#id` / GSI2: `company_id`+`id` |
| Crews | 작업조·Agent 추천안 | `crew_id` | GSI1: `office_id` + `status#id` |
| Assignments | 배치 로그 = CrewMember 원본 (수락 상태·임금·배정 직종·eta) | `crew_id` / `worker_id` | GSI1: `worker_id` + `created_at` (내 배정·작업 이력) |
| GapEvents | 결원 이벤트 로그 (4유형) | `event_id` | GSI1: `office_id` + `status#id` |
| Notifications | 인앱 알림 (+읽음 처리) | `user_id` / `created_at#id` | — |
| QualificationEvidenceCache | Q-Net 공식 정보 캐시(개인정보 없음) | `normalized_name` | — |
| SpecReportJobs | 보고서 상태·S3 키 등 비식별 메타데이터 | `report_id` | OwnerCreatedAtIndex: `owner_user_id`+`created_at` |

- CrewMember의 단일 진실 원천은 Assignments. Crew에는 `member_ids` 요약만 둔다.
- 작업 이력과 협업 이력은 Assignments에서 유도한다 (별도 테이블 없음).
- `worker_id`는 UUID. 주민등록번호는 어떤 형태로도 저장하지 않는다.

---

## 9. API 표면 (요약)

```text
Auth:    POST /auth/signup, POST /auth/login
Worker:  /worker/application, /worker/me, /worker/state/ready|inactive,
         /worker/offer/accept, /worker/offer/decline, /worker/assignments, /worker/history
Company: /company/requests(CRUD), /company/crews/{id}/gap-events,
         /company/crews/{id}/checkin/{workerId}, /company/crews/{id}/checkout/{workerId}
Office:  /office/workers, /office/requests, /office/requests/{id}/reject,
         /office/crews/manual, /office/crews/{id}/approve, /office/crews/{id}/fill-gap,
         /office/crews/{id}/cancel-composition, /office/cancel-offer,
         /office/requests/{id}/agent-compose, /office/gap-events, /office/gap-events/{id}/agent-recompose,
         /office/emergency/{id}/approve
공통:    GET /offices, GET /notifications, POST /notifications/read
Report: POST /reports/spec-gap/jobs, GET /reports/spec-gap/jobs,
        GET /reports/spec-gap/jobs/{reportId}
응답:    { success, data } / { success, error: { code, message } }
```

---

## 10. 팀 문서

| 문서 | 내용 |
|---|---|
| `PROMPT_1_BACKEND_REBUILD.md` | 백엔드·Agent를 계약 v2 + 엔터티별 DB로 개편하는 AI 에이전트 프롬프트 |
| `PROMPT_2_INTEGRATION_E2E.md` | 모노레포 통합, real 전환, E2E 디버깅 프롬프트 |
| `FRONTEND_CHANGES.md` | 프론트엔드 구현 기준 계약 v2 원본 (담당자 C) |

---

## 11. 데모 시나리오 (v2)

1. **가입과 대기** — WORKER 가입(사무소 선택) → 지원서 → 대기 → READY
2. **요청과 AI 편성** — COMPANY 요청 → OFFICE AI 편성 → 추천 카드(예산 초과 시 경고) → 임금 조절 → 승인 → 근로자 앱에서 수락 → DISPATCHED → 출근 처리 → RUNNING
3. **결원과 추가 편성** — 노쇼 시뮬레이션(또는 수락 후 취소) → GapEvent → 잔여 인원 고정 + EMERGENCY 추천 → 승인 → 대체자 수락 → FILLED → COMPANY 화면 갱신
4. **스펙 보고서** — WORKER 지원서 → 직종 선택 → 비동기 보고서 생성 → S3 저장 → 이전 보고서 목록에서 재조회

## 12. Out of Scope

GPS 출결, 출근 확률 예측 ML, SageMaker, 자동 급여 정산, 전자 근로계약, SMS/푸시, WebSocket, 다중 사무소 소속, 무응답 자동 타임아웃 Lambda.

---

## 13. 지원자 스펙 Gap 보고서와 Knowledge Base

지원자 입력은 Lambda에서 먼저 구조화 규칙으로 정규화·판정한다. Strands Agent에는
Amazon Bedrock Knowledge Base 검색과 Q-Net 공식 확인 두 읽기 도구만 등록되며,
Agent는 짧은 `AgentReportDraft` 근거 설명만 작성한다. 판정, Q-Net 원본 필드,
evidence type과 citation은 Lambda가 실제 도구 반환값에서 주입하고 다시 검증한다. 웹 화면에는
Markdown 제목·목록·하이퍼링크를 안전하게 렌더링하고 내부 NCS 코드와 보고서 ID를 노출하지 않는다.
추천 자격증 이름은 Q-Net 공식 상세 페이지로 연결하며, 실제 조회된 시행기관·하는 일·응시자격·
취득방법·시험 일정·수수료만 일반 사용자가 이해할 수 있는 문장으로 표시한다.

근로자 화면에서 생성하는 보고서는 비동기로 실행하고 별도 SSE-KMS S3 버킷에 JSON/Markdown으로 저장한다.
`SpecReportJobs`에는 보고서 본문이나 지원서 원문을 넣지 않고 소유자·상태·S3 키만 저장한다.
사용자는 `OwnerCreatedAtIndex`로 자신의 이전 보고서 목록만 조회한다. 보고서 객체는 31일 후
Standard-IA로 전환되고 61일 후 삭제된다.

서울 리전의 배포는 S3 Vectors 기반 Bedrock Knowledge Base를 사용한다. 전체 자동
배포와 데이터 ingestion, 실제 Retrieve 검증 방법은
[`scripts/kb/README.md`](scripts/kb/README.md)를 참고한다.

---

## 14. 로컬 검증과 배포

```bash
# 백엔드 테스트 및 IaC 검증
UV_CACHE_DIR=/tmp/kiro-uv-cache uv run --isolated --python 3.12 \
  --with-requirements requirements-dev.txt pytest -q
sam validate --lint --template-file template.yaml

# 프론트엔드
cd frontend
npm ci
npm run build

# KB 포함 전체 AWS 배포·데이터 동기화·Retrieve 검증
cd ..
AWS_REGION=ap-northeast-2 STAGE=dev STACK_NAME=crewmate ./scripts/kb/deploy-all.sh

# 시연용 dev DB 전체 초기화 + 72명 근로자/상태별 요청/작업조/이력 생성
.venv/bin/python scripts/seed/seed_showcase.py \
  --stack-name crewmate --stage dev --region ap-northeast-2 --confirm-reset dev
```

시연 시드는 CloudFormation 출력에서 실제 테이블과 Cognito User Pool을 찾아 실행한다. 8개 코어
테이블과 Q-Net 캐시·보고서 작업 메타데이터를 초기화하므로 `--confirm-reset`에 대상 stage를
명시해야 한다. 생성 전 구성만 확인하려면 `--dry-run`을 사용한다. 기본 로그인은
`worker1`, `office1`, `company1`이며 공통 비밀번호는 `demo1234`다.

일반 SAM 변경만 배포할 때는 `sam build` 후 `sam deploy`를 사용한다. 운영 배포에서는
`CorsAllowOrigin`을 CloudFront 도메인으로 제한하고 Crew/Report 모델 ID와 리전을 파라미터로 고정한다.
