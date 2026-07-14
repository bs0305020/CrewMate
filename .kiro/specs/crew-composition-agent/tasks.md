# Implementation Plan: Crew Composition Agent (담당자 B)

## Overview

`requirements.md`(12개 요구사항)와 `design.md`(7개 표준 섹션 + 13개 Correctness Properties)를 점진적·테스트 우선으로 구현하는 계획이다. 구현 언어는 설계에 명시된 **Python**(Pydantic + Strands Agents SDK + Amazon Bedrock, 테스트는 pytest + Hypothesis)이다.

작업 순서는 PRD_B_AGENT 4절 마일스톤을 따른다: ①Agent 로컬 프로토타입 → ②출력 스키마 확정 + 검증 모듈 + 실패 7종 → ③agent_invoke NORMAL → ④재시도·폴백 → ⑤gap_event + EMERGENCY → ⑥관측성·통합. 각 작업은 이전 산출물 위에 쌓이며, 마지막에 관측성·통합으로 배선(wiring)한다.

구현 원칙:

- **순수 로직 우선 + PBT**: `agent/schemas.py`(라운드트립), `validator.py`(7종 검증), `gap_event/gap_logic.py`(결원 계산), `agent_invoke/fallback.py`(폴백 컴포저)는 순수 함수로 구현하고 Correctness Property 1~13을 Hypothesis 속성 기반 테스트(각 최소 100회 반복)로 검증한다.
- **shared 헬퍼는 소비만**: `backend/shared/db`, `backend/shared/auth`, `backend/shared/state`, `backend/shared/response`는 담당자 A 소유이므로 **직접 구현하지 않고** mock/스텁으로 대체해 테스트한다. 통합 지점은 mock 기반으로 배선한다.
- **저장 흐름 분리 (NORMAL vs EMERGENCY)**: NORMAL은 Crew(PROPOSED, source=AGENT) 저장 **후** WorkRequest `COMPOSING→PROPOSED` 전이까지 수행한다. EMERGENCY는 Crew(PROPOSED, source=AGENT) 저장**만** 하고 WorkRequest 상태 머신을 건드리지 않는다(EMERGENCY 재편성 중 기존 WorkRequest는 RUNNING일 수 있음). GapEvent 종료 전이(`RECOMPOSING→PROPOSED`/`FAILED`)는 잠금을 획득한 경로 소유자가 수행한다 — **신뢰된 내부 invoke 경로는 gap_event Lambda, 외부/직접 `agent-recompose` 경로는 agent_invoke(compose_flow)**.
- **검증 직전 최신 스냅샷**: Agent 출력의 `member_ids`를 기준으로 **검증 직전에** 최신 worker 상태 스냅샷(state, current_crew_id, desired_daily_wage, trade)을 조립해 `ValidationContext`를 만든 뒤 순수 검증기에 주입한다. READY 재확인(Property 2)·RUNNING/RESERVED 충돌 검사(Property 6)·total_cost 서버 계산(Property 5)은 stale한 Agent 입력이 아니라 이 최신 스냅샷에 의존한다.
- **B의 GapEvent 책임 한계**: 본 범위는 GapEvent를 **PROPOSED까지만** 전이한다(종료 전이 주체는 경로별 — 신뢰된 내부 invoke 경로는 gap_event, 외부/직접 `agent-recompose` 경로는 agent_invoke). APPROVED/FILLED 전이, 대체 인력 READY→RESERVED→RUNNING 배정, 이탈자 INACTIVE 처리는 담당자 A의 긴급 승인 API 범위이며 본 범위에서 구현하지 않는다.
- **범위 밖 금지**: DynamoDB 테이블/GSI 정의, Cognito, 코어 API, React, 승인·배정·워커 상태전이(READY→RESERVED→RUNNING) 재구현, GapEvent APPROVED/FILLED 전이, ML 모델, 별도 긴급 Agent는 구현하지 않는다.

## Tasks

- [x] 1. 프로젝트 구조와 입출력 스키마 기반 마련
  - [x] 1.1 프로젝트 디렉터리 구조와 테스트 프레임워크 설정
    - `agent/`, `agent/tools/`, `backend/functions/agent_invoke/`, `backend/functions/gap_event/` 패키지 스캐폴딩(`__init__.py`) 생성
    - pytest + Hypothesis 설정(`pyproject.toml` 또는 `requirements-dev.txt`, `pytest.ini`), 공용 `conftest.py` 추가
    - `backend/shared/*`(db/auth/state/response) 소비 지점을 테스트에서 대체할 mock/스텁 모듈(`tests/mocks/shared_stubs.py`) 작성 — 실제 shared 구현은 만들지 않음
    - `agent/schemas.py`를 두 Lambda가 공통 소비하도록 공용 모듈 경로(Layer 대체용 import 경로)를 정리
    - _Requirements: 2.1, 2.2, 2.3, 2.4 (공유 스키마 패키징 기반)_

  - [x] 1.2 Pydantic 입출력 스키마 구현 (`agent/schemas.py`)
    - 입력 스키마: `Priority`, `TradeRequirement`, `RequestSpec`, `Candidate`, `FixedMember`, `CollaborationPair`, `AgentInput`
    - 출력 스키마: `Recommendation`, `AgentOutput`
    - 필드 제약(`skill_level` 1~5, `count`/`budget`/`wage` 양수, `mode` Literal, `certifications` 기본값 등)을 타입으로 강제
    - 혼합 텍스트/누락 필드/잘못된 타입 JSON이 파싱 단계에서 거부되도록 엄격 파싱 설정
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [x] 1.3 출력 스키마 라운드트립 속성 테스트 작성 (`agent/tests/test_property_10_schema_roundtrip.py`)
    - **Property 10: 출력 스키마 라운드트립과 비적합 거부**
    - **Validates: Requirements 2.5, 2.6, 2.7**
    - 유효 `AgentOutput` 직렬화→재파싱 시 동등 객체 산출, 비적합 JSON(누락/타입오류/혼합텍스트)은 파싱 실패 확인
    - Hypothesis 사용, `@settings(max_examples=100)` 이상, 한글·특수문자·긴 문자열을 reason/considerations 제너레이터에 포함
    - 태그 주석: `# Feature: crew-composition-agent, Property 10: 출력 스키마 라운드트립과 비적합 거부`

