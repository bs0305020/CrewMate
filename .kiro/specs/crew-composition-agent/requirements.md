# Requirements Document

## Introduction

CrewMate는 건설 일용직 작업조 편성을 디지털화하는 100% 서버리스 웹 서비스다. 이 스펙은 담당자 B 범위인 **Crew Composition Agent + Agent Invoke Lambda + Gap Event Lambda + Agent 관측성**을 정의한다.

Crew Composition Agent는 프로젝트의 유일한 AI 구성 요소로, 일반 편성(NORMAL)과 긴급 재편성(EMERGENCY)을 **하나의 동일한 Agent**로 처리한다. Agent는 조회·추천만 수행하며, 실제 배정·상태 변경은 인력사무소(OFFICE) 승인 이후 담당자 A의 Lambda가 수행한다(Human-in-the-Loop). Agent 출력은 코드로 검증하며(LLM 신뢰 금지), 검증을 통과한 추천안만 저장한다.

### 범위 밖 (Non-Goals) — 본 스펙에서 구현하지 않음

- DynamoDB 테이블 정의, Cognito, 코어 API(worker/company/office/assignment/notification) — 담당자 A. 본 범위는 `backend/shared/db` 등 shared 헬퍼를 **소비만** 한다.
- React 화면 전체 — 담당자 C.
- 승인·배정·상태 전이(READY→RESERVED→RUNNING) 로직 재구현 — 담당자 A의 `/office/.../approve` API가 수행한다.
- 별도 ML 모델(SageMaker, XGBoost, 출근/노쇼 확률 예측) — 사용하지 않는다.
- 긴급 전용 별도 Agent — 만들지 않는다(단일 Agent 사용).

## Glossary

- **Crew_Composition_Agent**: Strands Agents SDK와 Amazon Bedrock 기반의 단일 추천 Agent. NORMAL과 EMERGENCY 두 모드를 처리하며 조회·추천만 수행한다.
- **Agent_Invoke_Lambda**: `backend/functions/agent_invoke/`의 Lambda. 후보 조립, Agent 호출, 출력 검증, 저장, 재시도, 폴백을 담당한다.
- **Gap_Event_Lambda**: `backend/functions/gap_event/`의 Lambda. 결원 이벤트를 저장하고 EMERGENCY payload를 조립해 Agent_Invoke_Lambda를 호출하며 GapEvent 상태를 전이한다.
- **Agent_System_Prompt**: `agent/system_prompt.md` 파일에 저장되는 Agent 제약 프롬프트.
- **NORMAL**: 일반 편성 모드.
- **EMERGENCY**: 긴급 재편성 모드.
- **candidates**: office_id가 일치하고 state=READY인 신규 후보 근로자 목록.
- **fixed_members**: EMERGENCY 모드에서 RUNNING 상태를 유지하는 잔여 정상 팀원 목록.
- **READY / RESERVED / RUNNING**: 근로자 상태 머신 상태값. 신규 후보는 READY만 사용한다.
- **Crew**: 작업조 엔터티. 상태값 DRAFT → PROPOSED → APPROVED → RUNNING → COMPLETED (+CANCELLED).
- **WorkRequest**: 건설사 인력 요청 엔터티. 상태값 REQUESTED → COMPOSING → PROPOSED → APPROVED → RUNNING → COMPLETED (+CANCELLED).
- **GapEvent**: 결원 이벤트 엔터티. 상태값 DETECTED → RECOMPOSING → PROPOSED → APPROVED → FILLED (+FAILED). 유형 NO_SHOW / LEFT_SITE / UNAVAILABLE.
- **source=AGENT**: Agent가 생성한 Crew임을 표시하는 필드값.
- **OFFICE / COMPANY / WORKER**: 사용자 역할.
- **Shared_DB_Helper**: 담당자 A가 제공하는 `backend/shared/db` 접근 헬퍼. 본 범위는 소비만 한다.
- **Shared_Auth_Helper**: 담당자 A가 제공하는 `backend/shared/auth` 권한 확인 헬퍼. 본 범위는 소비만 한다.
- **Demo_Fallback_Response**: Bedrock 실패·지연에 대비해 사전 준비된 데모 추천 응답.

## Requirements

### Requirement 1: 단일 Crew Composition Agent 추천 생성

**User Story:** As an OFFICE user, I want a single agent to compose crews for both normal and emergency modes, so that I get consistent team-level recommendations without a separate emergency agent.

#### Acceptance Criteria

