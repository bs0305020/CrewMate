# CrewMate — Kiro 개발용 PRD (Product Requirements Document)

> **이 문서의 용도**: Kiro의 Spec 모드에 입력하여 `requirements.md → design.md → tasks.md`를 생성하기 위한 마스터 PRD입니다.
> Kiro에게 지시할 때는 이 문서 전체를 컨텍스트로 제공한 뒤, **"이 PRD를 기반으로 Spec을 생성해줘"** 로 시작하세요.

---

## 0. Kiro 작업 지침 (Steering Rules)

Kiro는 아래 원칙을 **모든 작업에서 최우선**으로 준수한다.

1. **100% 서버리스**: EC2, ECS/Fargate, 상시 실행 컨테이너 금지. 컴퓨팅은 Lambda(+ Step Functions)만 사용한다.
2. **데모 우선(Demo-First)**: 6일 안에 "처음부터 끝까지 이어지는 하나의 데이터/동작 흐름"이 시연 가능해야 한다. 완성도보다 E2E 흐름 연결이 우선이다.
3. **폴백 우선 설계**: 모든 AWS 매니지드 서비스 연동에는 폴백 경로를 함께 구현한다 (§10 참조). 폴백 경로가 막히면 데모가 막힌다.
4. **Mock 데이터 기반**: 실데이터 없음. Faker 기반 시드 고정(seed=42) 합성 데이터로 학습·시연한다.
5. **법·윤리 제약은 코드 레벨 제약**: 개인 단위 노쇼 횟수·출근 확률·네거티브 점수를 API 응답, UI, 로그에 절대 노출하지 않는다 (§8 참조). 이것은 기능 요구사항이다.
6. **단순한 것을 먼저**: 6일 일정이므로 과한 추상화 금지. 모놀리식 Lambda 핸들러 여러 개 + 공용 레이어 수준이면 충분하다.

---

## 1. 프로젝트 개요

- **프로젝트명**: CrewMate — 클라우드 기반 AI 건설현장 작업조 자동 편성 및 실시간 인력 재배치 플랫폼
- **형태**: 반응형 웹 (React SPA). 근로자용 모바일 웹 + 관리자(현장/사무소)용 콘솔
- **한 줄 설명**: 수기·전화 기반 레거시 건설 인력 중개를 클라우드로 전환하고, AI가 출근 신뢰도·기능 수준·근접성·비용을 종합해 현장별 최적 작업조를 자동 편성하며, 노쇼 발생 시 배차 이벤트 방식으로 실시간 대체 인력을 매칭하는 B2B2C 플랫폼
- **개발 기간**: 6일 (Code Assistant/Kiro 중심 개발)
- **비용 제약**: AWS Credit 지원으로 비용 걱정 없음. 단, 아키텍처는 사용량 과금(서버리스) 원칙 유지

---

## 2. 사용자 역할 및 핵심 플로우

### 2.1 3자 역할

| 역할 | 설명 | 주요 화면 |
|---|---|---|
| **근로자 (WORKER)** | 인력사무소에 지원해 대기 풀에 등록되는 일용직 근로자 | 모바일 웹: 지원, 배정 확인, GPS 출퇴근, 출근 잔디, 긴급 배차 푸시 수락 |
| **인력사무소 (OFFICE)** | 근로자 풀을 보유·관리하고, 건설사 공고에 지원(수주)하여 AI로 작업조를 편성하는 중개 주체 | 백오피스: 근로자 승인/풀 관리, 공고 수주, AI 편성 실행·검토, 보너스 승인, CSV 임포트 |
| **건설사/현장 (SITE)** | 인력 수요 공고를 게시하고, 편성안을 확정하며, 출결을 승인하는 현장 관리자 | 운영 콘솔: 공고 게시(가중치 설정), 편성안 확정, 출결 승인, 결원 대응(보너스 제시), 일일 리포트 |

### 2.2 핵심 플로우 (잡코리아식 지원 모델)