- [x] 2. Crew Composition Agent 로컬 프로토타입 (Day 1)
  - [x] 2.1 Agent System Prompt 작성 (`agent/system_prompt.md`)
    - Req 4의 11개 제약 전부 포함: 후보 목록 밖 근로자 금지, READY 후보만, NORMAL 조건 충족, EMERGENCY `fixed_members` 유지·보충, 필수 직종·인원 준수, 비용·숙련도·협업·우선순위 종합, 팀 조합 평가, JSON 스키마만 반환, 배정·상태변경 금지, 근로자 부정표현 없는 업무 중심 사유
    - 확률 수치·"최적 보장" 류 표현 금지 문구 포함
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10, 4.11, 3.1, 3.2, 3.3, 3.4, 1.7, 1.9_

  - [x] 2.2 조회 전용 Agent Tool 4종 구현 (`agent/tools/`)
    - `get_request_detail.py`, `get_ready_workers.py`, `get_worker_history.py`, `get_current_crew.py` 각각 구현 — 모두 `shared/db` **읽기 헬퍼만** 호출(mock 스텁 소비)
    - `tools/__init__.py`에서 **정확히 4종만** 등록하는 레지스트리 구성; 쓰기 계열 Tool(`update_worker_state`, `approve_crew`, `assign_worker`, `mark_running`, `delete_worker`, `update_company_request`)은 정의·등록하지 않음
    - `get_worker_history`는 개인정보 전체가 아닌 판단용 필드(협업 횟수·완료 건수 등)만 반환
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [x] 2.3 Crew Composition Agent 정의 구현 (`agent/crew_agent.py`)
    - `build_agent(fallback_enabled)`: `system_prompt.md` 로드 + Tool 4종 등록한 단일 Strands Agent 생성
    - `compose(agent_input, *, timeout_s)`: `AgentInput`(mode 포함) 실행 후 JSON만 파싱해 `AgentOutput` 반환; NORMAL/EMERGENCY는 동일 인스턴스에서 mode로 분기
    - Bedrock 오류/타임아웃은 `BedrockUnavailable` 예외로 표준화; JSON 외 혼합 텍스트는 파싱 실패로 처리
    - _Requirements: 1.1, 1.2, 1.3, 2.7, 5.7_

  - [x] 2.4 Agent 구조·프롬프트 스모크 테스트 (`agent/tests/test_agent_structure.py`)
    - Tool 레지스트리가 정확히 4종이고 쓰기 계열 Tool이 부재함을 단언
    - `system_prompt.md` 존재 및 핵심 제약 문구(READY 전용, JSON only, 부정표현·확률표현 금지) 포함 확인
    - _Requirements: 5.1, 5.6, 4.1_

- [x] 3. Agent 출력 코드 검증기 (Day 2) — 7종 검증, 순수 함수
  - [x] 3.1 검증기 컨텍스트 모델과 7종 검사 구현 (`backend/functions/agent_invoke/validator.py`)
    - 컨텍스트 모델: `WorkerStateSnapshot`, `ValidationContext`, `CheckResult`, `ValidationResult`
    - `validate_output(output, ctx)`가 7종 검사(멤버 출처·신규 READY·중복 금지·직종/인원+추천안 개수·total_cost 일치·타 RUNNING/RESERVED 비충돌·EMERGENCY fixed_members 보존)를 모두 수행하고 실패 사유 목록 반환
    - I/O 없는 순수 함수로 구현(상태 스냅샷은 컨텍스트로 주입, 검증기 내부에서 DB를 호출하지 않음); EMERGENCY 시 재편성 대상 crew는 check 6 예외 처리
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 1.4, 1.5, 1.6, 1.8_

  - [x] 3.2 멤버 출처 강제 속성 테스트 (`.../agent_invoke/tests/test_property_01_member_provenance.py`)
    - **Property 1: 멤버 출처(provenance) 강제**
    - **Validates: Requirements 7.2, 1.6**
    - 유효 출력의 한 `member_id`를 후보/고정 어디에도 없는 미지 id로 변형(mutation) → 반드시 거부 확인
    - Hypothesis, `max_examples=100` 이상; 태그 주석: `# Feature: crew-composition-agent, Property 1: 멤버 출처 강제`

  - [x] 3.3 신규 멤버 READY 상태 속성 테스트 (`.../tests/test_property_02_new_ready.py`)
    - **Property 2: 신규 멤버는 READY 상태**
    - **Validates: Requirements 7.3, 1.8**
    - 신규(비 fixed) 멤버 스냅샷 상태를 비READY로 변형 → 반드시 거부 확인
    - Hypothesis, `max_examples=100` 이상; 태그 주석: `# Feature: crew-composition-agent, Property 2: 신규 멤버는 READY 상태`

  - [x] 3.4 추천안 내 중복 멤버 금지 속성 테스트 (`.../tests/test_property_03_no_duplicate.py`)
    - **Property 3: 추천안 내 중복 멤버 금지**
    - **Validates: Requirements 7.4**
    - `member_ids`에 중복 `worker_id` 주입 → 반드시 거부 확인
    - Hypothesis, `max_examples=100` 이상; 태그 주석: `# Feature: crew-composition-agent, Property 3: 추천안 내 중복 멤버 금지`

  - [x] 3.5 직종·인원 충족 및 추천안 개수 속성 테스트 (`.../tests/test_property_04_trade_headcount.py`)
    - **Property 4: 필수 직종·인원 충족과 추천안 개수**
    - **Validates: Requirements 7.5, 1.4**
    - 유효 판정 조건은 (a) 추천안 1~3개, (b) 직종별 필요 인원 정확 충족; 미달·초과·0개·4개 이상은 거부 확인
    - Hypothesis, `max_examples=100` 이상; 태그 주석: `# Feature: crew-composition-agent, Property 4: 필수 직종·인원 충족과 추천안 개수`

  - [x] 3.6 total_cost 서버 계산 일치 속성 테스트 (`.../tests/test_property_05_total_cost.py`)
    - **Property 5: total_cost는 서버 계산 임금 합과 일치**
    - **Validates: Requirements 7.6**
    - `total_cost`가 `member_ids`의 `desired_daily_wage` 합과 일치할 때만 통과, 불일치는 거부 확인
    - Hypothesis, `max_examples=100` 이상; 태그 주석: `# Feature: crew-composition-agent, Property 5: total_cost는 서버 계산 임금 합과 일치`

  - [x] 3.7 타 RUNNING/RESERVED 비충돌 속성 테스트 (`.../tests/test_property_06_no_conflict.py`)
    - **Property 6: 타 RUNNING/RESERVED 배정과 비충돌**
    - **Validates: Requirements 7.7**
    - 신규 멤버 중 (현재 재편성 대상 Crew 제외) 다른 RUNNING/RESERVED 배정 포함자가 있으면 거부; EMERGENCY `fixed_members`는 예외 확인
    - Hypothesis, `max_examples=100` 이상; 태그 주석: `# Feature: crew-composition-agent, Property 6: 타 RUNNING/RESERVED 배정과 비충돌`

  - [x] 3.8 EMERGENCY fixed_members 보존 속성 테스트 (`.../tests/test_property_07_fixed_preserved.py`)
    - **Property 7: EMERGENCY에서 fixed_members 보존**
    - **Validates: Requirements 7.8, 1.5, 1.3**
    - mode=EMERGENCY에서 모든 추천안이 모든 `fixed_members`를 그대로 포함할 때만 유효; 누락·치환 시 거부 확인
    - Hypothesis, `max_examples=100` 이상; 태그 주석: `# Feature: crew-composition-agent, Property 7: EMERGENCY에서 fixed_members 보존`

  - [x] 3.9 검증기 건전성 속성 테스트 (`.../tests/test_property_08_soundness.py`)
    - **Property 8: 검증기 건전성(soundness) — 완전 준수 출력은 수용**
    - **Validates: Requirements 7.1**
    - 7종 규칙을 모두 만족하도록 구성한 출력은 변형 없이 반드시 유효로 판정(퇴화 검증기 배제)
    - Hypothesis, `max_examples=100` 이상; 태그 주석: `# Feature: crew-composition-agent, Property 8: 검증기 건전성`

  - [x] 3.10 검증기 단위 테스트 — 잘못된 출력 7종 케이스 (`.../tests/test_validator_units.py`)
    - 미지 id, 비READY, 중복, 직종·인원 미충족, 비용 불일치, 타배정 충돌, fixed_members 훼손 각각이 해당 검사에서 검출됨을 대표 사례로 확인
    - PRD_B Day 2 완료 기준("잘못된 출력 7종이 전부 검출됨")에 직접 대응하는 필수 테스트 — 생략 금지
    - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8_

