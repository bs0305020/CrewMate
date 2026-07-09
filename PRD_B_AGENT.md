# CrewMate PRD — B: Crew Composition Agent / 이벤트 (Kiro 입력용)

> **용도**: 이 문서를 Kiro Spec 모드에 입력하여 담당자 B 범위의 `requirements.md → design.md → tasks.md`를 생성한다.
> 지시문: **"이 PRD를 기반으로 requirements.md, design.md, tasks.md를 순서대로 생성해줘. 문서에 정의되지 않은 기능·ML 모델을 임의로 추가하지 마. '담당 범위 밖' 섹션의 항목은 구현하지 마."**

---

## 0. 프로젝트 컨텍스트

CrewMate는 건설 일용직 작업조 편성을 디지털화하는 100% 서버리스 웹 서비스다.
역할은 `WORKER`, `OFFICE`, `COMPANY` 3종이다.

이 문서의 담당 범위: **Crew Composition Agent(프로젝트의 유일한 AI 구성 요소) + Agent Invoke Lambda + Gap Event Lambda.**
Agent는 일반 편성(NORMAL)과 긴급 재편성(EMERGENCY)을 **하나의 동일한 Agent**로 처리한다.

---

## 1. 공유 계약 (Shared Contracts) — 임의 변경 금지

이 섹션은 팀 3인의 PRD에 동일하게 포함된 계약이다. Kiro는 이 계약을 절대 변경하지 않는다.

### 1.1 절대 원칙

1. 별도 ML 모델 없음 (SageMaker, XGBoost, 출근/노쇼 확률 예측 모델 금지)
2. 일반 편성과 긴급 재편성은 **동일한 Crew Composition Agent** 사용 — 별도 긴급 Agent 금지
3. Agent는 추천만 하고, 실제 배정·상태 변경은 인력사무소 승인 후 Lambda가 수행 (Human-in-the-Loop)
4. 100% 서버리스: EC2/ECS/상시 서버 금지
5. GPS 지오펜스·Amazon Location Service 금지, 노쇼는 시뮬레이션 버튼으로 발생
6. P0 알림은 DynamoDB 인앱 알림 + 프론트 폴링 (WebSocket은 P1)

### 1.2 근로자 상태 머신

```text
INACTIVE → (대기 시작) → READY → (승인·조건부 쓰기) → RESERVED → (배차 완료) → RUNNING → (작업 종료) → INACTIVE
RESERVED → (배차 취소/실패) → READY
```

- Agent의 신규 후보는 `READY`만 사용한다. `RESERVED`/`RUNNING`은 신규 후보 금지.
- 긴급 재편성 시 기존 정상 팀원은 `RUNNING` 유지 상태로 `fixed_members`가 된다.

### 1.3 기타 상태 enum

```text
WorkRequest: REQUESTED → COMPOSING → PROPOSED → APPROVED → RUNNING → COMPLETED (+CANCELLED)
Crew:        DRAFT → PROPOSED → APPROVED → RUNNING → COMPLETED (+CANCELLED)
GapEvent:    DETECTED → RECOMPOSING → PROPOSED → APPROVED → FILLED (+FAILED)
GapEvent 유형: NO_SHOW / LEFT_SITE / UNAVAILABLE
```

### 1.4 DynamoDB 단일 테이블 `CrewMate`

| 엔터티 | PK | SK | GSI1PK / GSI1SK |
|---|---|---|---|
| Worker | `WORKER#{worker_id}` | `PROFILE` | `OFFICE#{office_id}` / `STATE#{state}#W#{worker_id}` |
| WorkRequest | `REQ#{request_id}` | `META` | `OFFICE#{office_id}` / `REQ#{status}#{request_id}` |
| Crew | `CREW#{crew_id}` | `META` | `OFFICE#{office_id}` / `CREW#{status}#{crew_id}` |
| GapEvent | `GAP#{event_id}` | `META` | `OFFICE#{office_id}` / `GAP#{status}#{event_id}` |
| Collaboration | `WORKER#{worker_id}` | `COLLAB#{other_id}#{date}` | — |