1. THE Agent_Invoke_Lambda SHALL invoke the same Crew_Composition_Agent for both NORMAL mode and EMERGENCY mode.
2. WHEN the Agent_Invoke_Lambda invokes the Crew_Composition_Agent with mode=NORMAL, THE Crew_Composition_Agent SHALL generate crew composition recommendations based on the request conditions and the candidates.
3. WHEN the Agent_Invoke_Lambda invokes the Crew_Composition_Agent with mode=EMERGENCY, THE Crew_Composition_Agent SHALL keep the fixed_members and fill the shortage from the candidates.
4. THE Crew_Composition_Agent SHALL return between 1 and 3 recommendations that satisfy the required trade and headcount constraints.
5. WHILE mode is EMERGENCY, THE Crew_Composition_Agent SHALL include every fixed_members entry in the member_ids of every recommendation.
6. THE Crew_Composition_Agent SHALL include only worker_id values that exist in the candidates list or in the fixed_members list.
7. THE Crew_Composition_Agent SHALL base each recommendation on team combination factors including collaboration history, trade balance, and budget rather than on individual score rankings.
8. THE Crew_Composition_Agent SHALL operate only on candidates retrieved with the office_id and state=READY scope.
9. THE Crew_Composition_Agent SHALL generate recommendations without a separate ML model or probability prediction model.

### Requirement 2: Agent 입력/출력 스키마

**User Story:** As a developer integrating the agent, I want a fixed input and output contract, so that the Lambda can assemble input and validate output deterministically.

#### Acceptance Criteria

1. THE Agent_Invoke_Lambda SHALL construct the Crew_Composition_Agent input containing mode, request, fixed_members, candidates, and collaboration_pairs.
2. THE Agent_Invoke_Lambda SHALL include in the request object the fields required_workers, budget, priority (with cost, skill, and teamwork weights), site, work_date, and start_time.
3. THE Agent_Invoke_Lambda SHALL include for each candidate the fields worker_id, trade, skill_level, desired_daily_wage, certifications, and career_years.
4. THE Agent_Invoke_Lambda SHALL include for each collaboration_pairs entry the fields worker_a, worker_b, and count.
5. THE Crew_Composition_Agent SHALL return output containing mode, request_id, and recommendations.
6. THE Crew_Composition_Agent SHALL include for each recommendation the fields rank, member_ids, total_cost, reason, and considerations.
7. THE Crew_Composition_Agent SHALL return only JSON that conforms to the output schema, without any additional text.

### Requirement 3: 추천 사유 언어 제약

**User Story:** As an OFFICE user, I want recommendation reasons to stay work-focused and neutral, so that no worker is unfairly characterized and no misleading guarantees are shown.

#### Acceptance Criteria

1. THE Crew_Composition_Agent SHALL compose the reason and considerations text using only work-related information including trade composition, budget, skill level, and collaboration history.
2. THE Crew_Composition_Agent SHALL keep the generated reason and considerations text free of negative evaluations about any specific worker.
3. THE Crew_Composition_Agent SHALL keep the generated reason and considerations text free of negative operational data such as no_show_count.
4. THE Crew_Composition_Agent SHALL keep the generated reason and considerations text free of probability figures and optimality-guarantee expressions.

### Requirement 4: Agent System Prompt

**User Story:** As a developer, I want the agent constraints codified in a system prompt file, so that the agent behavior is governed consistently.

#### Acceptance Criteria

1. THE Agent_System_Prompt SHALL be stored in the file agent/system_prompt.md.
2. THE Agent_System_Prompt SHALL instruct the Crew_Composition_Agent to not create or recommend a worker that is absent from the provided candidate list.
3. THE Agent_System_Prompt SHALL instruct the Crew_Composition_Agent to use only READY-state workers as new candidates.
4. THE Agent_System_Prompt SHALL instruct the Crew_Composition_Agent to compose a crew that satisfies the request conditions in NORMAL mode.
5. THE Agent_System_Prompt SHALL instruct the Crew_Composition_Agent to maintain the fixed_members and fill the shortage from the candidates in EMERGENCY mode.
6. THE Agent_System_Prompt SHALL instruct the Crew_Composition_Agent to comply with the required trade and headcount constraints.
7. THE Agent_System_Prompt SHALL instruct the Crew_Composition_Agent to synthesize cost, skill level, collaboration history, and request priority.
8. THE Agent_System_Prompt SHALL instruct the Crew_Composition_Agent to evaluate the whole team combination rather than listing individuals.
9. THE Agent_System_Prompt SHALL instruct the Crew_Composition_Agent to return results only in the specified JSON schema.
10. THE Agent_System_Prompt SHALL instruct the Crew_Composition_Agent to not perform final assignment or state changes.
11. THE Agent_System_Prompt SHALL instruct the Crew_Composition_Agent to write the reason concisely on work information without negative expressions about workers.