- [x] 4. 체크포인트 — Agent 프로토타입·검증기 (Day 1~2 마일스톤)
  - 모든 테스트가 통과하는지 확인하고, 의문이 생기면 사용자에게 질문한다.

- [x] 5. Agent Invoke Lambda — NORMAL 편성 + EMERGENCY 진입 (Day 3)
  - [x] 5.1 후보 조립기 및 검증 컨텍스트 스냅샷 조립기 구현 (`backend/functions/agent_invoke/assembler.py`)
    - `assemble_normal_input(request_id, office_id)`: `shared/db.get_work_request` + `query_ready_workers(office_id, trades)` + `get_worker_collaborations`(mock 소비)로 `AgentInput`(mode=NORMAL) 조립; 후보는 office_id 일치 + state=READY만 포함, 테이블 직접 접근 없이 shared 헬퍼만 호출
    - `build_validation_context(output_member_ids, *, mode, candidates, fixed_members, required_workers, current_crew_id=None)`: Agent 출력의 `member_ids`를 기준으로 `shared/db.get_workers(member_ids)`(mock 소비)를 호출해 **검증 직전 최신** worker state·current_crew_id·desired_daily_wage·trade 스냅샷을 확보하고 `ValidationContext`(`validator.py` 정의)를 조립해 반환
    - 이 스냅샷 조립은 **handler/assembler 쪽 I/O**로 수행하며 `validator.py`는 순수 함수로 유지한다(검증기 내부에서 DB를 호출하지 않음)
    - READY 재확인(Property 2)·RUNNING/RESERVED 충돌 검사(Property 6)·total_cost 서버 계산(Property 5)이 stale한 Agent 입력이 아니라 **검증 직전 최신 스냅샷**에 의존하도록 컨텍스트를 구성
    - _Requirements: 6.3, 6.4, 6.5, 7.3, 7.6, 7.7, 2.1, 2.2, 2.3, 2.4_

  - [x] 5.2 저장 모듈 구현 — NORMAL/EMERGENCY 저장 흐름 분리 (`backend/functions/agent_invoke/persistence.py`)
    - `save_normal_proposal(recommendation, ctx)`: `shared/db.save_crew`로 Crew(status=PROPOSED, source=AGENT) 저장 **후** `transition_request_status(COMPOSING→PROPOSED)` 호출, crew_id 반환 (Req 8.1, 8.2)
    - `save_emergency_proposal(recommendation, ctx)`: `shared/db.save_crew`로 Crew(status=PROPOSED, source=AGENT) 저장**만** 수행하고 WorkRequest 상태·GapEvent 상태를 **모두 전이하지 않음**, crew_id 반환 (Req 8.1). GapEvent 종료 전이(`RECOMPOSING→PROPOSED`/`FAILED`)는 저장 함수가 아니라 **경로 소유자의 오케스트레이션**이 수행한다 — **신뢰된 내부 invoke 경로는 gap_event Lambda(Req 10.7), 외부/직접 `agent-recompose` 경로는 agent_invoke의 compose_flow(5.3 상호 참조)**
    - 구현 실수 방지를 위해 두 함수로 분리하거나 `SaveContext(mode=...)`로 분기하되, **NORMAL만 WorkRequest를 전이**하도록 코드 경로를 명확히 구분
    - EMERGENCY 재편성 중 기존 WorkRequest는 이미 RUNNING 상태일 수 있으므로 **EMERGENCY 경로는 WorkRequest 상태 머신을 되돌리거나 변경하지 않는다**
    - 승인·배정·워커 state 변경은 수행하지 않음(위임)
    - _Requirements: 8.1, 8.2, 8.3, 10.7_

  - [x] 5.3 Lambda 핸들러 구현 — 라우팅·권한(외부/내부 경로 구분)·상태가드(경로별 분기)·compose_flow 1차 (`backend/functions/agent_invoke/handler.py`)
    - 라우팅: `POST /office/requests/{requestId}/agent-compose`→NORMAL(외부/직접 호출), `POST /office/gap-events/{eventId}/agent-recompose`→EMERGENCY 직접 트리거(외부/직접 호출), 그리고 gap_event Lambda의 **신뢰된 내부 invoke**(EMERGENCY payload 소비)
    - **두 진입 경로 구분**: 핸들러는 **이벤트 형태**로 경로를 판별한다 — API Gateway proxy 이벤트(외부/직접 호출) vs 직접 invoke payload(gap_event의 trusted internal invoke)
    - **외부/직접 호출 권한**: API Gateway 경유 호출(agent-compose NORMAL, agent-recompose EMERGENCY 직접 트리거)에는 `shared/auth.require_role(OFFICE)`를 적용한다. OFFICE 아닌 주체(특히 COMPANY)의 **직접** Agent 실행은 FORBIDDEN (Req 11.1, 11.2, 11.4)
    - **신뢰된 내부 invoke 권한**: gap_event Lambda가 등록자 인증(COMPANY·OFFICE) 후 수행하는 동기 Lambda invoke에는 OFFICE 전용 외부 게이트를 **재적용하지 않는다**. gap_event가 이미 gap 등록자를 인증했고(Req 11.3) 긴급 재편성은 그 인증된 흐름의 연속이므로, **COMPANY가 등록한 gap에서 이어진 내부 호출도 FORBIDDEN 없이 진행**된다
    - **내부 경로 신뢰는 IAM으로 강제**: 스푸핑 가능한 payload 플래그가 아니라 IAM으로 경계를 강제한다 — 오직 gap_event의 Lambda 실행 역할만 agent_invoke를 직접 invoke할 수 있게 권한을 제한하고, API Gateway 진입 경로에는 OFFICE 역할 검사를 적용한다
    - 상태가드(NORMAL): `transition_request_status(REQUESTED→COMPOSING)` 조건부 쓰기 실패 시 STATE_CONFLICT(처리 중 동일 요청의 중복 호출은 조건 실패로 자연 거부, 큐잉하지 않음)
    - **상태가드(EMERGENCY) — 진입 경로별 분기**(내부 invoke가 자기 잠금에 막히는 모순 방지, 8.5와 상호 참조):
      - **외부/직접 `agent-recompose` 호출**: agent_invoke가 **스스로 잠금을 획득**한다. `eventId`로 GapEvent 조회 실패 시 `GAP_EVENT_NOT_FOUND` (Req 10.10). 예상 상태 = `DETECTED`이며 `transition_gap_event_status(DETECTED→RECOMPOSING)` 조건부 전이를 **직접 수행**한다. 조건부 전이 실패(이미 `RECOMPOSING`/`PROPOSED`/`FAILED`이거나 동시 중복 요청) 시 큐잉하지 않고 `STATE_CONFLICT` 반환 (Req 6.6, 6.7)
      - **신뢰된 내부 invoke**(gap_event가 호출): gap_event가 **이미** `DETECTED→RECOMPOSING` 잠금을 획득한 뒤 호출하므로 GapEvent는 이미 `RECOMPOSING` 상태다. 내부 경로는 **`RECOMPOSING`을 정상(예상) 상태로 수용**하고 잠금을 재획득하지 않으며 `STATE_CONFLICT`로 막지 않는다. GapEvent의 후속(종료) 전이(`RECOMPOSING→PROPOSED`/`FAILED`)는 **gap_event가 소유**하고, 내부 agent_invoke 경로는 GapEvent 상태를 전이하지 않는다
    - **EMERGENCY 종료 전이 소유(경로별) — 잠금 획득 주체가 종료 전이도 소유**:
      - **외부/직접 `agent-recompose` 경로**: 잠금(`DETECTED→RECOMPOSING`)을 직접 획득한 agent_invoke가 종료 전이까지 소유한다. compose·검증 후 **저장 성공 시 agent_invoke(compose_flow)가 `RECOMPOSING→PROPOSED`를 전이**하고, **재시도 소진 실패 시 agent_invoke가 `RECOMPOSING→FAILED`를 전이 + 수동 편성 안내**를 수행한다(재시도·실패 전이 배선은 6.3). 이 경로에는 gap_event가 관여하지 않는다
      - **신뢰된 내부 invoke 경로**: 종료 전이(`RECOMPOSING→PROPOSED`/`FAILED`)는 **gap_event가 소유**하며(8.5), agent_invoke 내부 경로는 GapEvent를 전이하지 않는다(현행 유지)
      - **저장 함수와 분리**: `save_emergency_proposal`은 두 경로 모두 Crew(PROPOSED, source=AGENT) 저장만 하고 GapEvent를 전이하지 않는다. 외부 경로의 GapEvent 종료 전이는 저장 함수가 아니라 **compose_flow 오케스트레이션**에서 수행한다
    - **EMERGENCY payload 조립 책임(경로별)**:
      - **외부/직접 `agent-recompose` 호출**: agent_invoke는 클라이언트 요청 body의 payload를 **신뢰하지 않고**, `eventId`로 GapEvent 조회 → 영향 Crew 조회 → READY 후보 조립을 거쳐 **서버 측에서 EMERGENCY payload를 조립**한다. 이때 **8.1의 `compute_fixed_members`/`compute_missing`와 8.4의 `build_emergency_payload` 순수 로직을 재사용**한다(gap_event와 동일 로직 공유, 분기 없음)
      - **신뢰된 내부 invoke**: gap_event가 이미 조립해 전달한 EMERGENCY payload를 그대로 소비한다
    - `compose_flow` 1차(단일 실행): `compose()` → `build_validation_context()`로 **검증 직전 최신 스냅샷** 조립 → `validate_output()` → 통과 시 mode에 따라 `save_normal_proposal()`/`save_emergency_proposal()` 호출, 실패 시 저장 없이 반환; **외부/직접 EMERGENCY 경로는 저장 성공 시 compose_flow가 `RECOMPOSING→PROPOSED` 종료 전이를 수행**(신뢰된 내부 invoke 경로는 전이하지 않음 — gap_event 소유); 응답은 `shared/response` 포맷
    - _Requirements: 6.1, 6.2, 6.3, 6.5, 6.6, 6.7, 10.6, 10.7, 10.10, 11.1, 11.2, 11.3, 11.4_

  - [x] 5.4 무효 출력 미저장 속성 테스트 (`.../tests/test_property_09_no_save_invalid.py`)
    - **Property 9: 무효 출력은 절대 저장되지 않음**
    - **Validates: Requirements 7.9, 8.1**
    - 검증 실패하는 임의 출력에 대해 (1) `save_crew`가 **호출되지 않고**, (2) **PROPOSED 전이가 발생하지 않음**(WorkRequest `COMPOSING→PROPOSED` 및 GapEvent `RECOMPOSING→PROPOSED` 미호출)을 확인한다. NORMAL·EMERGENCY 양쪽 저장 경로 모두 진입하지 않음을 확인
    - **단언 범위(design.md Property 9와 정합)**: 이 테스트는 **"저장 없음 + PROPOSED 전이 없음"** 만 단언한다. design.md Property 9 문구("어떤 Crew도 저장하지 않으며 어떤 WorkRequest 상태도 PROPOSED로 전이하지 않는다")와 일치시킨다. **"상태전이 호출 0회"라는 넓은 표현은 사용하지 않는다** — NORMAL 재시도 소진 롤백(`COMPOSING→REQUESTED`)은 정당한 전이로서 이 테스트의 금지 대상이 아니며, 롤백 검증은 **6.4에서 별도로** 다룬다(5.4=저장/PROPOSED 전이 부재, 6.4=롤백 검증으로 책임 분리)
    - 무효 출력이 절대 저장되지 않는 안전성 불변식을 지키는 **필수 PBT(별표 없음)** — 생략 금지. 별표가 없어도 필수 PBT이며, 전 강도(최소 100회 반복)를 유지한다
    - Hypothesis로 무효 출력 생성, `@settings(max_examples=100)`(최소 100회, 축소하지 않음); 태그 주석: `# Feature: crew-composition-agent, Property 9: 무효 출력은 절대 저장되지 않음`

  - [x] 5.5 실행 흐름 단위 테스트 (`.../tests/test_agent_invoke_flow.py`)
    - NORMAL/EMERGENCY 라우팅·mode 설정
    - **권한 경로 구분 검증(외부 vs 내부)**: (a) **외부/직접 호출**(API Gateway proxy 이벤트)에서 비OFFICE 주체(COMPANY·WORKER)의 직접 Agent 실행은 `FORBIDDEN`; (b) **COMPANY가 등록한 gap에서 이어진 gap_event의 trusted internal invoke(직접 invoke payload)는 FORBIDDEN 없이 정상 진행**됨을 mock으로 확인 — 외부 경로와 내부 경로를 명확히 구분해 검증한다
    - 상태가드(경로별 분기 검증, 5.3과 정합): NORMAL 조건부 쓰기 실패 시 `STATE_CONFLICT`; EMERGENCY `eventId` 미매칭 시 `GAP_EVENT_NOT_FOUND`
      - **(a) 외부/직접 `agent-recompose` 호출**: GapEvent가 `DETECTED`가 **아닐** 때(예: 이미 `RECOMPOSING`/`PROPOSED`/`FAILED`) `DETECTED→RECOMPOSING` 조건부 전이 실패로 `STATE_CONFLICT`; 같은 GapEvent에 대한 중복 recompose도 조건부 전이 실패로 `STATE_CONFLICT`(큐잉 없음)임을 mock으로 확인
      - **(b) 신뢰된 내부 invoke**: GapEvent가 **`RECOMPOSING`**(gap_event가 이미 잠금을 획득한 상태)일 때 내부 경로가 이를 정상(예상) 상태로 수용해 **`STATE_CONFLICT` 없이 정상 진행**하고, 내부 경로가 GapEvent 상태를 (재)전이하지 않음을 mock으로 확인
    - 저장 분리 확인: NORMAL은 `save_normal_proposal`(WorkRequest `COMPOSING→PROPOSED` 전이 포함) 경로, EMERGENCY는 `save_emergency_proposal`(WorkRequest 미전이) 경로로 진입함을 mock으로 확인
    - **EMERGENCY 종료 전이 소유 검증(경로별, 5.3·6.3과 정합)**:
      - **외부/직접 `agent-recompose` 경로**: 저장 성공 시 **agent_invoke(compose_flow)가 GapEvent `RECOMPOSING→PROPOSED`를 전이**함을, 재시도 소진 실패 시 **agent_invoke가 `RECOMPOSING→FAILED`를 전이(+ 수동 편성 안내)**함을 mock으로 확인
      - **신뢰된 내부 invoke 경로 대비**: 내부 경로에서는 agent_invoke가 GapEvent를 (재)전이하지 않음(종료 전이는 gap_event 소유)을 확인
    - 스냅샷 확인: **검증 직전 최신 스냅샷(`build_validation_context`)으로 `validate_output`을 호출**함을 확인(예: `get_workers`가 검증 직전에 호출되고 그 결과가 컨텍스트로 주입됨)
    - 라우팅·권한(외부/내부 경로 구분)·상태가드·저장 분리·종료 전이 소유(경로별)를 검증하는 필수 테스트 — 생략 금지
    - _Requirements: 6.1, 6.2, 6.6, 6.7, 10.7, 10.9, 10.10, 11.1, 11.2, 11.3, 11.4_