```
[근로자]                [인력사무소]                    [건설사/현장]
   │                        │                              │
   │ ① 사무소에 지원 ──────▶│                              │
   │                        │ ② 승인 → 대기 풀(POOLED)     │
   │                        │                              │ ③ 공고 게시
   │                        │                              │   (직종·인원·기간·일당·
   │                        │◀───────── 공고 노출 ─────────│    우선순위 가중치)
   │                        │ ④ 공고에 지원(수주)          │
   │                        │ ⑤ AI 작업조 편성 실행        │
   │                        │    (대기 풀 근로자 대상,     │
   │                        │     M1+M2, 상위 3안)         │
   │                        │ ⑥ 1안 선택 → 제안 제출 ─────▶│
   │                        │                              │ ⑦ 편성안 확정
   │◀── ⑧ 배정 통보(SNS) ───┤                              │
   │ ⑨ 수락 → ASSIGNED      │                              │
   │ ⑩ 당일 GPS 지오펜스 출근 체크인 ─────────────────────▶│ ⑪ 출결 승인
   │ ⑫ 출결 확정 → 잔디 적립│                              │
   │                        │                              │
   ├─ (노쇼 발생 시) ───────┤─────────── 긴급 배차 플로우 ──┤
   │                        │                              │ ⑬ 출근 마감 시각 미출근 감지
   │                        │                              │ ⑭ 보너스 제시
   │                        │ ⑮ 보너스 승인                │
   │◀─ ⑯ M3 랭킹 상위 후보부터 순차 푸시                    │
   │ ⑰ 수락 → 작업조 갱신 → WebSocket 실시간 반영 ─────────▶│
```

### 2.3 상태 머신 (핵심 엔터티)

- **WorkerPoolStatus**: `APPLIED → POOLED → (ASSIGNED ↔ POOLED)`
- **JobPosting**: `OPEN → OFFICE_APPLIED → PROPOSED → CONFIRMED → IN_PROGRESS → CLOSED`
- **CrewAssignment(개인 배정)**: `NOTIFIED → ACCEPTED → CHECKED_IN → CONFIRMED(출결 확정)` / `NOTIFIED → DECLINED` / `ACCEPTED → NO_SHOW`
- **DispatchEvent(긴급 배차)**: `GAP_DETECTED → BONUS_PROPOSED → BONUS_APPROVED → DISPATCHING(순차 발송) → FILLED / EXPIRED`

---

## 3. 기능 요구사항

각 요구사항은 EARS 형식의 수용 기준(Acceptance Criteria)을 포함한다. **[P0] = 데모 필수, [P1] = 가능하면, [P2] = 시간 남으면.**

### F1. 인증 및 역할 관리 [P0]
- **US-1.1**: 사용자는 근로자/인력사무소/건설사 중 하나의 역할로 가입·로그인할 수 있다.
  - WHEN 사용자가 로그인하면, THE SYSTEM SHALL Cognito User Pool 그룹(WORKER/OFFICE/SITE) 기반으로 역할별 화면과 API 권한을 분리한다.
  - 데모 간소화: 회원가입 폼 대신 **시드된 데모 계정 3종**으로 로그인 가능하면 충분하다.

### F2. 클라우드 중앙 인력 정보 관리 [P0]
- **US-2.1**: 인력사무소는 근로자 프로필(직종, 기능등급, 자격, 거주 좌표, 활동 지역)을 중앙 DB에서 조회·관리할 수 있다.
- **US-2.2**: 근로자는 사무소에 지원할 수 있고, 사무소는 승인하여 대기 풀에 편입한다.
  - WHEN 사무소가 지원을 승인하면, THE SYSTEM SHALL 해당 근로자를 POOLED 상태로 전환하고 편성 후보 풀에 포함시킨다.
- **US-2.3 [P1]**: 사무소는 레거시 엑셀(CSV)을 업로드하여 표준 스키마로 일괄 이관할 수 있다.
  - WHEN CSV가 업로드되면, THE SYSTEM SHALL Lambda로 파싱·검증 후 DB에 적재하고 성공/실패 건수를 반환한다.

### F3. 공고 게시 및 수주 [P0]
- **US-3.1**: 건설사는 필요 직종·인원·기간·일당과 **우선순위 가중치(비용/숙련/근접/안정성, 슬라이더 합계 1)**를 포함한 공고를 게시할 수 있다.
- **US-3.2 [P1]**: 건설사는 자연어로도 공고를 작성할 수 있다: *"내일 오전 7시, 형틀목공 4명에 보통인부 2명, 비용 우선으로"* → Bedrock이 구조화 JSON으로 파싱해 폼을 자동 채운다.
- **US-3.3**: 인력사무소는 공고 목록을 보고 지원(수주)할 수 있다.

