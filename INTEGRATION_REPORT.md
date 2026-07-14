# PROMPT 2 통합 보고서 — 모노레포 통합 · real 전환 · E2E 정합

세 파트(backend/agent/frontend)는 이미 하나의 저장소에 있고, 프론트엔드는 계약 v2 기준으로
구현되어 있다. 이 단계에서는 프론트 mock 응답과 백엔드 real 응답의 **필드 단위 정합**을 맞추고,
real 전환에 필요한 설정(환경 변수·CORS)을 정리했다. 디버깅 우선순위는 프론트(1) > Agent(2) > 백엔드(3)를
따랐고, 어긋난 부분은 백엔드를 프론트 기대에 맞췄다.

> **이 환경의 한계**: AWS 자격증명이 없어 `sam deploy` / 시드 실행 / 배포된 CloudFront 대상 E2E는
> 수행하지 못했다. 대신 코드 정합·빌드·단위검증을 완료하고, 배포 시 확인할 항목을 §5에 명시했다.

---

## 1. Phase 1 — 조사 요약

- **(a) 파일 배치**: 이미 목표 구조(모노레포). `backend/`(shared·functions·agent), `frontend/`, `scripts/seed/`, `template.yaml`, `tests/`. 물리 이동 불필요.
- **(b) 응답 필드 정합**: 프론트 mock 응답 형태와 백엔드 real 응답을 엔드포인트별로 대조. 봉투·배열·엔터티 필드 대부분 일치. **불일치 2건** 발견(§2).
- **(c) 중복 모듈**: 없음(프론트 TS / 백엔드 Py, Agent는 `backend/shared` 소비).
- **(d) 하드코딩 URL·크리덴셜**: 소스에 AWS 키 없음(grep 확인). CloudFront URL은 문서에만. `.env.production`은 placeholder. mock 토큰은 가짜 문자열.
- **(e) CORS**: 백엔드가 성공·오류 응답 **모두**에 CORS 헤더 부착(오류 응답 누락 버그 없음). origin이 `"*"` 하드코딩이라 환경변수화(§3).

---

## 2. 프론트↔백엔드 불일치 수정 (백엔드를 프론트에 맞춤, 규칙 1)

| # | 증상 | 원인 | 수정 |
|---|---|---|---|
| M1 | 근로자 홈의 "완료 작업" 수치가 빈값이 될 수 있음 | `worker_self_view`가 `completed_count`를 제외 | self-view에 **`completed_count` 포함**. 성실도 비율의 분모 `dispatched_count`는 계속 OFFICE 응답 한정(비율 노출은 사무소만). |
| M2 | 거절 발생 후 사무소/건설사 화면이 부분 재편성·긴급 UI를 못 띄움 | `assemble_crew_members`가 거절/노쇼/이탈/취소 멤버를 목록에서 제거 | `active_only` 파라미터 추가. **화면 표시용 응답**(office/company 요청 상세)은 `active_only=False`로 거절 멤버를 `acceptance=DECLINED`로 포함. **편성 계산용**(fill-gap·긴급 승인의 고정 인원)은 기존대로 active-only. |

두 수정 모두 PROMPT 1의 성실도 규칙(비율은 사무소 한정)과 원자 트랜잭션 로직에는 영향 없음.
롤업(출근/퇴근/수락 집계)은 별도 assignment 조회를 쓰므로 M2 변경과 무관.

---

## 3. Phase 2 — 통합 설정

- **환경 변수**: 프론트 `client.ts`가 `VITE_API_BASE_URL || VITE_API_URL` 순으로 베이스 URL을 읽도록 정리(하위호환 유지). `VITE_API_MODE=mock|real` 그대로.
- **`.env.example` 신규**: `frontend/.env.example` — `VITE_API_MODE`, `VITE_API_BASE_URL`(스테이지 경로 `/dev` 포함 안내).
- **CORS**: `shared/responses.py`가 `CORS_ALLOW_ORIGIN` 환경 변수를 읽어 모든 응답(성공·오류)에 부착(기본 `"*"`). 템플릿 파라미터 `CorsAllowOrigin`이 이 값을 주입(배포 시 CloudFront 오리진으로 좁힘 가능). OPTIONS 프리플라이트는 SAM `Api.Cors` + `AddDefaultAuthorizerToCorsPreflight:false`로 처리.
- **빌드 검증**: `npm ci && npm run build`(tsc + vite) **성공, 0 에러**. 백엔드 `sam validate --lint` valid(PROMPT 1에서 확인).

---

## 4. 수정 파일 목록 (PROMPT 2 범위)

**백엔드 (프론트 정합 위해 수정)**
- `backend/shared/schemas.py` — worker_self_view에 completed_count 포함(M1)
- `backend/shared/crew.py` — assemble_crew_members(active_only) (M2)
- `backend/shared/responses.py` — CORS origin 환경변수화
- `backend/functions/company_request/app.py` — 요청 상세 crew 표시 active_only=False (M2)
- `backend/functions/office_core/app.py` — 요청 상세 crew 표시 active_only=False (M2)

**프론트엔드 (규칙 1(a) mock↔real 설정 한정)**
- `frontend/src/api/client.ts` — 베이스 URL 변수 `VITE_API_BASE_URL` 우선 인식(설정 전용, 로직 변경 없음)
- `frontend/.env.example` — 신규