- [x] 6. 재시도 및 Bedrock 폴백 (Day 4)
  - [x] 6.1 데모 폴백 컴포저 구현 (`backend/functions/agent_invoke/fallback.py`)
    - `demo_fallback(agent_input)`: LLM 없이 결정적 로컬 컴포저로 필요 직종별 후보를 저비용 우선·예산 내 채움(EMERGENCY는 `fixed_members` 포함), `AgentOutput` 반환
    - 시드(seed=42) 기반 결정적 결과로 데모 안정성 확보
    - _Requirements: 9.3, 9.4_

  - [x] 6.2 폴백 산출물 유효성 속성 테스트 (`.../tests/test_property_13_fallback_valid.py`)
    - **Property 13: 폴백 산출물의 유효성(model-based)**
    - **Validates: Requirements 9.4, 9.3**
    - 충분한 후보를 포함한 임의 `AgentInput`에 대해 `demo_fallback` 산출물이 동일한 `validate_output`(Property 1~7)을 항상 통과함을 확인
    - Hypothesis, `max_examples=100` 이상; 태그 주석: `# Feature: crew-composition-agent, Property 13: 폴백 산출물의 유효성`

  - [x] 6.3 compose_flow에 재시도·폴백·롤백 배선 (`backend/functions/agent_invoke/handler.py` 확장)
    - Bedrock 실패/타임아웃 + 폴백 플래그 ON → `demo_fallback` 결과로 대체 후 동일 검증 경로(검증 직전 최신 스냅샷 포함) 진입
    - 검증 실패 시 결과 폐기 + 오류 로그 + **Agent 정확히 1회 재시도**; 재실패 시 `AGENT_RETRY_FAILED` 반환
    - **롤백은 NORMAL에만 적용**: NORMAL이면 `transition_request_status(COMPOSING→REQUESTED)` 롤백으로 수동 편성 가능 상태로 되돌린다. **EMERGENCY는 WorkRequest 상태를 되돌리거나 변경하지 않는다**(RUNNING일 수 있음)
    - **EMERGENCY 재편성 실패 전이(`RECOMPOSING→FAILED` + 수동 편성 안내)의 주체는 경로별로 다름**(기존 "gap_event가 수행"에서 정정): **외부/직접 `agent-recompose` 경로는 잠금을 획득한 agent_invoke 자신**이 재시도 소진 실패 시 `RECOMPOSING→FAILED` 전이 + 수동 편성 안내를 수행하고, **신뢰된 내부 invoke 경로는 gap_event Lambda가 수행**한다(8.5, 내부 경로에서 agent_invoke는 GapEvent를 전이하지 않음). 이 EMERGENCY 실패 전이는 NORMAL 롤백(`COMPOSING→REQUESTED`)과 별개다
    - 폴백 OFF에서 Bedrock 실패는 `AGENT_RETRY_FAILED`로 매핑
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 10.9_

  - [x] 6.4 재시도·폴백 단위 테스트 (`.../tests/test_agent_invoke_retry_fallback.py`)
    - 재시도 정확히 1회, 폴백 on/off 분기 확인
    - **NORMAL 롤백 검증(정당한 전이)**: NORMAL 재시도 소진 시 `transition_request_status(COMPOSING→REQUESTED)` 롤백이 **정확히 1회 발생**하고 `AGENT_RETRY_FAILED`를 반환함을 mock으로 확인한다. 이 롤백은 5.4(무효 출력 미저장)의 금지 대상이 **아니라** 여기 6.4에서 검증하는 **정당한 상태 전이**다(5.4=저장/PROPOSED 전이 부재, 6.4=롤백 검증으로 책임 분리)
    - **EMERGENCY는 WorkRequest 미전이**(롤백 없음) 확인 — 재편성 실패 처리(GapEvent `RECOMPOSING→FAILED` + 수동 편성 안내)는 gap_event(8.5) 소관
    - **Bedrock 강제 실패 + 폴백 ON 시 데모 경로가 유효 추천을 검증 통과·저장**함을 mock으로 확인 (PRD_B Day 4 완료 기준 "Bedrock 강제 실패 시에도 데모 경로 유지"에 직접 대응하는 필수 테스트 — 생략 금지)
    - _Requirements: 9.1, 9.2, 9.4_