- 사무소의 READY 후보 조회: GSI1 `GSI1PK = OFFICE#{id}` AND `begins_with(GSI1SK, 'STATE#READY#')`
- DB 접근은 담당자 A가 제공하는 `backend/shared/db` 헬퍼를 사용한다.

### 1.5 Worker 주요 속성 (Agent 판단 입력)

`worker_id(UUID), name, office_id, state, trade, skill_level(1~5), career_years, region, desired_daily_wage, certifications[], completed_count, no_show_count, current_crew_id`

데이터 원칙:

- 주민등록번호는 존재하지 않으며 어떤 형태로도 다루지 않는다.
- `no_show_count` 등 부정적 데이터는 내부 판단에만 사용하고 **Agent가 생성하는 추천 사유 텍스트에 특정 근로자에 대한 부정적 평가 문구를 포함하지 않는다.**
- `trade` enum 예시: `FORMWORK, REBAR, MASONRY, MATERIAL_CARRY, GENERAL`

### 1.6 API 응답 형식

```json
{ "success": true, "data": {} }
{ "success": false, "error": { "code": "AGENT_OUTPUT_INVALID", "message": "..." } }
```

관련 오류 코드: `AGENT_OUTPUT_INVALID, AGENT_RETRY_FAILED, GAP_EVENT_NOT_FOUND, STATE_CONFLICT, CREW_INVALID`

### 1.7 권한 요약

- Agent 실행은 OFFICE 역할만 트리거할 수 있다.
- COMPANY는 결원 이벤트 등록만 가능하며 Agent를 직접 실행할 수 없다.

---

## 2. 담당 범위

### 포함 (이 PRD로 구현)

1. `agent/` — Crew Composition Agent (Strands Agents SDK + Amazon Bedrock)
2. Agent Tools 4종: `get_request_detail`, `get_ready_workers`, `get_worker_history`, `get_current_crew`
3. `backend/functions/agent_invoke/` Lambda — NORMAL/EMERGENCY 실행, 검증, 저장, 폴백
4. `backend/functions/gap_event/` Lambda — 결원 이벤트 처리, EMERGENCY payload 조립
5. Agent 관측성 로그

### 담당 범위 밖 (구현 금지 — 다른 팀원 담당)

- DynamoDB 테이블 정의, Cognito, 코어 API(worker/company/office/assignment/notification) → 담당자 A. shared 헬퍼를 소비만 한다.
- React 화면 전체 → 담당자 C.
- **승인·상태 변경 로직을 이 범위에서 재구현하지 않는다.** 승인은 A의 `/office/.../approve` API가 처리한다.

---

## 3. 기능 요구사항

### F-B1. Crew Composition Agent [P0] — 프로젝트 핵심

Agent가 답하는 질문:

- NORMAL: "요청 조건과 가용 근로자 기준으로 어떤 작업조 구성이 가장 적절한가?"
- EMERGENCY: "남은 팀원을 유지하면서 READY 후보 중 누구를 추가해야 전체 조합이 가장 적절한가?"

Agent 입력 (Lambda가 조립하여 전달):

```json
{
  "mode": "NORMAL | EMERGENCY",
  "request": { "required_workers": [], "budget": 450000, "priority": {"cost":"HIGH","skill":"MEDIUM","teamwork":"HIGH"}, "site": "...", "work_date": "...", "start_time": "..." },
  "fixed_members": [],
  "candidates": [ { "worker_id": "...", "trade": "...", "skill_level": 4, "desired_daily_wage": 170000, "certifications": [], "career_years": 7 } ],
  "collaboration_pairs": [ { "worker_a": "...", "worker_b": "...", "count": 3 } ]
}
```

Agent 출력 (JSON만, 다른 텍스트 금지):

```json
{
  "mode": "NORMAL",
  "request_id": "REQ_001",
  "recommendations": [
    {
      "rank": 1,
      "member_ids": ["W001", "W014", "W027"],
      "total_cost": 430000,
      "reason": "필요 직종 구성을 충족하며 예산 범위 안에서 숙련도와 기존 협업 경험의 균형이 가장 좋습니다.",
      "considerations": ["필수 직종 충족", "예산 범위 충족", "A-B 공동 작업 이력 존재"]
    }
  ]
}
```