### Requirement 5: Agent Tools (조회 전용)

**User Story:** As a security-conscious developer, I want the agent to have only read tools, so that the agent can never write state directly.

#### Acceptance Criteria

1. THE Crew_Composition_Agent SHALL be provided exactly four read-only tools named get_request_detail, get_ready_workers, get_worker_history, and get_current_crew.
2. WHEN get_request_detail is invoked with a request_id, THE get_request_detail tool SHALL return the request detail conditions.
3. WHEN get_ready_workers is invoked with an office_id and required_trades, THE get_ready_workers tool SHALL return the READY candidates of that office.
4. WHEN get_worker_history is invoked with worker_ids, THE get_worker_history tool SHALL return the limited work and collaboration history for those workers.
5. WHEN get_current_crew is invoked with a crew_id, THE get_current_crew tool SHALL return the current members, active members, gaps, and required conditions.
6. THE Crew_Composition_Agent SHALL be provided no write-capable tools, including update_worker_state, approve_crew, assign_worker, mark_running, delete_worker, and update_company_request.
7. WHERE tool complexity is a problem, THE Agent_Invoke_Lambda SHALL be able to assemble candidate data in advance and pass it to the Crew_Composition_Agent in a single call.

### Requirement 6: Agent Invoke Lambda 실행 및 후보 조립

**User Story:** As an OFFICE user, I want the invoke Lambda to assemble READY candidates and run the agent for the requested mode, so that composition uses only valid, in-scope candidates.

#### Acceptance Criteria

1. WHEN the Agent_Invoke_Lambda receives POST /office/requests/{requestId}/agent-compose, THE Agent_Invoke_Lambda SHALL run in NORMAL mode.
2. WHEN the Agent_Invoke_Lambda receives POST /office/gap-events/{eventId}/agent-recompose, THE Agent_Invoke_Lambda SHALL run in EMERGENCY mode.
3. THE Agent_Invoke_Lambda SHALL assemble the candidates using only workers whose office_id matches and whose state is READY.
4. THE Agent_Invoke_Lambda SHALL retrieve candidate data through the Shared_DB_Helper without implementing its own table access.
5. WHEN the candidates are assembled, THE Agent_Invoke_Lambda SHALL invoke the Crew_Composition_Agent with the assembled input.
6. IF the target WorkRequest or GapEvent is not in the expected state at invocation time, THEN THE Agent_Invoke_Lambda SHALL return the STATE_CONFLICT error.
7. IF multiple compose or recompose requests are received for the same WorkRequest or GapEvent while one request is already being processed, THEN THE Agent_Invoke_Lambda SHALL reject subsequent requests with STATE_CONFLICT instead of queueing them.

### Requirement 7: Agent 출력 코드 검증

**User Story:** As an OFFICE user, I want the agent output validated by code rather than trusted, so that only structurally correct and rule-compliant recommendations are stored.

#### Acceptance Criteria

1. WHEN the Agent_Invoke_Lambda receives Crew_Composition_Agent output, THE Agent_Invoke_Lambda SHALL validate the output with server-side code rather than trusting the model.
2. THE Agent_Invoke_Lambda SHALL verify that each member_id exists in the candidates list or in the fixed_members list.
3. THE Agent_Invoke_Lambda SHALL verify that every newly recommended worker is in the READY state.
4. THE Agent_Invoke_Lambda SHALL verify that no worker_id is duplicated within a recommendation.
5. THE Agent_Invoke_Lambda SHALL verify that the required trade and headcount are satisfied.
6. THE Agent_Invoke_Lambda SHALL verify that total_cost equals the server-computed sum of the recommended workers' desired_daily_wage.
7. THE Agent_Invoke_Lambda SHALL verify that no recommended worker is included in another RUNNING or RESERVED assignment.
8. WHILE mode is EMERGENCY, THE Agent_Invoke_Lambda SHALL verify that the fixed_members are preserved unchanged in every recommendation.
9. IF any validation check fails, THEN THE Agent_Invoke_Lambda SHALL reject the output as AGENT_OUTPUT_INVALID and SHALL discard the recommendation without saving.

### Requirement 8: 검증 통과 시 저장 (승인은 위임)

**User Story:** As an OFFICE user, I want validated recommendations stored as proposals only, so that assignment happens after my explicit approval.

#### Acceptance Criteria