- [x] 7. 체크포인트 — NORMAL 완성·재시도·폴백 (Day 3~4 마일스톤)
  - 모든 테스트가 통과하는지 확인하고, 의문이 생기면 사용자에게 질문한다.

- [x] 8. Gap Event Lambda + EMERGENCY 재편성 (Day 5)
  - [x] 8.1 결원 계산 순수 로직 구현 (`backend/functions/gap_event/gap_logic.py`)
    - `compute_fixed_members(active_members, departed_ids)`: 활성 멤버에서 이탈자 제외한 잔여 정상 팀원 반환(입력 객체·state 비변경)
    - `compute_missing(required_workers, fixed_members)`: 직종별 결원 = `max(0, 요구 인원 − 잔여 보유 인원)`
    - _Requirements: 10.3, 10.4, 10.5_

  - [x] 8.2 결원 계산 — fixed_members 속성 테스트 (`backend/functions/gap_event/tests/test_property_11_fixed_members.py`)
    - **Property 11: 결원 계산 — fixed_members = 활성 − 이탈, 비변경**
    - **Validates: Requirements 10.3, 10.4**
    - 활성 − 이탈 집합 정확 반환, 이탈자 미포함, 입력 멤버 상태 비변경 확인
    - Hypothesis, `max_examples=100` 이상; 태그 주석: `# Feature: crew-composition-agent, Property 11: 결원 계산 fixed_members`

  - [x] 8.3 결원 계산 — 부족분·커버 속성 테스트 (`.../gap_event/tests/test_property_12_missing_coverage.py`)
    - **Property 12: 결원 계산 — 직종별 부족분과 커버 보장**
    - **Validates: Requirements 10.5**
    - 직종별 결원 = `max(0, 요구−잔여)`이고, 잔여 + 결원 = 모든 직종 요구 인원 정확 충족 확인
    - Hypothesis, `max_examples=100` 이상; 태그 주석: `# Feature: crew-composition-agent, Property 12: 결원 계산 부족분·커버`

  - [x] 8.4 EMERGENCY payload 조립 구현 (`backend/functions/gap_event/emergency_payload.py`)
    - `build_emergency_payload(request, fixed_members, candidates, collaboration_pairs)`: mode=EMERGENCY `AgentInput` 조립(순수 함수)
    - _Requirements: 10.6, 1.3, 2.1_

  - [x] 8.5 Gap Event Lambda 핸들러 구현 (`backend/functions/gap_event/handler.py`)
    - 인증(COMPANY·OFFICE 모두 허용) → `save_gap_event(status=DETECTED, type)` → `get_crew`(실패 시 `CREW_INVALID`)
    - **OFFICE 폴링 조회 가능성**: `save_gap_event`는 `office_id` 및 상태 키 등 **office query path(GSI office 조회 경로)** 로 조회되는 데 필요한 값을 포함해 저장하여, 결원 발생 후 폴링 주기(약 5초) 내 OFFICE 화면에서 긴급 재편성 상태를 조회할 수 있게 한다 (Req 10.1, PRD F-B5). 단, DynamoDB 테이블/GSI 자체 구현은 담당자 A 범위이므로 `shared/db` 헬퍼를 **소비만** 한다
    - `compute_fixed_members`/`compute_missing` 호출 → **agent_invoke 내부 invoke 이전에** `transition_gap_event_status(DETECTED→RECOMPOSING)` 조건부 전이로 **잠금을 먼저 획득**(실패 시 `STATE_CONFLICT`) → EMERGENCY payload로 agent_invoke를 **신뢰된 내부 invoke**(동기 Lambda invoke)로 호출
    - **내부 invoke가 이미 잠긴 `RECOMPOSING`을 수용해야 함**: gap_event가 잠금을 선점하므로, agent_invoke의 EMERGENCY 내부 경로는 이미 `RECOMPOSING`인 GapEvent를 정상(예상) 상태로 수용하며 잠금을 재획득하거나 `STATE_CONFLICT`로 막지 않는다(5.3의 EMERGENCY 상태가드 내부 경로 분기와 상호 참조). 이로써 내부 invoke가 자기 자신의 잠금에 막히는 모순을 방지한다
    - **신뢰된 내부 invoke 규약**: gap_event는 등록자(COMPANY·OFFICE)를 인증한 뒤 agent_invoke를 호출하며, **등록자가 OFFICE일 것을 요구하지 않는다**(agent_invoke의 OFFICE 전용 외부 게이트를 이 내부 경로에 재적용하지 않으므로 COMPANY가 등록한 gap도 정상적으로 재편성으로 이어진다). 이 내부 경로의 신뢰는 스푸핑 가능한 payload 플래그가 아니라 **IAM**(오직 gap_event의 Lambda 실행 역할만 agent_invoke 직접 invoke 가능)으로 강제하며, 5.3의 외부/내부 경로 구분과 정합한다 (Req 11.3)
    - **후속(종료) 전이 소유권 — 내부 invoke 경로에 한정**: GapEvent의 종료 전이(`RECOMPOSING→PROPOSED`/`RECOMPOSING→FAILED`) 소유는 **gap_event가 주도하는 신뢰된 내부 invoke 경로에 한정**된다. 이 내부 경로에서 gap_event가 종료 전이를 소유하며 agent_invoke 내부 경로는 GapEvent 상태를 전이하지 않는다 — 저장 성공 시 gap_event가 `RECOMPOSING→PROPOSED`; 재시도 소진 실패 시 gap_event가 `RECOMPOSING→FAILED` + 수동 편성 안내; 잔여 팀원 RUNNING 상태 비변경. **외부/직접 `agent-recompose` 경로의 종료 전이는 gap_event가 아니라 잠금을 획득한 agent_invoke가 소유**하며(5.3 상호 참조), 이 외부 경로에는 gap_event가 관여하지 않는다
    - **책임 한계**: 본 범위는 GapEvent를 **PROPOSED까지만** 전이한다. GapEvent `APPROVED/FILLED` 전이, 대체 인력 `READY→RESERVED→RUNNING` 배정, 이탈자 `INACTIVE` 처리는 담당자 A의 긴급 승인 API(`/office/emergency/{eventId}/approve`)가 수행하며 본 범위에서 구현하지 않는다
    - _Requirements: 10.1, 10.2, 10.6, 10.7, 10.8, 10.9, 10.11, 11.3_

  - [x] 8.6 Gap Event 단위 테스트 (`.../gap_event/tests/test_gap_event_handler.py`)
    - GapEvent 상태 전이는 **`DETECTED→RECOMPOSING→PROPOSED`(또는 실패 시 `RECOMPOSING→FAILED`)까지만** 확인하고, **PROPOSED 이후(`APPROVED/FILLED`)는 검증 대상에서 제외**(담당자 A 범위)
    - `CREW_INVALID`, gap 등록 권한(COMPANY·OFFICE 허용), 재편성 실패 시 `FAILED` + 수동 편성 안내를 mock으로 확인
    - **office query path 저장 형태 확인**: DETECTED GapEvent가 office query path로 조회 가능한 형태(`office_id`·상태 키 포함)로 `save_gap_event` 되었음을 mock 인자로 검증
    - 긴급 시나리오(결원 → fixed_members 유지 → 추천 저장 → PROPOSED) 통과를 뒷받침하는 필수 테스트 — 생략 금지
    - _Requirements: 10.1, 10.7, 10.8, 10.9, 10.11, 11.3_