수용 기준:

- Agent는 후보 목록에 없는 `worker_id`를 생성하지 않는다.
- Agent는 필수 직종·인원 제약을 충족하는 추천안을 1~3개 반환한다.
- EMERGENCY 모드에서 `fixed_members`는 모든 추천안에 그대로 포함된다.
- 단순 개인 점수 나열이 아니라 팀 조합(협업 이력·직종 균형·예산)을 근거로 판단한다.
- 추천 사유에 특정 근로자에 대한 부정적 평가 문구를 생성하지 않는다.
- "절대 최적", "출근 확률 97%" 류의 확률·보장 표현을 생성하지 않는다.
- Agent는 DB를 임의 범위로 조회하지 않는다. Tool 또는 Lambda가 `office_id + state=READY`로 조회한 후보만 받는다.

### F-B2. Agent System Prompt [P0]

`agent/system_prompt.md`에 최소 다음 제약을 포함한다:

```text
1. 제공된 후보 목록에 없는 근로자를 만들거나 추천하지 않는다.
2. 신규 후보는 READY 상태의 근로자만 사용한다.
3. NORMAL 모드에서는 요청 조건을 충족하는 작업조를 구성한다.
4. EMERGENCY 모드에서는 fixed_members를 유지하고 부족 인원을 candidates에서 보충한다.
5. 필수 직종과 인원 제약을 반드시 준수한다.
6. 비용, 숙련도, 협업 이력과 요청 우선순위를 종합 판단한다.
7. 개인 나열이 아닌 전체 팀 조합을 평가한다.
8. 결과는 지정된 JSON 스키마로만 반환한다.
9. 최종 배정이나 상태 변경을 수행하지 않는다.
10. 추천 이유는 업무 정보 중심으로 간결하게, 근로자에 대한 부정적 표현 없이 작성한다.
```

### F-B3. Agent Tools [P0]

조회 전용 Tool 4종만 제공한다:

| Tool | 입력 | 출력 |
|---|---|---|
| `get_request_detail` | `request_id` | 요청 상세 조건 |
| `get_ready_workers` | `office_id, required_trades[]` | 해당 사무소 READY 후보 |
| `get_worker_history` | `worker_ids[]` | 작업·협업 이력 (제한된 정보) |
| `get_current_crew` | `crew_id` | 현재 멤버, 활성 멤버, 결원, 요구 조건 |

**Agent에게 절대 주지 않는 Tool**: `update_worker_state, approve_crew, assign_worker, mark_running, delete_worker, update_company_request` — 쓰기 계열 일체 금지.

폴백 단순화: Tool 복잡도가 문제 되면 Lambda가 후보 데이터를 미리 조립해 Agent에 한 번에 전달하는 방식으로 전환할 수 있다.

### F-B4. Agent Invoke Lambda [P0]

Route:

```text
POST /office/requests/{requestId}/agent-compose        NORMAL
POST /office/gap-events/{eventId}/agent-recompose      EMERGENCY
```

처리 흐름:

```text
요청/이벤트 조회 → 후보 조립 (office_id + READY)
→ Agent 호출
→ 구조화 응답 검증
→ 검증 통과: 추천안을 Crew(status = PROPOSED, source = AGENT)로 저장, Request status = PROPOSED
→ 검증 실패: 결과 폐기 → 오류 로그 → Agent 1회 재시도 → 재실패 시 AGENT_RETRY_FAILED 반환 (프론트는 수동 편성으로 폴백)
```

검증 항목 (Lambda에서 코드로 검증, LLM 신뢰 금지):

```text
1. member_id가 실제 후보 목록(또는 fixed_members)에 존재하는가
2. 신규 추천 인원이 모두 READY 상태인가
3. 중복 worker_id가 없는가
4. 필수 직종·인원이 충족되는가
5. total_cost가 서버 계산값(후보 desired_daily_wage 합)과 일치하는가
6. 같은 worker가 다른 RUNNING/RESERVED 작업에 포함되어 있지 않은가
7. EMERGENCY: fixed_members가 그대로 유지되는가
```