### F4. AI 작업조 자동 편성 [P0] — 데모의 핵심 1
- **US-4.1**: 사무소가 수주한 공고에 대해 "AI 편성 실행"을 누르면, 자기 대기 풀 근로자 중에서 제약 조건(직종별 필수 인원·자격)을 만족하는 **상위 3개 작업조 조합**이 반환된다.
  - WHEN 편성이 실행되면, THE SYSTEM SHALL ① 후보 풀 조회 → ② M1 출근확률 스코어 결합 → ③ M2 가중 목적함수 최적화 → ④ 상위 3안 + 각 조합의 점수 분해(숙련/비용/근접/안정성)를 반환한다.
  - 응답 시간 10초 이내 (동기 API).
- **US-4.2**: 각 편성안에는 Bedrock이 생성한 **매칭 사유 설명(XAI)** 이 포함된다. 예: "이 조합은 요청 예산 대비 12% 절감되며, 3인이 최근 60일 내 동일 공정 협업 이력이 있습니다."
- **US-4.3**: 사무소가 1안을 선택해 제출하면 건설사가 확정하고, 확정 시 SNS로 근로자에게 배정 통보가 발송된다.
  - 데모 간소화: 근로자 수락은 앱 내 버튼 1탭으로 처리.

### F5. GPS 지오펜스 출결 + 출근 잔디 [P0] — 데모의 핵심 2
- **US-5.1**: 근로자 앱은 현장 지오펜스 진입/이탈 이벤트를 발생시킨다 (Amazon Location Service).
  - 데모 간소화: 실제 GPS 대신 **개발자용 위치 시뮬레이터 버튼**("현장 도착" / "현장 이탈")으로 좌표를 주입할 수 있어야 한다.
- **US-5.2**: WHEN 출근 위치와 퇴근 위치가 동일 지오펜스이고 현장 관리자 승인이 있으면, THE SYSTEM SHALL 출결을 확정(CONFIRMED)하고 DB에 기록한다.
- **US-5.3**: 확정된 출결은 근로자의 **잔디 그래프(GitHub contribution 스타일)** 에 적립되며, 연속 출근 스트릭·공정 완수 배지가 표시된다.

### F6. 실시간 결원 감지 및 긴급 배차 [P0] — 데모의 핵심 3
- **US-6.1**: WHEN 출근 마감 시각(EventBridge 스케줄)까지 지오펜스 진입 이벤트가 없는 배정 인원이 있으면, THE SYSTEM SHALL 결원 이벤트(GAP_DETECTED)를 발생시키고 현장 콘솔에 알림을 띄운다.
  - 데모 간소화: 마감 시각 대기 대신 **"노쇼 시뮬레이션" 버튼**으로 즉시 트리거 가능해야 한다.
- **US-6.2**: 현장이 긴급 보너스 금액을 제시하고 사무소가 승인하면(보너스 합의 플로우), 긴급 배차가 시작된다.
- **US-6.3**: WHEN 긴급 배차가 시작되면, THE SYSTEM SHALL Step Functions가 ① 대체 후보 조회 → ② M3 기대 충원 성공률(수락확률 × 도착 가능성) 랭킹 → ③ SQS 큐 → SNS 푸시로 **상위 후보부터 순차 발송**한다 (동시 발송 금지, 후보당 응답 대기 타임아웃 후 다음 후보).
- **US-6.4**: WHEN 후보가 수락하면, THE SYSTEM SHALL 작업조를 즉시 갱신하고 **WebSocket(API Gateway)** 으로 현장 콘솔에 실시간 반영한다.

### F7. 생성형 AI 운영 어시스턴트 [P1]
- **US-7.1**: 자연어 공고 파싱 (F3.2와 동일, Bedrock)
- **US-7.2**: 매칭 사유 설명 생성 (F4.2와 동일)
- **US-7.3**: 일일 운영 리포트 자동 생성 — 출결 요약, 결원·대체 이벤트 내역, 익일 편성 현황을 Bedrock으로 요약해 현장 콘솔에 표시.

---

## 4. 시스템 아키텍처 (완전 서버리스)