- [x] 9. 관측성 로그 및 통합 배선 (Day 6)
  - [x] 9.1 구조화 관측성 로그 구현 (`backend/functions/agent_invoke/observability.py`)
    - `AgentLogRecord`(agent_execution_id, agent_mode, request_id, input_candidate_count, recommendation_count, validation_passed, validation_failed_checks, retried, fallback_used, saved, crew_id) 정의 및 CloudWatch 구조화 기록 함수
    - 이름·전화번호 등 개인정보 전체 제외, worker_id·집계 수치만 기록
    - _Requirements: 12.1, 12.2_

  - [x] 9.2 관측성 로그를 실행 경로에 배선 (`agent_invoke/handler.py`, `gap_event/handler.py` 확장)
    - `compose_flow`(NORMAL/EMERGENCY)와 gap_event 처리 경로에서 실행마다 `AgentLogRecord`를 채워 기록(검증 성공/실패·재시도·폴백·최종 저장 반영)
    - _Requirements: 12.1, 12.2_

  - [x] 9.3 관측성 로그 단위 테스트 (`.../agent_invoke/tests/test_observability.py`)
    - 로그 레코드가 필수 필드를 모두 포함하고 PII(name/phone 등)를 포함하지 않음을 확인
    - _Requirements: 12.1, 12.2_

  - [x] 9.4 통합(모킹) 테스트 (`tests/integration/test_end_to_end_mocked.py`)
    - NORMAL: 요청 → 후보 조립 → compose → 검증 직전 최신 스냅샷 → 검증 → Crew(PROPOSED) 저장 + WorkRequest `COMPOSING→PROPOSED` 전이까지 mock 기반 end-to-end
    - **EMERGENCY(신뢰된 내부 invoke 경로)**: 결원 등록 → gap_event가 fixed_members/결원 계산·`DETECTED→RECOMPOSING` 잠금 → agent_invoke 동기 호출 → Crew(PROPOSED) 저장(**WorkRequest 미전이**) → **gap_event가 GapEvent `RECOMPOSING→PROPOSED` 종료 전이**까지 mock 기반 검증(PROPOSED 이후 APPROVED/FILLED는 A 범위이므로 제외)
    - **EMERGENCY(외부/직접 `agent-recompose` 경로)**: 외부 OFFICE `agent-recompose` 호출 → `DETECTED→RECOMPOSING`(agent_invoke가 직접 획득) → **서버측 EMERGENCY payload 조립(8.1의 `compute_fixed_members`/`compute_missing`·8.4의 `build_emergency_payload` 재사용)** → compose·검증 → Crew(PROPOSED) 저장(**WorkRequest 미전이**) → **agent_invoke(compose_flow)가 GapEvent `RECOMPOSING→PROPOSED` 종료 전이**까지 mock 기반 end-to-end 검증(PROPOSED 이후 APPROVED/FILLED는 A 범위이므로 제외)
    - shared/db·shared/auth·Bedrock·Lambda invoke는 mock/스텁으로 대체
    - 데모 시나리오 2·3(요청→AI 편성→저장, C 노쇼→A+B+E 추천→저장)의 코드 경로를 자동 검증하는 필수 테스트 — 생략 금지
    - _Requirements: 6.2, 6.5, 8.1, 8.2, 10.6, 10.7_