1. WHEN the agent output passes all validation checks, THE Agent_Invoke_Lambda SHALL store the recommendation as a Crew with status=PROPOSED and source=AGENT.
2. WHEN the recommendation is stored as a Crew, THE Agent_Invoke_Lambda SHALL update the associated WorkRequest status to PROPOSED.
3. THE Agent_Invoke_Lambda SHALL store the recommendation without approving it, leaving approval to the OFFICE user through 담당자 A's approval API.

### Requirement 9: 재시도 및 Bedrock 폴백

**User Story:** As a developer running a live demo, I want one retry and a prepared fallback, so that a bad agent output or a Bedrock outage does not break the flow.

#### Acceptance Criteria

1. IF the agent output validation fails, THEN THE Agent_Invoke_Lambda SHALL discard the result, record an error log, and retry the Crew_Composition_Agent one time.
2. IF the validation still fails after the one retry, THEN THE Agent_Invoke_Lambda SHALL return the AGENT_RETRY_FAILED error.
3. THE Agent_Invoke_Lambda SHALL provide a configuration flag that enables fallback to a Demo_Fallback_Response.
4. WHERE the fallback flag is enabled, IF a Bedrock call fails or does not respond within the configured timeout, THEN THE Agent_Invoke_Lambda SHALL return the Demo_Fallback_Response.

### Requirement 10: Gap Event Lambda (긴급 재편성)

**User Story:** As an OFFICE user, I want gap events captured and turned into an emergency recomposition, so that I can quickly replace missing workers while keeping the rest of the team running.

#### Acceptance Criteria

1. WHEN a gap event of type NO_SHOW, LEFT_SITE, or UNAVAILABLE is registered, THE Gap_Event_Lambda SHALL store the GapEvent with status=DETECTED and the corresponding type within the same invocation so that it is retrievable through the office query path.
2. WHEN the GapEvent is stored as DETECTED, THE Gap_Event_Lambda SHALL look up the affected Crew.
3. THE Gap_Event_Lambda SHALL compute the exclusion list of departed workers from the active members without modifying any worker state.
4. THE Gap_Event_Lambda SHALL compute the remaining normal team members as fixed_members and preserve their RUNNING state.
5. THE Gap_Event_Lambda SHALL compute the missing trade and headcount.
6. WHEN the fixed_members and the missing trade and headcount are computed, THE Gap_Event_Lambda SHALL build the EMERGENCY payload, invoke the Agent_Invoke_Lambda, and transition the GapEvent status to RECOMPOSING.
7. WHEN the Agent_Invoke_Lambda finishes storing the recommendation, THE Gap_Event_Lambda SHALL transition the GapEvent status to PROPOSED.
8. WHILE the emergency recomposition is not yet approved and completed, THE Gap_Event_Lambda SHALL preserve the RUNNING state of the remaining team members.
9. IF the recomposition fails after the retry is exhausted, THEN THE Gap_Event_Lambda SHALL leave the GapEvent as FAILED and return manual composition guidance.
10. IF the eventId in an agent-recompose request has no matching GapEvent, THEN THE Agent_Invoke_Lambda SHALL return the GAP_EVENT_NOT_FOUND error.
11. IF the affected Crew cannot be retrieved or is invalid during emergency processing, THEN THE Gap_Event_Lambda SHALL return the CREW_INVALID error.

### Requirement 11: 실행 트리거 권한

**User Story:** As a platform owner, I want only OFFICE to trigger the agent while COMPANY can only register gap events, so that composition control stays with the office.

#### Acceptance Criteria

1. WHERE the requester holds the OFFICE role verified through the Shared_Auth_Helper, THE Agent_Invoke_Lambda SHALL allow the agent composition execution.
2. IF a requester that is not in the OFFICE role attempts to trigger the Agent_Invoke_Lambda composition directly, THEN THE Agent_Invoke_Lambda SHALL reject the request with a permission error.
3. THE Gap_Event_Lambda SHALL allow gap event registration by both the COMPANY role and the OFFICE role.
4. IF a COMPANY-role requester attempts to trigger the Crew_Composition_Agent directly, THEN THE Agent_Invoke_Lambda SHALL reject the request.

### Requirement 12: Agent 관측성 로그

**User Story:** As a developer, I want structured agent logs without PII, so that I can debug composition runs safely.

#### Acceptance Criteria

1. WHEN the Crew_Composition_Agent is executed, THE Agent_Invoke_Lambda SHALL write a structured CloudWatch log that includes agent_mode, agent_execution_id, input candidate count, recommendation count, validation success or failure, retry occurrence, and final save result.
2. THE Agent_Invoke_Lambda SHALL exclude full worker personal information from the debug logs.