```
사용자(3 role) → CloudFront → S3 (React 정적 호스팅)
                     │
                     ▼
              Cognito (역할별 인증)
                     │
                     ▼
        API Gateway (REST + WebSocket)
                     │
   ┌─────────────────┼──────────────────────────┐
   ▼                 ▼                          ▼
Lambda Core API   Lambda 편성 엔진(M2)      Step Functions (긴급 배차)
(CRUD·승인·공고)   + SageMaker(M1) 호출        │→ Lambda 후보조회 → SageMaker(M3)
   │                 │                          │→ SQS 순차 큐 → SNS 푸시
   ▼                 ▼                          │→ 수락 시 WebSocket 반영
Aurora Serverless v2 (PostgreSQL + PostGIS)   DynamoDB (실시간 배차 상태·위치 캐시)
                     │
Amazon Location Service (지오펜스) → EventBridge → 출결 Lambda / 결원감지 Lambda
Amazon Bedrock (Claude): NL 파싱 · XAI 사유 · 일일 리포트
S3 Data Lake ← Faker 시드 데이터 → SageMaker Training → Serverless Endpoint (M1·M3)
```

### 서비스 매핑

| 영역 | 서비스 | 용도 |
|---|---|---|
| 프론트 | S3 + CloudFront | React SPA 정적 호스팅 |
| 인증 | Cognito | 3자 역할별 인증 (User Pool 그룹) |
| API | API Gateway | REST + WebSocket(배차 실시간) |
| 컴퓨팅 | Lambda (Python 3.12) | Core API, 편성 엔진, 출결 확정, 결원 감지, CSV 임포트, Bedrock 호출 |
| 오케스트레이션 | Step Functions | 긴급 배차 순차 플로우 |
| 이벤트 | EventBridge | 지오펜스 이벤트 라우팅, 출근 마감 스케줄, 일일 리포트 스케줄 |
| 큐/알림 | SQS / SNS | 순차 배차 큐, 모바일 푸시(데모: 웹 알림으로 대체 가능) |
| 데이터 | Aurora Serverless v2 (PostGIS) | 근로자·현장·공고·작업조·출결 관계형 코어 |
| 데이터 | DynamoDB | 실시간 배차 상태, 위치 캐시, WebSocket 커넥션 관리 |
| 데이터 | S3 | 데이터 레이크(학습 데이터), CSV 업로드 |
| AI/ML | SageMaker (Serverless Inference) | M1·M3 학습·추론 |
| AI/ML | Bedrock (Claude) | M4: NL 파싱, XAI, 리포트 |
| 위치 | Amazon Location Service | 지도, 지오펜스, 진입/이탈 이벤트 |
| IaC/배포 | AWS SAM + GitHub Actions | 인프라 코드화, CI/CD |
| 관측 | CloudWatch | 로그·모니터링 |

> **주의**: 기획서 초안의 ECS Fargate + Spring Boot는 폐기. Core API도 Lambda로 통일한다. 언어는 Python 3.12 단일화(ML 코드와 공유)를 기본으로 하되, 팀 판단으로 Node.js 변경 가능 — 단 하나로 통일할 것.

---

## 5. 데이터 모델 (핵심 엔터티)

Aurora(PostgreSQL) 기준. Kiro는 design 단계에서 상세 스키마(DDL)를 생성할 것.

| 엔터티 | 핵심 필드 |
|---|---|
| `users` | id, role(WORKER/OFFICE/SITE), cognito_sub, name, phone |
| `workers` | user_id, trade(직종 10종), skill_grade, certifications[], home_location(Point), active_region |
| `offices` | user_id, name, region |
| `sites` | user_id, company_name, site_name, location(Point), geofence_id |
| `pool_applications` | worker_id, office_id, status(APPLIED/POOLED/REJECTED) |
| `job_postings` | site_id, trades_required(jsonb: 직종별 인원), start_date, days, daily_wage, weights(jsonb: w1~w4), status |
| `office_bids` | posting_id, office_id, status |
| `crew_proposals` | posting_id, office_id, rank(1~3), member_ids[], score_breakdown(jsonb), xai_reason(text), status |
| `assignments` | proposal_id, worker_id, date, status(NOTIFIED/ACCEPTED/CHECKED_IN/CONFIRMED/NO_SHOW/DECLINED) |
| `attendance_records` | assignment_id, check_in_at/loc, check_out_at/loc, approved_by, confirmed_at |
| `dispatch_events` | assignment_id(결원), bonus_amount, status, candidate_queue(jsonb) |
| `dispatch_offers` | dispatch_id, worker_id, seq, sent_at, responded_at, response |
| `collab_history` | worker_a, worker_b, posting_id, date (동일 작업조 공동 투입 파생 기록) |