**테스트**
- `tests/test_v2_flow.py` — M1/M2 회귀 검증 보강

프론트엔드 **기능 로직**은 수정하지 않았다(Phase 4 항목은 mock에 이미 구현되어 있고 백엔드가 계약을 맞춤).

---

## 5. 남은 이슈 / 배포 시 반드시 확인할 것

1. **[최우선] 인증 토큰 형식**: 프론트가 `Authorization: Bearer <IdToken>`로 보낸다. API Gateway REST의 Cognito User Pools authorizer가 `Bearer ` 접두사를 거부해 401이 날 수 있다. **배포 후 로그인→인증 API 호출이 401이면** `frontend/src/api/client.ts`에서 `Bearer ` 접두사를 제거하고 토큰만 전송하도록 한 줄 수정(규칙 1(a) 설정 범위). 반대로 정상 동작하면 그대로 둔다.
2. **API 베이스 URL은 스테이지 포함**: `VITE_API_BASE_URL`은 반드시 `.../<api-id>/dev`처럼 스테이지 경로까지 포함해야 한다(`sam deploy` 출력 `ApiUrl` 그대로 사용).
3. **CORS origin**: 데모는 `"*"`로 동작. 운영 시 `CorsAllowOrigin` 파라미터를 CloudFront 오리진으로 좁힐 것.
4. **성실도 `10/11` 표기 미구현(프론트)**: PROMPT 2 §3.6은 OFFICE 후보 목록에 성실도 `완료/배차` 표기를 기대하나, 현재 프론트 화면(WorkersPage/ComposePage)은 이를 렌더링하지 않는다. 백엔드는 OFFICE 응답에 `completed_count`/`dispatched_count`를 이미 제공하므로, 표기가 필요하면 프론트에 소량 추가 필요(Phase 4 추가 항목 후보). 데모 필수는 아님.
5. **상태 전이값 차이(PROMPT 1에서 문서화)**: 거절→근로자 READY, 제안취소→READY, 편성취소→요청 REQUESTED (간소화된 mock과 다름). E2E에서 프론트 표시와 어긋나면 백엔드를 프론트에 맞춰 조정.
6. **이 환경 미수행**: `sam build` / `sam deploy` / `python scripts/seed/*.py` / 배포 URL E2E — AWS 자격증명 필요. 아래 순서로 팀이 수행.

---

## 6. 배포·E2E 실행 순서 (팀 수행)

```bash
# 1) 백엔드 배포
sam build
sam deploy --guided        # 최초 1회, 이후 sam deploy
#   출력에서 ApiUrl / UserPoolId / UserPoolClientId 확보

# 2) 시드 (배포된 테이블/풀 대상)
#    환경변수로 테이블명 주입 후 실행 (또는 스택 출력값 참고)
python scripts/seed/seed_workers.py --reset
python scripts/seed/seed_history.py
python scripts/seed/seed_demo_scenario.py
python scripts/seed/seed_cognito.py --stack-name <스택명>   # worker1/office1/company1 등 + 엔터티 연결

# 3) 프론트 real 전환 + 배포
#    frontend/.env.production 에 VITE_API_MODE=real, VITE_API_BASE_URL=<ApiUrl> 설정
cd frontend && npm ci && npm run build
#    dist/ 를 S3 업로드 + CloudFront 무효화 (frontend/deploy/deploy.ps1 참고)
```

---

## 7. 데모 리허설 체크리스트 (Phase 3, real 모드)

- [ ] 가입·로그인: 3역할 signup → login → 역할별 홈. OFFICE 가입 시 `GET /offices`에 신규 사무소 노출.
- [ ] 일반 편성: WORKER 지원서→대기 → COMPANY 요청 → OFFICE AI 편성(추천 카드 assigned_trade·offered_wage) → 임금 조절 → 승인 → WORKER 앱 제안 도착(폴링) → 수락 → 전원 수락 시 DISPATCHED → COMPANY 출근→RUNNING→퇴근→COMPLETED, 이력·완료수 반영.
- [ ] 거절→추가 편성: 조원 1명 거절 → GapEvent(DECLINED) → 요청 COMPOSING → 긴급 재편성 화면(수동/AI) → 대체자 수락 → FILLED.
- [ ] 노쇼→긴급 배차: RUNNING 작업조 노쇼 → EMERGENCY 추천(잔여 예산) → 승인 → 대체자 수락 → 작업조 갱신, COMPANY 화면에 신규 투입자 강조.
- [ ] 동시성: 같은 READY 근로자 이중 승인 → 한쪽 STATE_CONFLICT 안내.
- [ ] 노출 검증: COMPANY 화면/응답에 성실도·부정 라벨 없음. (OFFICE 성실도 표기는 §5-4 참고.)

---

## 8. 검증 결과 (이 환경)

- `pytest` — **7 passed** (M1/M2 회귀 포함).
- `npm ci && npm run build` — **성공, 0 에러** (tsc + vite).
- 변경 파일 diagnostics — 이상 없음.
- 크리덴셜 소스 부재, `frontend/.env.example` 존재.
