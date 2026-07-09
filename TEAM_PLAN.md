# CrewMate — 3인 분업 계획 (6일 스프린트)

> 각자 자기 PRD(`PRD_A_BACKEND.md` / `PRD_B_AGENT.md` / `PRD_C_FRONTEND.md`)를 Kiro에 입력해
> requirements → design → tasks를 생성하고 자기 디렉터리 안에서만 작업한다.
> 세 PRD에 동일하게 들어 있는 **"공유 계약(Shared Contracts)" 섹션은 절대 임의 수정하지 않는다.**
> 계약 변경이 필요하면 팀 합의 후 세 PRD를 동시에 수정한다.

---

## 1. 역할 분담

### A — 플랫폼 / 백엔드 코어

- AWS SAM 프로젝트, DynamoDB 단일 테이블 + GSI, Cognito 시드 계정 3종, API Gateway
- Lambda 5종: `worker_api`, `company_request`, `office_core`, `assignment`, `notification`
- 상태머신 구현: READY → RESERVED → RUNNING 조건부 쓰기 / TransactWriteItems
- `backend/shared/` 공용 모듈 (db 접근, 권한 체크, 응답 포맷, 상태 상수)
- 시드 스크립트: 근로자 50~100명, 사무소 2곳, 건설사 2~3곳, 협업 이력, 긴급 데모 세트(A,B,C,D,E,F)

### B — Agent / 이벤트

- Crew Composition Agent (Strands Agents SDK + Bedrock): NORMAL / EMERGENCY 단일 Agent
- Agent Tools 4종: `get_request_detail`, `get_ready_workers`, `get_worker_history`, `get_current_crew`
- `agent_invoke` Lambda: 입력 조립 → Agent 호출 → JSON 스키마 검증 → 저장 → 재시도 1회 → 수동 폴백
- `gap_event` Lambda: 노쇼/이탈 저장, fixed_members·결원 직종 계산, EMERGENCY payload 생성
- Agent 로깅 (후보 수, 모드, 검증 성공/실패, 재시도 여부)

### C — 프론트엔드 / 데모

- React SPA: worker / office / company 3역할 화면 전체
- 역할별 로그인·라우팅, 폴링 기반 상태 갱신 (3~5초)
- Agent 추천 카드 UI, 승인 플로우, 노쇼 시뮬레이션 버튼, 긴급 재편성 화면
- mock API 레이어 (백엔드 완성 전 개발용) → 실 API 전환 스위치
- S3 + CloudFront 배포, 데모 시나리오 리허설 주도

---

## 2. 일자별 계획

### Day 1 — 계약 확정 + 골격 (오전에 전원 계약 리뷰 30분)

| A | B | C |
|---|---|---|
| SAM 프로젝트, DynamoDB 테이블+GSI 생성, Cognito 시드 계정, API Gateway 골격, `shared/` 응답·상태 모듈 | Bedrock 모델 액세스 확인, Agent 로컬 프로토타입(하드코딩 후보 → JSON 추천 출력 확인) | React 프로젝트, 로그인 화면, 역할별 라우팅 셸, mock API 레이어 설계 |

**Day 1 완료 기준**: 3계정 로그인 → 역할별 빈 화면 표시. Agent가 로컬에서 스키마에 맞는 JSON 반환.

### Day 2 — 근로자·요청 흐름

| A | B | C |
|---|---|---|
| worker_api(지원서 CRUD, 대기 시작/취소), company_request(요청 CRUD), 시드 스크립트 v1 | Agent 출력 JSON Schema 확정, 검증 모듈(pydantic 등) 작성, 실패 케이스 단위 테스트 | 근로자 지원서/상태 화면, 건설사 요청 생성·목록 화면 (mock API로) |

**완료 기준**: 지원서 등록 → 대기 → READY → 건설사 요청 → 사무소 요청 목록 노출 (API 직접 호출 기준).

### Day 3 — 수동 편성 + 승인 (핵심 동시성)

| A | B | C |
|---|---|---|
| office_core(후보 조회·수동 편성), assignment(승인→RESERVED→RUNNING 조건부 쓰기, TransactWriteItems, 실패 롤백) | agent_invoke NORMAL 모드: 실 DB에서 READY 후보 조립 → Agent 호출 → 검증 → Crew(PROPOSED) 저장 | 사무소 요청 목록·상세, READY 후보 테이블, 수동 편성 화면. 실 API 전환 시작 |

**완료 기준**: 수동 편성 → 승인 → 조원 전체 RUNNING. 동시 승인 충돌 시 `STATE_CONFLICT` 반환 확인.

### Day 4 — Agent 편성 E2E

| A | B | C |
|---|---|---|
| B·C 통합 지원, notification Lambda, 시드 v2(협업 이력 포함) | NORMAL 모드 완성: 추천 1~3안, 재시도·수동 폴백, Bedrock 실패 대비 사전 준비 응답 | AI 자동 편성 버튼, 추천 카드(조합·비용·사유), 승인 UI, 폴링 연결 |

**완료 기준**: 데모 시나리오 2 전체 통과 (요청 → AI 편성 → 승인 → RUNNING).

### Day 5 — 긴급 재편성

| A | B | C |
|---|---|---|
| 긴급 승인·배차 API(`/office/emergency/{eventId}/approve`), 이탈자 INACTIVE 처리 | gap_event Lambda, EMERGENCY 모드(fixed_members 유지), Gap Event 상태 전이(DETECTED→…→FILLED) | 노쇼 시뮬레이션 버튼, 긴급 재편성 화면, 건설사 작업조 변경 표시, 알림 목록 |

**완료 기준**: 데모 시나리오 3 전체 통과 (C 노쇼 → A+B+E 추천 → 승인 → 작업조 갱신).

### Day 6 — 통합·데모

전원: E2E 3시나리오 반복 테스트 → 오류 처리·UI 다듬기 → 시드 데이터 고정(seed=42) → 데모 리셋 스크립트 확인 → 발표 리허설 2회.

---

## 3. 통합 지점과 의존성

```text
A의 DB 키 설계·API 계약  ──▶  B (후보 조회, 추천 저장)
                          ──▶  C (모든 화면)
B의 Agent 출력 스키마     ──▶  C (추천 카드 렌더링)
C의 mock API 레이어       ──▶  C 자신 (A 완성 전 병렬 개발)
```

- **C는 A를 기다리지 않는다**: Day 1~3은 mock API로 개발하고, 계약(응답 형식·필드명)만 지키면 Day 3~4에 스위치 하나로 전환된다.
- **B는 Day 1~2에 DB 없이 개발한다**: 하드코딩 후보 목록으로 Agent 품질을 먼저 확보하고, Day 3에 실 조회로 교체한다.
- 매일 저녁 15분 스탠드업: 계약 위반 여부, 블로커, 다음 날 통합 항목 확인.

## 4. 리스크 오너

| 리스크 | 오너 | 폴백 |
|---|---|---|
| Agent JSON 형식 오류 | B | 스키마 검증 → 1회 재시도 → 수동 편성 폴백 |
| Bedrock 호출 실패/지연 | B | 사전 준비된 데모 추천 응답 |
| 승인 시점 중복 배정 | A | 조건부 쓰기 + TransactWriteItems |
| Cognito 셋업 지연 | A | 시드 계정 + 단순 로그인 |
| 백엔드 지연으로 프론트 블로킹 | C | mock API 레이어 유지 |
| 데모 중 데이터 오염 | A | 원클릭 시드 리셋 스크립트 |