DynamoDB 테이블: `dispatch_state`(배차 실시간 상태), `ws_connections`(WebSocket 커넥션), `location_cache`(최근 위치).

---

## 6. ML 엔진 사양

| # | 엔진 | 유형 | 모델 | 서빙 | 우선순위 |
|---|---|---|---|---|---|
| M1 | 출근 확률 예측 | 이진 분류 | XGBoost | SageMaker Serverless Endpoint | P0 |
| M2 | 작업조 편성 최적화 | 제약 조합 최적화 | 가중 목적함수 + Greedy/Local Search | Lambda (비학습) | P0 |
| M3 | 긴급 배차 수락확률 랭킹 | 이진 분류 + 랭킹 | XGBoost/LogReg | SageMaker Endpoint (M1과 멀티모델 공유 가능) | P0 |
| M4 | 생성형 AI | NL 파싱·XAI·요약 | Bedrock Claude (프롬프트만) | Lambda 경유 API 호출 | P0(파싱·XAI), P1(리포트) |

### M1 피처
최근 30일 출근율, 연속 출근 스트릭, 자택–현장 거리, 요일, 배정 통보 리드타임, 일당 수준, 해당 공정 경험 횟수, 기상 조건(mock).
**출력 P(출근)은 개인에게 절대 비공개** — 작업조 안정성 = Π P(출근) 산출용 내부 변수로만 사용.

### M2 목적함수
```
Score(작업조) = w₁·숙련 적합도 + w₂·비용 효율 + w₃·근접성 + w₄·안정성(M1)
(Σw = 1, 건설사 공고의 슬라이더 가중치)
제약: 직종별 필수 인원, 자격 요건
알고리즘: 후보 필터링 → Greedy 초기해 → Local Search(스왑 개선). 확장: OR-Tools CP-SAT 대체 가능하게 모듈화.
```

### M3 기대 충원 성공률
```
기대 충원 성공률 = P(수락 | 거리, 시간대, 보너스 금액, 과거 수락률, 당일 가용성) × 도착 가능성(ETA 기반)
```
보너스 금액이 피처이므로, [P2] "보너스 금액 슬라이더 → 충원 확률 변화" 시뮬레이션 UI 데모 가능.

### M4 프롬프트 3종
1. 자연어 → JSON 매칭 조건 파싱 (few-shot, JSON 강제)
2. M2 점수 분해 + 협업 이력 → 매칭 사유 설명문 생성
3. 당일 운영 데이터 → 일일 리포트 생성

---

## 7. Mock 데이터 계획

Python + Faker, **seed 고정(42)**, 생성 스크립트는 `scripts/seed/`에 저장하고 원천 데이터는 S3에 적재.

| 데이터셋 | 규모 | 방식 |
|---|---|---|
| 근로자 프로필 | 800명 | 직종 10종(형틀목공·철근·미장·보통인부 등)·기능등급·자격·부산권 거주 좌표 분포 |
| 인력사무소 | 3~5곳 | 데모 계정 포함, 근로자를 사무소별 풀에 분배 |
| 현장 | 20곳 | 부산권 좌표, 공정 캘린더·필요 직종 구성, 지오펜스 등록 |
| 출결 이력 | 90일 × 배정 건 | 근로자별 성실도 잠재변수 → 노쇼 확률적 주입 → **M1 학습 라벨** |
| 협업 이력 | 배정 이력에서 파생 | 동일 작업조 공동 투입 기록 |
| 배차 응답 이력 | 500건 | 거리·보너스·시간대별 수락/거절 시나리오 → **M3 학습 라벨** |
| "오늘" 데모 데이터 | 별도 시딩 | 확정 편성 1건 + 노쇼 대상자 1명 + 대기 대체 후보 등 데모 시나리오 전용 |

---

## 8. 법·윤리 제약 (기능 요구사항으로 취급)

1. **네거티브 정보 비노출**: 노쇼 횟수, 마이너스 점수, 개인별 출근 확률(M1 출력)을 어떤 API 응답·UI·근로자 화면에도 노출하지 않는다. 사업장 간 공유 금지 (근로기준법 제40조 취업 방해 금지).
2. **포지티브 온리**: 근로자에게 노출되는 것은 본인의 잔디·배지·스트릭뿐이다.
3. **XAI 의무**: 모든 편성 결과에 매칭 사유 설명을 첨부한다.
4. **열람·정정권**: [P2] 근로자는 자신에 대해 수집된 데이터를 열람할 수 있다.
5. 코드 리뷰 관점: M1 확률값이 담긴 필드는 API 스키마에서 내부 전용으로 격리하고, 프론트로 나가는 DTO에 포함시키지 않는다.