- [x] 10. 최종 체크포인트 — gap_event·관측성·통합 (Day 5~6 마일스톤)
  - 모든 테스트가 통과하는지 확인하고, 의문이 생기면 사용자에게 질문한다.

## Notes

- **별표(`*`)의 의미**: `*`는 "시간 제약 시 **제너레이터 범위(generator breadth)** 를 좁힐 수 있는 보조 테스트"를 뜻하며, **완전 생략을 의미하지 않는다**. 별표 없음 = 필수 테스트(전 강도 유지). 시간이 부족해도 대응하는 대표 단위 테스트는 어떤 경우에도 생략하지 않는다.
- **별표 유무는 PBT 여부와 무관하다**: 별표가 있다고 PBT인 것도, 없다고 PBT가 아닌 것도 아니다. 안전 불변식을 지키는 **필수 PBT는 별표 없이 필수로 둔다** — 예: **5.4(Property 9, 무효 출력 미저장)는 별표 없는 필수 PBT**다.
- **반복 횟수는 축소 대상이 아니다**: 모든 PBT는 별표 유무와 무관하게 `@settings(max_examples=100)`로 **최소 100회** 반복을 유지한다. 시간 제약 시 조정 가능한 것은 **제너레이터 범위(generator breadth)뿐**이며, 반복 횟수(100회)는 줄이지 않는다.
- 필수(별표 없음) 테스트: 3.10(검증기 7종 단위), 5.4(Property 9 무효 출력 미저장, 필수 PBT), 5.5(실행 흐름 단위), 6.4(재시도·폴백 단위), 8.6(gap_event 단위), 9.4(통합). 이들은 PRD_B_AGENT·TEAM_PLAN의 Day 2/Day 4/Day 5 완료 기준(잘못된 출력 7종 검출, Bedrock 강제 실패 시 데모 경로 유지, 긴급 시나리오 통과)과 직접 연결되므로 건너뛰지 않는다.
- 검증기 속성 테스트(3.2~3.9)는 검증기 안전성의 핵심이다. 시간 제약 시 **제너레이터 범위**는 좁힐 수 있으나 반복 횟수(최소 100회)는 유지하며, 대응하는 대표 단위 테스트(3.10)는 반드시 유지한다.
- 각 작업은 검증하는 요구사항 번호를 `_Requirements: X.Y_`로 참조하며, PBT 작업은 검증하는 Property 번호와 `Validates` 절을 명시한다.
- 속성 기반 테스트는 Hypothesis로 작성하며 각 **최소 100회** 반복(`@settings(max_examples=100)`, 반복 횟수는 축소하지 않음)하고, `# Feature: crew-composition-agent, Property N: ...` 형식의 태그 주석을 단다.
- **실행 트리거 권한 경로 구분(외부 vs 내부)**: agent_invoke의 OFFICE 전용 게이트는 **API Gateway 경유 외부/직접 호출**(agent-compose NORMAL, agent-recompose EMERGENCY 직접 트리거)에만 적용된다. gap_event가 등록자(COMPANY·OFFICE) 인증 후 수행하는 **신뢰된 내부 invoke**에는 OFFICE 게이트를 재적용하지 않으며(COMPANY가 등록한 gap도 정상 재편성으로 이어짐), 이 내부 경로의 신뢰는 스푸핑 가능한 payload 플래그가 아니라 **IAM**(gap_event 실행 역할만 agent_invoke 직접 invoke 가능)으로 강제한다. 핸들러는 이벤트 형태(API Gateway proxy 이벤트 vs 직접 invoke payload)로 두 경로를 구분한다.
- **EMERGENCY 상태가드 경로별 분기**: agent_invoke의 EMERGENCY 상태가드는 **진입 경로별로 분기**한다. **외부/직접 `agent-recompose` 호출**은 예상 상태 `DETECTED`에서 `DETECTED→RECOMPOSING`을 **직접 획득**하고, 조건부 전이 실패(이미 `RECOMPOSING`/`PROPOSED`/`FAILED`·동시 중복)면 `STATE_CONFLICT`(큐잉 없음)·`eventId` 미매칭이면 `GAP_EVENT_NOT_FOUND`를 반환한다. **신뢰된 내부 invoke**는 gap_event가 이미 `DETECTED→RECOMPOSING` 잠금을 획득한 뒤 호출하므로 이미 `RECOMPOSING`인 GapEvent를 **정상 상태로 수용**하며 재획득·`STATE_CONFLICT` 없이 진행한다. 이로써 gap_event의 내부 invoke가 자기 잠금에 막히는 모순을 방지한다(5.3·8.5 상호 참조). **GapEvent 종료 전이(`RECOMPOSING→PROPOSED`/`RECOMPOSING→FAILED`) 소유는 잠금(`DETECTED→RECOMPOSING`)을 획득한 주체를 따른다 — 외부/직접 경로는 agent_invoke(compose_flow), 신뢰된 내부 invoke 경로는 gap_event가 소유**하며, 반대 경로는 GapEvent 상태를 전이하지 않는다.
- **외부 `agent-recompose`의 payload 서버 조립**: 외부/직접 EMERGENCY 호출에서 agent_invoke는 클라이언트 payload를 신뢰하지 않고 `eventId`로 GapEvent→영향 Crew→READY 후보를 조회해 **서버 측에서 EMERGENCY payload를 조립**하며, 이때 8.1의 `compute_fixed_members`/`compute_missing`와 8.4의 `build_emergency_payload` 순수 로직을 **재사용**(gap_event와 동일 로직, 분기 없음)한다. 신뢰된 내부 invoke는 gap_event가 조립한 payload를 그대로 소비한다. 이 재사용으로 5.3(외부 경로)이 8.1·8.4에 의존하지만, Task Dependency Graph의 기존 wave 순서(8.1=wave 2, 8.4=wave 3, 5.3=wave 4)가 이 의존을 이미 만족하므로 그래프는 변경하지 않는다.
- **외부 `agent-recompose` 경로의 GapEvent 종료 전이 소유(성공 PROPOSED/실패 FAILED)는 agent_invoke가, 내부 invoke 경로는 gap_event가 소유**: "잠금(`DETECTED→RECOMPOSING`)을 획득한 주체가 종료 전이(`RECOMPOSING→PROPOSED`/`RECOMPOSING→FAILED`)도 소유한다"는 원칙으로 대칭 정리한다. **외부/직접 `agent-recompose` 경로**는 agent_invoke가 잠금을 직접 획득하므로 저장 성공 시 `RECOMPOSING→PROPOSED`, 재시도 소진 실패 시 `RECOMPOSING→FAILED` + 수동 편성 안내까지 **agent_invoke(compose_flow, 성공 전이 5.3 / 실패 전이 6.3)** 가 소유한다. **신뢰된 내부 invoke 경로**는 gap_event가 잠금을 선점하므로 종료 전이를 **gap_event(8.5)** 가 소유하고 agent_invoke 내부 경로는 GapEvent를 전이하지 않는다. 저장 함수 `save_emergency_proposal`(5.2)은 두 경로 모두 Crew(PROPOSED, source=AGENT) 저장만 하고 GapEvent를 전이하지 않으며, 종료 전이는 경로 소유자의 오케스트레이션에서 수행한다. 이는 gap_event 주도 내부 흐름을 규정한 Req 10.7과 충돌하지 않으며(외부 직접 route Req 6.2·6.6/6.7·10.10는 agent_invoke가 처리), 외부 route에 대한 설계 결정으로서 agent_invoke가 종료 전이를 맡는다.
- **저장 흐름 분리**: NORMAL만 WorkRequest를 `COMPOSING→PROPOSED`로 전이한다. EMERGENCY는 `save_emergency_proposal`이 Crew(PROPOSED) 저장만 하며 WorkRequest 상태 머신을 되돌리거나 변경하지 않고 GapEvent도 전이하지 않는다. **EMERGENCY의 GapEvent 종료 전이는 경로 소유자의 오케스트레이션이 담당한다 — 신뢰된 내부 invoke 경로는 gap_event, 외부/직접 `agent-recompose` 경로는 agent_invoke의 compose_flow**.
- **Property 9 단언 범위 및 5.4/6.4 책임 분리**: 5.4(Property 9)는 무효 출력에 대해 **"저장 없음 + PROPOSED 전이 없음"**(WorkRequest `COMPOSING→PROPOSED`·GapEvent `RECOMPOSING→PROPOSED` 미호출)만 단언하며, design.md Property 9와 문구를 일치시킨다. "상태전이 호출 0회"라는 넓은 표현은 쓰지 않는다 — NORMAL 재시도 소진 롤백(`COMPOSING→REQUESTED`)은 정당한 전이이므로 5.4의 금지 대상이 아니고 **6.4에서 롤백으로 별도 검증**한다. 이 책임 분리로 Property 9(저장/PROPOSED 미발생)와 롤백(6.3/6.4) 요구가 충돌하지 않는다.
- **검증 직전 최신 스냅샷**: 검증기에 주입하는 `ValidationContext`는 handler/assembler가 검증 직전에 `shared/db.get_workers`로 조립한 최신 스냅샷을 사용한다. `validator.py`는 I/O 없는 순수 함수로 유지한다.
- **B의 GapEvent 책임은 PROPOSED까지**: 본 범위는 `DETECTED→RECOMPOSING→PROPOSED`(또는 실패 시 `FAILED`)만 담당한다. GapEvent `APPROVED/FILLED` 전이, 대체 인력 `READY→RESERVED→RUNNING` 배정, 이탈자 `INACTIVE` 처리는 담당자 A의 긴급 승인 API(`/office/emergency/{eventId}/approve`) 범위다.
- `backend/shared/*`(db/auth/state/response)는 담당자 A 소유로 **직접 구현하지 않고** mock/스텁으로 대체해 소비한다. 통합 지점은 mock 기반으로 배선한다.
- 범위 밖(테이블/GSI 정의, Cognito, 코어 API, React, 승인·배정·워커 상태전이(READY→RESERVED→RUNNING), GapEvent `APPROVED/FILLED` 전이, ML 모델, 별도 긴급 Agent)은 구현하지 않는다.
- 검증기·스키마·결원 계산·폴백 컴포저는 I/O 없는 순수 함수로 구현해 결정적 PBT가 가능하도록 한다.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1"] },
    { "id": 2, "tasks": ["1.3", "2.2", "3.1", "5.2", "6.1", "8.1", "9.1"] },
    {
      "id": 3,
      "tasks": [
        "2.3",
        "3.2",
        "3.3",
        "3.4",
        "3.5",
        "3.6",
        "3.7",
        "3.8",
        "3.9",
        "3.10",
        "5.1",
        "6.2",
        "8.2",
        "8.3",
        "8.4",
        "9.3"
      ]
    },
    { "id": 4, "tasks": ["2.4", "5.3"] },
    { "id": 5, "tasks": ["5.4", "5.5", "8.5"] },
    { "id": 6, "tasks": ["6.3", "8.6"] },
    { "id": 7, "tasks": ["6.4", "9.2"] },
    { "id": 8, "tasks": ["9.4"] }
  ]
}
```