수용 기준:

- 추천 결과는 저장만 하고 자동 승인하지 않는다. 승인은 OFFICE 사용자가 A의 승인 API로 수행한다.
- WHEN Bedrock 호출이 실패하거나 지연되면, THE SYSTEM SHALL 사전 준비된 데모 추천 응답으로 폴백할 수 있는 플래그를 제공한다 (데모 안정성).

### F-B5. Gap Event Lambda [P0]

트리거: COMPANY/OFFICE의 결원 등록(EventBridge 경유 또는 직접 호출).

처리 흐름:

```text
GapEvent 저장 (status = DETECTED, type = NO_SHOW | LEFT_SITE | UNAVAILABLE)
→ 영향 Crew 조회
→ 이탈자를 활성 멤버에서 제외 목록으로 계산 (상태 변경 자체는 A의 API 경로)
→ 잔여 정상 팀원 = fixed_members 계산 (RUNNING 유지)
→ 결원 직종·인원 계산
→ EMERGENCY payload 생성 → agent_invoke 호출 (status = RECOMPOSING)
→ 추천 저장 완료 시 status = PROPOSED
```

수용 기준:

- WHEN 결원 이벤트가 발생하면, THE SYSTEM SHALL 5초 내(폴링 주기 내) OFFICE 화면에서 긴급 재편성 상태를 조회할 수 있도록 GapEvent를 저장한다.
- 긴급 재편성이 승인·완료될 때까지 잔여 팀원의 RUNNING 상태를 건드리지 않는다.
- 재편성 실패(재시도 소진) 시 GapEvent를 `FAILED`로 남기고 수동 편성 경로를 안내한다.

### F-B6. Agent 관측성 [P1]

CloudWatch 구조화 로그: `agent_mode, agent_execution_id, 입력 후보 수, 추천안 개수, 검증 성공/실패, 재시도 여부, 최종 저장 여부`.
근로자 개인정보 전체를 디버그 로그에 출력하지 않는다.

---

## 4. 마일스톤 (담당자 B)

| Day | 목표 | 완료 기준 |
|---|---|---|
| 1 | Bedrock 액세스 확인 + Agent 로컬 프로토타입 | 하드코딩 후보로 스키마에 맞는 JSON 추천 출력 |
| 2 | 출력 JSON Schema 확정 + 검증 모듈 + 실패 단위 테스트 | 잘못된 출력 7종 케이스가 전부 검출됨 |
| 3 | agent_invoke NORMAL 모드 (실 DB 후보) | 실 요청 → 추천 1~3안 → Crew(PROPOSED) 저장 |
| 4 | NORMAL 완성: 재시도·폴백·사전 준비 응답 | Bedrock 강제 실패 시에도 데모 경로 유지 |
| 5 | gap_event + EMERGENCY 모드 | C 노쇼 → A+B 고정 → A+B+E 추천 저장 |
| 6 | 통합·프롬프트 튜닝·리허설 | E2E 시나리오 2·3 반복 통과 |

---

## 5. 금지 사항

1. ML 모델을 설계·학습·서빙하지 않는다 (SageMaker 금지).
2. 일반/긴급을 서로 다른 Agent로 만들지 않는다.
3. Agent에게 쓰기 Tool을 주지 않는다. Agent가 worker state를 직접 변경하지 않는다.
4. 인력사무소 승인 절차를 생략하거나 자동 승인하지 않는다.
5. `RUNNING`/`RESERVED` 근로자를 신규 후보로 추천하지 않는다.
6. 승인·배정·상태 변경 로직을 이 범위에서 구현하지 않는다 — 담당자 A의 API를 사용한다.
7. 프론트엔드 코드를 작성하지 않는다 — 담당자 C 영역.
8. 추천 사유에 확률 수치·최적 보장·근로자 부정 평가를 생성하지 않는다.