---

## 9. 6일 개발 마일스톤

| 일차 | 목표 | 산출물 |
|---|---|---|
| Day 1 | Kiro Spec 생성(requirements/design/tasks), DB 스키마 확정, SAM 프로젝트 골격, Cognito·S3·CloudFront 셋업, Mock 데이터 스크립트 착수 | 배포되는 Hello-world 스택 |
| Day 2 | Aurora Serverless·DynamoDB 구축, Core API Lambda(회원/풀/공고/수주 CRUD), React 골격 + 로그인 + 역할별 라우팅 | 공고 게시→수주까지 동작 |
| Day 3 | Mock 데이터 완성·S3 적재, M1 학습·엔드포인트 배포, M2 편성 Lambda + 편성 API, 편성 결과 UI(상위 3안 카드) | AI 편성 E2E 동작 |
| Day 4 | Location Service 지오펜스 + 위치 시뮬레이터, 출결 확정 파이프라인, 잔디 UI, 결원 감지→M3→Step Functions→SQS/SNS 긴급 배차, WebSocket 실시간 반영 | 출결·긴급배차 E2E 동작 |
| Day 5 | Bedrock 통합(파싱·XAI·리포트), 프론트–백엔드 통합 마감, 데모 시나리오 데이터 시딩, 버퍼 | 데모 시나리오 1~3 통과 |
| Day 6 | 통합 테스트, 버그 픽스, 데모 리허설, 발표 자료 | 최종 데모 |

---

## 10. 리스크 및 폴백 (반드시 구현 가능한 형태로 설계)

| 리스크 | 폴백 |
|---|---|
| SageMaker 학습·배포 지연 | 로컬(scikit-learn/XGBoost) 학습 → 아티팩트를 Lambda 컨테이너/레이어 추론으로 대체 (인터페이스 동일 유지) |
| Location Service 지오펜스 이슈 | 좌표 수신 후 Haversine 거리 검증 Lambda로 폴백 (기능 동일, 서비스만 대체) |
| Bedrock 권한/리전 문제 | 핵심 프롬프트 3종의 캐시 응답(JSON 파일)으로 데모 폴백 |
| Aurora Serverless + VPC 셋업 지연 | 핵심 엔터티를 DynamoDB 단일 테이블로 대체, 공간 질의는 Haversine으로 대체 |
| SNS 모바일 푸시 복잡도 | 웹 인앱 알림(WebSocket/폴링)으로 대체 — 데모 화면상 동일 효과 |

편성 엔진(M2)·배차 랭킹 호출부는 **어댑터 패턴**으로 감싸서 SageMaker ↔ Lambda 추론 전환이 환경 변수 하나로 가능해야 한다.

---

## 11. 데모 시나리오 (최종 수용 기준 — 이 3개가 끊김 없이 이어져야 함)

1. **AI 편성**: 건설사가 자연어로 *"내일 오전 7시, 형틀목공 4명 + 보통인부 2명, 비용 우선"* 공고 게시 → 사무소가 수주 후 AI 편성 실행 → 상위 3안 + 매칭 사유(XAI) 표시 → 1안 제출 → 건설사 확정 → 근로자 통보·수락
2. **GPS 출결**: 근로자가 위치 시뮬레이터로 지오펜스 진입 → 출근 체크 → 퇴근 시 동일 지오펜스 + 관리자 승인 → 잔디 1칸 적립 화면
3. **긴급 배차**: 노쇼 시뮬레이션 버튼 → 결원 이벤트 → 현장 보너스 제시 → 사무소 승인 → 대체 후보 순차 푸시 → 수락 → 현장 콘솔에 작업조 실시간 갱신(WebSocket) → 일일 운영 리포트 자동 생성

## 12. Out of Scope

결제·임금 정산, 실제 근로계약 체결, 실데이터 연동, 네이티브 모바일 앱(모바일 웹으로 대체), 다중 사무소 간 경쟁 입찰 로직 고도화. 확장 로드맵으로만 문서에 명시한다.