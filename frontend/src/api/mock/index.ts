import type {
  ApiResponse,
  LoginRequest,
  SignupRequest,
  LoginResponse,
  Worker,
  WorkRequest,
  WorkerApplicationRequest,
  CreateWorkRequestPayload,
  Crew,
  CrewMember,
  Trade,
  RequiredTrade,
  Recommendation,
  GapEvent,
} from '../types';
import { SEED_ACCOUNTS, SEED_OFFICES, mockState, setCurrentUserId, getCurrentUserId, registerAccount } from './state';

export const handlers: Record<string, (body?: unknown, pathParam?: string) => Promise<ApiResponse<unknown>>> = {
  // === 인증 ===
  'POST /auth/login': async (body) => {
    const { username, password } = body as LoginRequest;
    const account = SEED_ACCOUNTS[username];
    if (!account || account.password !== password) {
      return { success: false, error: { code: 'UNAUTHORIZED', message: '아이디 또는 비밀번호가 일치하지 않습니다.' } };
    }
    await delay(300);
    setCurrentUserId(account.user.userId);
    const response: LoginResponse = { user: account.user };
    return { success: true, data: response };
  },

  // 회원가입 (간단 — 아이디/비번/역할/이름 + 사무소는 지역)
  'POST /auth/signup': async (body) => {
    await delay(300);
    const { username, password, role, name, region } = body as SignupRequest;
    if (!username || !password || !name) {
      return { success: false, error: { code: 'INVALID_INPUT', message: '모든 항목을 입력해주세요.' } };
    }
    const result = registerAccount(username, password, role, name, region);
    if (!result.ok) {
      return { success: false, error: { code: 'USERNAME_TAKEN', message: result.error! } };
    }
    // 가입 즉시 로그인 처리
    setCurrentUserId(result.user!.userId);
    return { success: true, data: { user: result.user } };
  },

  // === 근로자 API ===
  'GET /worker/me': async () => {
    await delay(150);
    const userId = getCurrentUserId();
    const worker = mockState.workers.find((w) => w.user_id === userId);
    if (!worker) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: '근로자 정보를 찾을 수 없습니다.' } };
    return { success: true, data: worker };
  },

  'POST /worker/application': async (body) => {
    await delay(200);
    const userId = getCurrentUserId();
    const payload = body as WorkerApplicationRequest;
    const existingIdx = mockState.workers.findIndex((w) => w.user_id === userId);

    if (existingIdx >= 0) {
      const existing = mockState.workers[existingIdx];
      mockState.workers[existingIdx] = { ...existing, ...applyApplicationFields(payload), updated_at: new Date().toISOString() };
      return { success: true, data: mockState.workers[existingIdx] };
    }

    const newWorker: Worker = {
      worker_id: `W${String(mockState.workers.length + 1).padStart(3, '0')}`,
      user_id: userId!,
      state: 'INACTIVE',
      completed_count: 0,
      no_show_count: 0,
      rating: null,
      rating_count: 0,
      attended_count: 0,
      dispatched_count: 0,
      current_crew_id: null,
      current_offer: null,
      work_history: [],
      state_changed_at: new Date().toISOString(),
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      ...applyApplicationFields(payload),
    };
    mockState.workers.push(newWorker);
    return { success: true, data: newWorker };
  },

  'PUT /worker/application': async (body) => {
    await delay(200);
    const userId = getCurrentUserId();
    const payload = body as WorkerApplicationRequest;
    const idx = mockState.workers.findIndex((w) => w.user_id === userId);
    if (idx < 0) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: '근로자 정보를 찾을 수 없습니다.' } };
    mockState.workers[idx] = { ...mockState.workers[idx], ...applyApplicationFields(payload), updated_at: new Date().toISOString() };
    return { success: true, data: mockState.workers[idx] };
  },

  'POST /worker/state/ready': async () => {
    await delay(200);
    const userId = getCurrentUserId();
    const idx = mockState.workers.findIndex((w) => w.user_id === userId);
    if (idx < 0) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: '근로자 정보를 찾을 수 없습니다.' } };
    const worker = mockState.workers[idx];
    if (worker.state !== 'INACTIVE') return { success: false, error: { code: 'WORKER_NOT_READY', message: '대기 시작은 INACTIVE 상태에서만 가능합니다.' } };
    mockState.workers[idx] = { ...worker, state: 'READY', state_changed_at: now(), updated_at: now() };
    return { success: true, data: mockState.workers[idx] };
  },

  'POST /worker/state/inactive': async () => {
    await delay(200);
    const userId = getCurrentUserId();
    const idx = mockState.workers.findIndex((w) => w.user_id === userId);
    if (idx < 0) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: '근로자 정보를 찾을 수 없습니다.' } };
    const worker = mockState.workers[idx];
    if (worker.state === 'RESERVED' || worker.state === 'RUNNING' || worker.state === 'NOTIFIED') {
      return { success: false, error: { code: 'WORKER_ALREADY_RUNNING', message: '현재 상태에서는 대기를 취소할 수 없습니다.' } };
    }
    mockState.workers[idx] = { ...worker, state: 'INACTIVE', state_changed_at: now(), updated_at: now() };
    return { success: true, data: mockState.workers[idx] };
  },

  // 수락 (긴급 배차 시 예상 도착시간 eta 전달 가능)
  'POST /worker/offer/accept': async (body) => {
    await delay(200);
    const { eta } = (body || {}) as { eta?: string };
    const userId = getCurrentUserId();
    const idx = mockState.workers.findIndex((w) => w.user_id === userId);
    if (idx < 0) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: '근로자 정보를 찾을 수 없습니다.' } };
    const worker = mockState.workers[idx];
    if (worker.state !== 'NOTIFIED' || !worker.current_offer) {
      return { success: false, error: { code: 'STATE_CONFLICT', message: '수락할 배정 제안이 없습니다.' } };
    }

    // worker → RESERVED (배차완료 수 +1)
    mockState.workers[idx] = { ...worker, state: 'RESERVED', dispatched_count: (worker.dispatched_count || 0) + 1, state_changed_at: now(), updated_at: now() };

    // crew member acceptance 업데이트
    const crew = mockState.crews.find((c) => c.crew_id === worker.current_offer!.crew_id);
    if (crew) {
      const mIdx = crew.members.findIndex((m) => m.worker_id === worker.worker_id);
      if (mIdx >= 0) {
        crew.members[mIdx].acceptance = 'ACCEPTED';
        if (eta) crew.members[mIdx].eta = eta; // 긴급 배차 예상 도착시간 저장
      }
      crew.updated_at = now();

      const request = mockState.requests.find((r) => r.request_id === crew.request_id);
      const activeGap = findActiveGapEvent(crew.crew_id);

      if (activeGap && activeGap.status === 'APPROVED') {
        // 긴급 배차: 대체 인력이 모두 수락하면 GapEvent FILLED
        const hasPending = crew.members.some((m) => m.acceptance === 'PENDING');
        if (!hasPending) {
          const gIdx = mockState.gapEvents.findIndex((g) => g.event_id === activeGap.event_id);
          if (gIdx >= 0) mockState.gapEvents[gIdx] = { ...activeGap, status: 'FILLED', updated_at: now() };
          // 요청 상태 복구: 기존 팀원 중 작업중(RUNNING)이 있으면 RUNNING, 아니면 DISPATCHED
          const anyRunning = crew.member_ids.some((id) => {
            const w = mockState.workers.find((x) => x.worker_id === id);
            return w && w.state === 'RUNNING';
          });
          const reqIdx = mockState.requests.findIndex((r) => r.request_id === crew.request_id);
          if (reqIdx >= 0) { mockState.requests[reqIdx].status = anyRunning ? 'RUNNING' : 'DISPATCHED'; mockState.requests[reqIdx].updated_at = now(); }
          pushNotification('USER_COMPANY_001', 'GAP_FILLED', '긴급 배차 완료', `${request?.site_name || '현장'}의 결원이 대체 인력으로 충원되었습니다.`);
          pushNotification('USER_OFFICE_001', 'GAP_FILLED', '긴급 배차 완료', `긴급 대체 인력이 수락하여 작업조가 갱신되었습니다.`);
        }
      } else {
        // 일반 배차: 전원 수락 → DISPATCHED
        const allAccepted = crew.members.every((m) => m.acceptance === 'ACCEPTED');
        if (allAccepted) {
          crew.status = 'DISPATCHED';
          const reqIdx = mockState.requests.findIndex((r) => r.request_id === crew.request_id);
          if (reqIdx >= 0) { mockState.requests[reqIdx].status = 'DISPATCHED'; mockState.requests[reqIdx].updated_at = now(); }
          pushNotification('USER_OFFICE_001', 'DISPATCH_COMPLETE', '배차 완료', `${crew.crew_id} 작업조 전원이 수락했습니다.`);
          pushNotification('USER_COMPANY_001', 'DISPATCH_COMPLETE', '배차 완료', `요청한 인력이 모두 확정되었습니다.`);
        }
      }
    }

    return { success: true, data: mockState.workers[idx] };
  },

  // 거절
  'POST /worker/offer/decline': async () => {
    await delay(200);
    const userId = getCurrentUserId();
    const idx = mockState.workers.findIndex((w) => w.user_id === userId);
    if (idx < 0) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: '근로자 정보를 찾을 수 없습니다.' } };
    const worker = mockState.workers[idx];
    if (worker.state !== 'NOTIFIED' || !worker.current_offer) {
      return { success: false, error: { code: 'STATE_CONFLICT', message: '거절할 배정 제안이 없습니다.' } };
    }

    const crewId = worker.current_offer.crew_id;
    const crew = mockState.crews.find((c) => c.crew_id === crewId);
    if (crew) {
      // 거절 기록 + 해당 멤버만 DECLINED (나머지 팀원은 유지 = 부분 재편성)
      recordDecline(crew.request_id, worker.worker_id);
      const mIdx = crew.members.findIndex((m) => m.worker_id === worker.worker_id);
      if (mIdx >= 0) crew.members[mIdx].acceptance = 'DECLINED';
      crew.updated_at = now();
      // 빈 자리(결원) 이벤트 생성 → AI/수동 재편성 경로 노출
      createGapEvent(crew, worker.worker_id, worker.name, 'DECLINED');
      const reqIdx = mockState.requests.findIndex((r) => r.request_id === crew.request_id);
      if (reqIdx >= 0) mockState.requests[reqIdx] = { ...mockState.requests[reqIdx], status: 'COMPOSING', updated_at: now() };
      pushNotification('USER_OFFICE_001', 'WORKER_DECLINED', '배정 거절', `${worker.name}님이 배정을 거절했습니다. 빈 자리 재편성이 필요합니다.`);
    }

    // 거절한 worker는 다시 대기(READY)로 복귀 (다른 작업 배정 가능)
    mockState.workers[idx] = { ...worker, state: 'READY', current_offer: null, current_crew_id: null, state_changed_at: now(), updated_at: now() };

    return { success: true, data: mockState.workers[idx] };
  },

  'GET /worker/assignments': async () => {
    await delay(150);
    const userId = getCurrentUserId();
    const worker = mockState.workers.find((w) => w.user_id === userId);
    if (!worker || !worker.current_crew_id) return { success: true, data: [] };
    const crew = mockState.crews.find((c) => c.crew_id === worker.current_crew_id);
    if (!crew) return { success: true, data: [] };
    const request = mockState.requests.find((r) => r.request_id === crew.request_id);
    if (!request) return { success: true, data: [] };
    return { success: true, data: [{ crew_id: crew.crew_id, request_id: request.request_id, site_name: request.site_name, work_date: request.work_date, start_time: request.start_time, location_text: request.location_text, status: crew.status }] };
  },

  // 배차완료(RESERVED) 취소 — 작업 시작 24시간 전까지 (C-8)
  'POST /worker/reservation/cancel': async () => {
    await delay(200);
    const userId = getCurrentUserId();
    const idx = mockState.workers.findIndex((w) => w.user_id === userId);
    if (idx < 0) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: '근로자 정보를 찾을 수 없습니다.' } };
    const worker = mockState.workers[idx];
    if (worker.state !== 'RESERVED' || !worker.current_offer) {
      return { success: false, error: { code: 'STATE_CONFLICT', message: '배차완료(RESERVED) 상태에서만 취소할 수 있습니다.' } };
    }
    const offer = worker.current_offer;
    const startDt = new Date(`${offer.work_date}T${offer.start_time || '00:00'}:00`);
    if (!isNaN(startDt.getTime()) && startDt.getTime() - Date.now() < 24 * 60 * 60 * 1000) {
      return { success: false, error: { code: 'STATE_CONFLICT', message: '작업 시작 24시간 이내에는 취소할 수 없습니다.' } };
    }
    const crew = mockState.crews.find((c) => c.crew_id === offer.crew_id);
    if (crew) {
      recordDecline(crew.request_id, worker.worker_id);
      const mIdx = crew.members.findIndex((m) => m.worker_id === worker.worker_id);
      if (mIdx >= 0) crew.members[mIdx].acceptance = 'DECLINED';
      crew.updated_at = now();
      createGapEvent(crew, worker.worker_id, worker.name, 'UNAVAILABLE');
      const reqIdx = mockState.requests.findIndex((r) => r.request_id === crew.request_id);
      if (reqIdx >= 0) mockState.requests[reqIdx] = { ...mockState.requests[reqIdx], status: 'COMPOSING', updated_at: now() };
      pushNotification('USER_OFFICE_001', 'GAP_EVENT', '배차 취소', `${worker.name}님이 배차를 취소했습니다. 재편성이 필요합니다.`);
    }
    // READY 복귀 + 배차완료 카운트 -1 (24h 이전 취소는 배차완료에서 제외)
    mockState.workers[idx] = { ...worker, state: 'READY', current_offer: null, current_crew_id: null, dispatched_count: Math.max(0, (worker.dispatched_count || 0) - 1), state_changed_at: now(), updated_at: now() };
    return { success: true, data: mockState.workers[idx] };
  },

  // 내가 수락한 작업 이력 (C-12)
  'GET /worker/accepted-jobs': async () => {
    await delay(150);
    const userId = getCurrentUserId();
    const worker = mockState.workers.find((w) => w.user_id === userId);
    if (!worker) return { success: true, data: [] };
    const jobs = mockState.crews
      .map((crew) => {
        const m = crew.members.find((mm) => mm.worker_id === worker.worker_id && mm.acceptance === 'ACCEPTED');
        if (!m) return null;
        const req = mockState.requests.find((r) => r.request_id === crew.request_id);
        if (!req) return null;
        return {
          crew_id: crew.crew_id, request_id: req.request_id, site_name: req.site_name,
          work_date: req.work_date, start_time: req.start_time, location_text: req.location_text,
          assigned_trade: m.assigned_trade, offered_wage: m.offered_wage,
          status: crew.status, accepted_at: crew.updated_at,
        };
      })
      .filter((j) => j !== null);
    return { success: true, data: jobs };
  },

  // 출근일 히트맵 집계 (C-13)
  'GET /worker/attendance': async () => {
    await delay(120);
    const userId = getCurrentUserId();
    const worker = mockState.workers.find((w) => w.user_id === userId);
    if (!worker) return { success: true, data: {} };
    const counts: Record<string, number> = {};
    for (const h of worker.work_history) {
      if (h.work_date) counts[h.work_date] = (counts[h.work_date] || 0) + 1;
    }
    return { success: true, data: counts };
  },

  // 경력 연차별 평균 희망 일당 (C-10) — 직종 무관
  'GET /worker/wage-stats/{careerYears}': async (_body, careerYears?: string) => {
    await delay(100);
    const years = Number(careerYears);
    if (Number.isNaN(years)) return { success: false, error: { code: 'VALIDATION_ERROR', message: 'career_years(정수)가 필요합니다.' } };
    const wages = mockState.workers
      .filter((w) => w.career_years === years && w.desired_daily_wage > 0)
      .map((w) => w.desired_daily_wage);
    if (wages.length === 0) return { success: true, data: { career_years: years, average_wage: null, sample_count: 0 } };
    const avg = Math.round(wages.reduce((s, x) => s + x, 0) / wages.length);
    return { success: true, data: { career_years: years, average_wage: avg, sample_count: wages.length } };
  },

  // === 공통: 인력사무소 목록 ===
  'GET /offices': async () => {
    await delay(100);
    return { success: true, data: SEED_OFFICES };
  },

  // === 건설사 API ===
  'POST /company/requests': async (body) => {
    await delay(300);
    const userId = getCurrentUserId();
    const payload = body as CreateWorkRequestPayload;
    const newRequest: WorkRequest = {
      request_id: `REQ${String(mockState.requests.length + 1).padStart(3, '0')}`,
      company_id: userId!,
      office_id: payload.office_id,
      site_name: payload.site_name,
      work_date: payload.work_date,
      start_time: payload.start_time,
      location_text: payload.location_text,
      required_workers: payload.required_workers,
      budget: payload.budget,
      priority: payload.priority,
      notes: payload.notes,
      status: 'REQUESTED',
      created_at: now(),
      updated_at: now(),
    };
    mockState.requests.push(newRequest);
    return { success: true, data: newRequest };
  },

  'GET /company/requests': async () => {
    await delay(150);
    const userId = getCurrentUserId();
    return { success: true, data: mockState.requests.filter((r) => r.company_id === userId) };
  },

  'GET /company/requests/{id}': async (_body, requestId?: string) => {
    await delay(150);
    const request = mockState.requests.find((r) => r.request_id === requestId);
    if (!request) return { success: false, error: { code: 'REQUEST_NOT_FOUND', message: '요청을 찾을 수 없습니다.' } };
    const crew = findActiveCrew(request.request_id);
    // member에 실시간 worker_state + 신규 투입 여부 추가
    const crewWithState = crew ? {
      ...crew,
      members: crew.members.map((m) => {
        const w = mockState.workers.find((x) => x.worker_id === m.worker_id);
        return { ...m, worker_state: w?.state || 'INACTIVE' };
      }),
    } : null;
    // 이 요청의 활성/최근 GapEvent
    const gaps = mockState.gapEvents.filter((g) => g.request_id === requestId);
    const activeGap = gaps.length > 0 ? gaps[gaps.length - 1] : null;
    return { success: true, data: { ...request, crew: crewWithState, activeGap } };
  },

  // 출근 처리 (company가 호출)
  'POST /company/crews/{crewId}/checkin/{workerId}': async (_body, _crewId?: string) => {
    await delay(200);
    // crewId에서 실제로는 crewId/checkin/workerId 형태로 올 수 있지만 단순화
    // body로 worker_id 전달
    const { worker_id } = (_body || {}) as { worker_id: string };
    const wIdx = mockState.workers.findIndex((w) => w.worker_id === worker_id);
    if (wIdx < 0) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: '근로자를 찾을 수 없습니다.' } };
    const worker = mockState.workers[wIdx];
    if (worker.state !== 'RESERVED') return { success: false, error: { code: 'STATE_CONFLICT', message: '출근 처리는 배차완료(RESERVED) 상태에서만 가능합니다.' } };
    // 출근(체크인) 수 +1
    mockState.workers[wIdx] = { ...worker, state: 'RUNNING', attended_count: (worker.attended_count || 0) + 1, state_changed_at: now(), updated_at: now() };
    // crew도 RUNNING으로 변경 (전원 RUNNING 시)
    const crew = mockState.crews.find((c) => c.crew_id === worker.current_crew_id);
    if (crew) {
      const allRunning = crew.member_ids.every((id) => {
        const w = mockState.workers.find((x) => x.worker_id === id);
        return w && w.state === 'RUNNING';
      });
      if (allRunning) { crew.status = 'RUNNING'; crew.updated_at = now(); }
    }
    return { success: true, data: mockState.workers[wIdx] };
  },

  // 퇴근 처리 (company가 호출, body.rating: 1~5 별점 선택)
  'POST /company/crews/{crewId}/checkout/{workerId}': async (_body) => {
    await delay(200);
    const { worker_id, rating } = (_body || {}) as { worker_id: string; rating?: number };
    const wIdx = mockState.workers.findIndex((w) => w.worker_id === worker_id);
    if (wIdx < 0) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: '근로자를 찾을 수 없습니다.' } };
    const worker = mockState.workers[wIdx];
    if (worker.state !== 'RUNNING') return { success: false, error: { code: 'STATE_CONFLICT', message: '퇴근 처리는 작업중(RUNNING) 상태에서만 가능합니다.' } };
    const crewIdBeforeCheckout = worker.current_crew_id;
    const crewForHistory = mockState.crews.find((c) => c.crew_id === crewIdBeforeCheckout);
    const reqForHistory = crewForHistory ? mockState.requests.find((r) => r.request_id === crewForHistory.request_id) : null;
    const memberForHistory = crewForHistory?.members.find((m) => m.worker_id === worker_id);
    const historyEntry = reqForHistory && memberForHistory ? {
      crew_id: crewIdBeforeCheckout!,
      request_id: reqForHistory.request_id,
      site_name: reqForHistory.site_name,
      work_date: reqForHistory.work_date,
      assigned_trade: memberForHistory.assigned_trade,
      offered_wage: memberForHistory.offered_wage,
      completed_at: now(),
    } : null;
    // 별점(1~5) 반영: 평균/개수 갱신
    let ratingCount = worker.rating_count || 0;
    let ratingAvg = worker.rating ?? null;
    if (typeof rating === 'number' && rating >= 1 && rating <= 5) {
      const newCount = ratingCount + 1;
      ratingAvg = Math.round(((ratingAvg ?? 0) * ratingCount + rating) / newCount * 10) / 10;
      ratingCount = newCount;
    }
    mockState.workers[wIdx] = { ...worker, state: 'INACTIVE', current_crew_id: null, current_offer: null, completed_count: worker.completed_count + 1, rating: ratingAvg, rating_count: ratingCount, work_history: historyEntry ? [...worker.work_history, historyEntry] : worker.work_history, state_changed_at: now(), updated_at: now() };
    // 전원 퇴근(INACTIVE) 시 crew→COMPLETED, request→COMPLETED
    const crew = mockState.crews.find((c) => c.crew_id === crewIdBeforeCheckout);
    if (crew) {
      const allDone = crew.member_ids.every((id) => {
        if (id === worker_id) return true; // 방금 퇴근한 worker
        const w = mockState.workers.find((x) => x.worker_id === id);
        return w && w.state === 'INACTIVE';
      });
      if (allDone) {
        crew.status = 'COMPLETED'; crew.updated_at = now();
        const reqIdx = mockState.requests.findIndex((r) => r.request_id === crew.request_id);
        if (reqIdx >= 0) { mockState.requests[reqIdx].status = 'COMPLETED'; mockState.requests[reqIdx].updated_at = now(); }
      }
    }
    return { success: true, data: mockState.workers[wIdx] };
  },

  // === 사무소 API ===
  'GET /office/workers': async () => {
    await delay(150);
    return { success: true, data: mockState.workers.filter((w) => w.office_id === 'OFFICE001') };
  },

  'GET /office/requests': async () => {
    await delay(150);
    const items = mockState.requests
      .filter((r) => r.office_id === 'OFFICE001')
      .map((r) => ({ ...r, company_name: companyName(r.company_id) }));
    return { success: true, data: items };
  },

  'GET /office/requests/{id}': async (_body, requestId?: string) => {
    await delay(150);
    const request = mockState.requests.find((r) => r.request_id === requestId);
    if (!request) return { success: false, error: { code: 'REQUEST_NOT_FOUND', message: '요청을 찾을 수 없습니다.' } };
    const crew = findActiveCrew(request.request_id);
    const crewWithState = crew ? {
      ...crew,
      members: crew.members.map((m) => {
        const w = mockState.workers.find((x) => x.worker_id === m.worker_id);
        return { ...m, worker_state: w?.state || 'INACTIVE' };
      }),
    } : null;
    return { success: true, data: { ...request, company_name: companyName(request.company_id), crew: crewWithState } };
  },

  // office가 요청 거절
  'POST /office/requests/{requestId}/reject': async (body, requestId?: string) => {
    await delay(200);
    const { reason } = (body || {}) as { reason: string };
    const reqIdx = mockState.requests.findIndex((r) => r.request_id === requestId);
    if (reqIdx < 0) return { success: false, error: { code: 'REQUEST_NOT_FOUND', message: '요청을 찾을 수 없습니다.' } };
    const req = mockState.requests[reqIdx];
    if (req.status !== 'REQUESTED') return { success: false, error: { code: 'STATE_CONFLICT', message: '이미 처리된 요청입니다.' } };
    mockState.requests[reqIdx] = { ...req, status: 'REJECTED', rejection_reason: reason, updated_at: now() };
    pushNotification(req.company_id, 'REQUEST_REJECTED', '요청 거절', `"${req.site_name}" 요청이 거절되었습니다. 사유: ${reason}`);
    return { success: true, data: mockState.requests[reqIdx] };
  },

  // office가 무응답 worker 제안 취소
  // 취소된 worker만 INACTIVE + 거절 기록, 나머지 팀원은 READY로 복귀시켜 재편성 가능하게 함
  'POST /office/cancel-offer': async (body) => {
    await delay(200);
    const { worker_id } = (body || {}) as { worker_id: string };
    const wIdx = mockState.workers.findIndex((w) => w.worker_id === worker_id);
    if (wIdx < 0) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: '근로자를 찾을 수 없습니다.' } };
    const worker = mockState.workers[wIdx];
    if (worker.state !== 'NOTIFIED') return { success: false, error: { code: 'STATE_CONFLICT', message: '제안 취소는 NOTIFIED 상태에서만 가능합니다.' } };

    // 대상 crew 찾기
    const crew = mockState.crews.find((c) => c.crew_id === worker.current_crew_id || c.member_ids.includes(worker_id));

    if (crew) {
      // 거절 기록 (취소된 사람만)
      recordDecline(crew.request_id, worker_id);
      // 해당 멤버만 DECLINED 처리 (나머지 팀원은 그대로 유지 = 부분 재편성)
      const mIdx = crew.members.findIndex((m) => m.worker_id === worker_id);
      if (mIdx >= 0) crew.members[mIdx].acceptance = 'DECLINED';
      crew.updated_at = now();
      // 빈 자리(결원) 이벤트 생성 → AI/수동 재편성 경로 노출
      createGapEvent(crew, worker_id, worker.name, 'UNAVAILABLE');
      const reqIdx = mockState.requests.findIndex((r) => r.request_id === crew.request_id);
      if (reqIdx >= 0) mockState.requests[reqIdx] = { ...mockState.requests[reqIdx], status: 'COMPOSING', updated_at: now() };
      pushNotification('USER_OFFICE_001', 'GAP_EVENT', '빈 자리 발생', `${worker.name}님의 제안이 취소되어 빈 자리가 발생했습니다. 재편성이 필요합니다.`);
    }

    // 취소된 worker는 다시 대기(READY)로 복귀
    mockState.workers[wIdx] = { ...worker, state: 'READY', current_offer: null, current_crew_id: null, state_changed_at: now(), updated_at: now() };
    pushNotification(worker.user_id, 'OFFER_CANCELLED', '제안 취소', '배정 제안이 취소되었습니다.');
    return { success: true, data: mockState.workers[wIdx] };
  },

  // 빈 자리 채우기 (부분 재편성): 기존 팀원 유지, 거절/취소된 자리에 신규 인원 투입
  'POST /office/crews/{crewId}/fill-gap': async (body, crewId?: string) => {
    await delay(300);
    const { members: newMembers } = body as {
      members: { worker_id: string; assigned_trade: Trade; offered_wage: number }[];
    };
    const crew = mockState.crews.find((c) => c.crew_id === crewId);
    if (!crew) return { success: false, error: { code: 'CREW_INVALID', message: '작업조를 찾을 수 없습니다.' } };
    const request = mockState.requests.find((r) => r.request_id === crew.request_id);
    if (!request) return { success: false, error: { code: 'REQUEST_NOT_FOUND', message: '요청을 찾을 수 없습니다.' } };

    // 신규 인원 검증
    for (const mi of newMembers) {
      const w = mockState.workers.find((x) => x.worker_id === mi.worker_id);
      if (!w) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: `${mi.worker_id}를 찾을 수 없습니다.` } };
      if (w.state !== 'READY') return { success: false, error: { code: 'WORKER_NOT_READY', message: `${w.name}님은 READY 상태가 아닙니다.` } };
      if (w.excluded_trades.includes(mi.assigned_trade)) {
        return { success: false, error: { code: 'CREW_INVALID', message: `${w.name}님은 ${mi.assigned_trade} 직종을 희망하지 않습니다.` } };
      }
    }

    // 기존 멤버 중 DECLINED가 아닌 사람(fixed) 유지, DECLINED 제거
    const fixedMembers = crew.members.filter((m) => m.acceptance !== 'DECLINED');

    // 신규 멤버 생성 (NOTIFIED로 제안)
    const addedMembers: CrewMember[] = newMembers.map((mi) => {
      const w = mockState.workers.find((x) => x.worker_id === mi.worker_id)!;
      return { worker_id: w.worker_id, name: w.name, assigned_trade: mi.assigned_trade, career_years: w.career_years, offered_wage: mi.offered_wage, acceptance: 'PENDING' as const, is_replacement: true };
    });

    // 예산 검증 (fixed + added 총합)
    const totalCost = [...fixedMembers, ...addedMembers].reduce((s, m) => s + m.offered_wage, 0);
    if (request.budget > 0 && totalCost > request.budget) {
      return { success: false, error: { code: 'CREW_INVALID', message: '총예산을 초과합니다.' } };
    }

    // crew 갱신
    crew.members = [...fixedMembers, ...addedMembers];
    crew.member_ids = crew.members.map((m) => m.worker_id);
    crew.status = 'NOTIFIED';
    crew.updated_at = now();

    // 신규 인원만 NOTIFIED + offer 세팅
    for (const member of addedMembers) {
      const wi = mockState.workers.findIndex((x) => x.worker_id === member.worker_id);
      if (wi >= 0) {
        mockState.workers[wi] = {
          ...mockState.workers[wi],
          state: 'NOTIFIED',
          current_crew_id: crew.crew_id,
          current_offer: {
            crew_id: crew.crew_id,
            assigned_trade: member.assigned_trade,
            offered_wage: member.offered_wage,
            site_name: request.site_name,
            work_date: request.work_date,
            start_time: request.start_time,
            location_text: request.location_text,
          },
          state_changed_at: now(),
          updated_at: now(),
        };
        pushNotification(mockState.workers[wi].user_id, 'OFFER', '배정 제안', `${request.site_name}에 배정 제안이 도착했습니다. 확인 후 수락해주세요.`);
      }
    }

    return { success: true, data: crew };
  },

  // 편성 전체 취소 (빈 자리 채울 인원이 없을 때): crew 취소 + company/수락한 worker에게 알림
  'POST /office/crews/{crewId}/cancel-composition': async (_body, crewId?: string) => {
    await delay(300);
    const crew = mockState.crews.find((c) => c.crew_id === crewId);
    if (!crew) return { success: false, error: { code: 'CREW_INVALID', message: '작업조를 찾을 수 없습니다.' } };
    const request = mockState.requests.find((r) => r.request_id === crew.request_id);

    // 멤버 처리: 거절자 제외한 나머지(수락/응답대기)는 READY로 복귀 + 알림
    for (const member of crew.members) {
      if (member.acceptance === 'DECLINED') continue; // 거절자는 알림 X
      const wi = mockState.workers.findIndex((x) => x.worker_id === member.worker_id);
      if (wi < 0) continue;
      const w = mockState.workers[wi];
      // NOTIFIED/RESERVED 상태면 READY로 복귀
      if (w.state === 'NOTIFIED' || w.state === 'RESERVED') {
        mockState.workers[wi] = { ...w, state: 'READY', current_offer: null, current_crew_id: null, state_changed_at: now(), updated_at: now() };
      }
      // 수락/응답대기 worker에게 취소 알림 (거절자에겐 안 감)
      pushNotification(w.user_id, 'COMPOSITION_CANCELLED', '편성 취소',
        `${request?.site_name || '현장'} 작업조 편성이 취소되었습니다. 다시 대기 상태로 전환됩니다.`);
    }

    // crew 취소
    crew.status = 'CANCELLED';
    crew.updated_at = now();

    // 이 편성에 연결된 진행 중 결원 이벤트 종료(FAILED) — 댕글링 방지
    for (const g of mockState.gapEvents) {
      if (g.crew_id === crew.crew_id && ['DETECTED', 'RECOMPOSING', 'PROPOSED', 'APPROVED'].includes(g.status)) {
        g.status = 'FAILED';
        g.updated_at = now();
      }
    }

    // 요청 취소 + company에 취소 요청 알림
    if (request) {
      const reqIdx = mockState.requests.findIndex((r) => r.request_id === request.request_id);
      if (reqIdx >= 0) {
        mockState.requests[reqIdx] = { ...mockState.requests[reqIdx], status: 'CANCELLED', updated_at: now() };
      }
      pushNotification(request.company_id, 'REQUEST_CANCELLED', '편성 취소 요청',
        `"${request.site_name}" 요청의 인력 편성이 취소되었습니다. 인원 부족으로 작업조를 완성하지 못했습니다.`);
    }

    return { success: true, data: crew };
  },

  // worker 작업 이력 조회
  'GET /worker/history': async () => {
    await delay(150);
    const userId = getCurrentUserId();
    const worker = mockState.workers.find((w) => w.user_id === userId);
    if (!worker) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: '근로자 정보를 찾을 수 없습니다.' } };
    return { success: true, data: worker.work_history };
  },

  // 수동 편성 (새 플로우: assigned_trade + offered_wage 포함)
  'POST /office/crews/manual': async (body) => {
    await delay(300);
    const { request_id, members: memberInputs } = body as {
      request_id: string;
      members: { worker_id: string; assigned_trade: Trade; offered_wage: number }[];
    };

    const request = mockState.requests.find((r) => r.request_id === request_id);
    if (!request) return { success: false, error: { code: 'REQUEST_NOT_FOUND', message: '요청을 찾을 수 없습니다.' } };

    // 검증: 비희망 직종 배정 불가
    for (const mi of memberInputs) {
      const w = mockState.workers.find((x) => x.worker_id === mi.worker_id);
      if (!w) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: `${mi.worker_id}를 찾을 수 없습니다.` } };
      if (w.state !== 'READY') return { success: false, error: { code: 'WORKER_NOT_READY', message: `${w.name}님은 READY 상태가 아닙니다.` } };
      if (w.excluded_trades.includes(mi.assigned_trade)) {
        return { success: false, error: { code: 'CREW_INVALID', message: `${w.name}님은 ${mi.assigned_trade} 직종을 희망하지 않습니다.` } };
      }
    }

    // 직종별 인원 검증
    const tradeCount: Record<string, number> = {};
    for (const mi of memberInputs) { tradeCount[mi.assigned_trade] = (tradeCount[mi.assigned_trade] || 0) + 1; }
    for (const req of request.required_workers) {
      if ((tradeCount[req.trade] || 0) < req.count) {
        return { success: false, error: { code: 'CREW_INVALID', message: `${req.trade} 직종이 부족합니다.` } };
      }
    }

    const crewMembers: CrewMember[] = memberInputs.map((mi) => {
      const w = mockState.workers.find((x) => x.worker_id === mi.worker_id)!;
      return { worker_id: w.worker_id, name: w.name, assigned_trade: mi.assigned_trade, career_years: w.career_years, offered_wage: mi.offered_wage, acceptance: 'PENDING' };
    });

    // 기존 crew 정리 (재편성 시 옛 거절 crew 제거)
    cancelExistingCrews(request_id);

    const newCrew: Crew = {
      crew_id: `CREW${String(mockState.crews.length + 1).padStart(3, '0')}`,
      request_id,
      office_id: 'OFFICE001',
      status: 'DRAFT',
      source: 'MANUAL',
      member_ids: memberInputs.map((m) => m.worker_id),
      members: crewMembers,
      created_at: now(),
      updated_at: now(),
    };
    mockState.crews.push(newCrew);
    return { success: true, data: newCrew };
  },

  // AI 자동 편성 (mock: READY 후보에서 자동으로 3안 생성)
  'POST /office/requests/{requestId}/agent-compose': async (_body, requestId?: string) => {
    await delay(1500); // AI 호출 시뮬레이션
    const request = mockState.requests.find((r) => r.request_id === requestId);
    if (!request) return { success: false, error: { code: 'REQUEST_NOT_FOUND', message: '요청을 찾을 수 없습니다.' } };

    // 거절한 근로자는 후보에서 제외
    const declinedIds = request.declined_worker_ids || [];
    const readyCandidates = mockState.workers.filter((w) => w.state === 'READY' && w.office_id === 'OFFICE001' && !declinedIds.includes(w.worker_id));
    if (readyCandidates.length < request.required_workers.reduce((s, rw) => s + rw.count, 0)) {
      return { success: false, error: { code: 'AGENT_RETRY_FAILED', message: 'READY 상태 후보가 부족하여 AI 편성에 실패했습니다. 수동 편성으로 진행해주세요.' } };
    }

    // 간단한 mock 추천 생성: 후보를 셔플해서 3안 만들기
    const totalNeeded = request.required_workers.reduce((s, rw) => s + rw.count, 0);
    const recommendations: Recommendation[] = [];

    let rankCounter = 1;
    for (let attempt = 0; attempt < 5 && recommendations.length < 3; attempt++) {
      // 예산 준수를 위해 저렴한 후보 우선. attempt마다 약간의 변형을 줌
      const sorted = [...readyCandidates].sort((a, b) => {
        if (attempt === 0) return a.desired_daily_wage - b.desired_daily_wage; // 최저가 우선
        return (a.desired_daily_wage - b.desired_daily_wage) + (Math.random() - 0.5) * 40000; // 약간 섞기
      });
      const members: CrewMember[] = [];
      let costTotal = 0;

      for (const rw of request.required_workers) {
        let assigned = 0;
        for (const w of sorted) {
          if (assigned >= rw.count) break;
          if (members.some((m) => m.worker_id === w.worker_id)) continue;
          const at = rw.trade === 'ANY' ? assignAnyTrade(w) : rw.trade;
          if (!at || w.excluded_trades.includes(at)) continue;
          const wage = w.desired_daily_wage;
          members.push({ worker_id: w.worker_id, name: w.name, assigned_trade: at, career_years: w.career_years, offered_wage: wage, acceptance: 'PENDING' as const, notified_at: undefined });
          costTotal += wage;
          assigned++;
        }
      }

      // 인원 충족 + 예산 이내 + 중복 조합 아닌 것만 추가
      const withinBudget = request.budget <= 0 || costTotal <= request.budget;
      const isDuplicate = recommendations.some((r) => r.member_ids.slice().sort().join(',') === members.map((m) => m.worker_id).sort().join(','));
      if (members.length >= totalNeeded && withinBudget && !isDuplicate) {
        const reasons = ['필수 직종 구성 충족', '예산 범위 내', rankCounter === 1 ? '최저 비용 우선' : '경력 균형'];
        recommendations.push({
          rank: rankCounter,
          member_ids: members.map((m) => m.worker_id),
          members,
          total_cost: costTotal,
          reason: `${reasons.join(', ')} 기준으로 구성한 ${rankCounter}안입니다.`,
          considerations: reasons,
          fitness: computeFitness(members, request.budget, request.priority),
        });
        rankCounter++;
      }
    }

    if (recommendations.length === 0) {
      return { success: false, error: { code: 'AGENT_RETRY_FAILED', message: '예산 범위 내에서 가능한 조합을 찾지 못했습니다. 예산을 조정하거나 수동 편성으로 진행해주세요.' } };
    }

    // 기존 crew 정리 (재편성 시 옛 거절 crew 제거)
    cancelExistingCrews(requestId!);

    // Crew 저장 (PROPOSED, source=AGENT)
    const topRec = recommendations[0];
    const newCrew: Crew = {
      crew_id: `CREW${String(mockState.crews.length + 1).padStart(3, '0')}`,
      request_id: requestId!,
      office_id: 'OFFICE001',
      status: 'PROPOSED',
      source: 'AGENT',
      member_ids: topRec.member_ids,
      members: topRec.members,
      recommendations,
      created_at: now(),
      updated_at: now(),
    };
    mockState.crews.push(newCrew);

    // request → PROPOSED
    const reqIdx = mockState.requests.findIndex((r) => r.request_id === requestId);
    if (reqIdx >= 0) mockState.requests[reqIdx] = { ...mockState.requests[reqIdx], status: 'PROPOSED', updated_at: now() };

    return { success: true, data: newCrew };
  },

  // 노쇼 시뮬레이션 (company가 호출)
  'POST /company/crews/{crewId}/gap-events': async (body, crewId?: string) => {
    await delay(300);
    const { type, affected_worker_id } = (body || {}) as { type: string; affected_worker_id: string };
    const crew = mockState.crews.find((c) => c.crew_id === crewId);
    if (!crew) return { success: false, error: { code: 'CREW_INVALID', message: '작업조를 찾을 수 없습니다.' } };

    const wIdx = mockState.workers.findIndex((w) => w.worker_id === affected_worker_id);
    if (wIdx < 0) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: '근로자를 찾을 수 없습니다.' } };

    // worker → INACTIVE
    mockState.workers[wIdx] = { ...mockState.workers[wIdx], state: 'INACTIVE', current_crew_id: null, current_offer: null, no_show_count: mockState.workers[wIdx].no_show_count + 1, state_changed_at: now(), updated_at: now() };

    // GapEvent 생성
    const gapEvent = {
      event_id: `GAP${String(mockState.gapEvents.length + 1).padStart(3, '0')}`,
      crew_id: crewId!,
      request_id: crew.request_id,
      office_id: crew.office_id,
      type: type as 'NO_SHOW' | 'LEFT_SITE' | 'UNAVAILABLE' | 'DECLINED',
      affected_worker_id,
      affected_worker_name: mockState.workers[wIdx].name,
      status: 'DETECTED' as const,
      created_at: now(),
      updated_at: now(),
    };
    mockState.gapEvents.push(gapEvent);

    // crew member에서 해당 worker 상태 반영
    const mIdx = crew.members.findIndex((m) => m.worker_id === affected_worker_id);
    if (mIdx >= 0) crew.members[mIdx].acceptance = 'DECLINED';
    crew.updated_at = now();

    // 요청 상태 → COMPOSING (재편성 중)
    const reqIdxGap = mockState.requests.findIndex((r) => r.request_id === crew.request_id);
    if (reqIdxGap >= 0) mockState.requests[reqIdxGap] = { ...mockState.requests[reqIdxGap], status: 'COMPOSING', updated_at: now() };

    // 알림
    const typeLabel = type === 'LEFT_SITE' ? '작업 중 이탈' : type === 'NO_SHOW' ? '노쇼' : type;
    pushNotification('USER_OFFICE_001', 'GAP_EVENT', '결원 발생', `${mockState.workers[wIdx].name}님이 ${typeLabel}했습니다. 긴급 재편성이 필요합니다.`);

    return { success: true, data: gapEvent };
  },

  // GapEvent 목록 조회 (office)
  'GET /office/gap-events': async () => {
    await delay(120);
    const active = mockState.gapEvents.filter((g) => g.office_id === 'OFFICE001');
    return { success: true, data: active };
  },

  // GapEvent 단건 조회
  'GET /office/gap-events/{eventId}': async (_body, eventId?: string) => {
    await delay(120);
    const ev = mockState.gapEvents.find((g) => g.event_id === eventId);
    if (!ev) return { success: false, error: { code: 'GAP_EVENT_NOT_FOUND', message: '결원 이벤트를 찾을 수 없습니다.' } };
    return { success: true, data: ev };
  },

  // AI 긴급 재편성 (EMERGENCY): 잔여 팀원 고정, 빈 자리에 대체 인력 추천
  'POST /office/gap-events/{eventId}/agent-recompose': async (_body, eventId?: string) => {
    const evIdx = mockState.gapEvents.findIndex((g) => g.event_id === eventId);
    if (evIdx < 0) return { success: false, error: { code: 'GAP_EVENT_NOT_FOUND', message: '결원 이벤트를 찾을 수 없습니다.' } };
    const ev = mockState.gapEvents[evIdx];

    // RECOMPOSING 전이
    mockState.gapEvents[evIdx] = { ...ev, status: 'RECOMPOSING', updated_at: now() };
    await delay(1500); // AI 분석 시뮬레이션

    const crew = mockState.crews.find((c) => c.crew_id === ev.crew_id);
    const request = mockState.requests.find((r) => r.request_id === ev.request_id);
    if (!crew || !request) return { success: false, error: { code: 'CREW_INVALID', message: '작업조/요청을 찾을 수 없습니다.' } };

    // 고정 팀원 (거절 아닌 사람)
    const fixedMembers = crew.members.filter((m) => m.acceptance !== 'DECLINED');
    const fixedCost = fixedMembers.reduce((s, m) => s + m.offered_wage, 0);

    // 결원 직종 계산 (요구 - 고정 인원). 특정 직종을 먼저 소비하고, 남은 고정 인원으로 ANY를 흡수.
    const gapTrades: RequiredTrade[] = [];
    const fixedPool: Trade[] = fixedMembers.map((m) => m.assigned_trade);
    let anyNeeded = 0;
    for (const rw of request.required_workers) {
      if (rw.trade === 'ANY') { anyNeeded += rw.count; continue; }
      let have = 0;
      for (let i = 0; i < rw.count; i++) {
        const idx = fixedPool.indexOf(rw.trade);
        if (idx >= 0) { fixedPool.splice(idx, 1); have++; }
      }
      for (let i = 0; i < rw.count - have; i++) gapTrades.push(rw.trade);
    }
    const anyRemaining = Math.max(0, anyNeeded - fixedPool.length);
    for (let i = 0; i < anyRemaining; i++) gapTrades.push('ANY');

    // 대체 후보: READY + 거절 이력 없음 + 고정멤버 아님
    const declinedIds = request.declined_worker_ids || [];
    const fixedIds = fixedMembers.map((m) => m.worker_id);
    const candidates = mockState.workers.filter(
      (w) => w.state === 'READY' && w.office_id === 'OFFICE001' && !declinedIds.includes(w.worker_id) && !fixedIds.includes(w.worker_id)
    );

    // 대체 조합 추천 생성 (저렴한 순, 잔여 예산 이내)
    const remainingBudget = request.budget > 0 ? request.budget - fixedCost : 0;
    const recommendations: Recommendation[] = [];
    let rankCounter = 1;
    for (let attempt = 0; attempt < 5 && recommendations.length < 3; attempt++) {
      const sorted = [...candidates].sort((a, b) =>
        attempt === 0 ? a.desired_daily_wage - b.desired_daily_wage
                      : (a.desired_daily_wage - b.desired_daily_wage) + (Math.random() - 0.5) * 40000
      );
      const picked: CrewMember[] = [];
      let cost = 0;
      for (const trade of gapTrades) {
        for (const w of sorted) {
          if (picked.some((p) => p.worker_id === w.worker_id)) continue;
          const at = trade === 'ANY' ? assignAnyTrade(w) : trade;
          if (!at || w.excluded_trades.includes(at)) continue;
          picked.push({ worker_id: w.worker_id, name: w.name, assigned_trade: at, career_years: w.career_years, offered_wage: w.desired_daily_wage, acceptance: 'PENDING' });
          cost += w.desired_daily_wage;
          break;
        }
      }
      const withinBudget = remainingBudget <= 0 || cost <= remainingBudget;
      const dup = recommendations.some((r) => r.member_ids.slice().sort().join(',') === picked.map((m) => m.worker_id).sort().join(','));
      if (picked.length >= gapTrades.length && withinBudget && !dup) {
        recommendations.push({
          rank: rankCounter,
          member_ids: picked.map((m) => m.worker_id),
          members: picked,
          total_cost: cost,
          reason: `잔여 팀원과의 협업 및 예산(${remainingBudget > 0 ? remainingBudget.toLocaleString() + '원 이내' : '제한 없음'})을 고려한 긴급 대체 ${rankCounter}안입니다.`,
          considerations: ['잔여 팀원 유지', '결원 직종 충족', rankCounter === 1 ? '최저 비용' : '경력 균형'],
          fitness: computeFitness(picked, remainingBudget, request.priority),
        });
        rankCounter++;
      }
    }

    if (recommendations.length === 0) {
      // 실패 → FAILED
      mockState.gapEvents[evIdx] = { ...mockState.gapEvents[evIdx], status: 'FAILED', updated_at: now() };
      return { success: false, error: { code: 'AGENT_RETRY_FAILED', message: '대체 가능한 인력을 찾지 못했습니다. 수동 편성 또는 편성 취소가 필요합니다.' } };
    }

    // PROPOSED 전이 + 추천 저장
    mockState.gapEvents[evIdx] = { ...mockState.gapEvents[evIdx], status: 'PROPOSED', recommendations, updated_at: now() };
    return { success: true, data: mockState.gapEvents[evIdx] };
  },

  // 긴급 재편성 승인: 선택한 대체 조합을 crew에 투입 (기존 팀원 유지)
  'POST /office/emergency/{eventId}/approve': async (body, eventId?: string) => {
    await delay(300);
    const evIdx = mockState.gapEvents.findIndex((g) => g.event_id === eventId);
    if (evIdx < 0) return { success: false, error: { code: 'GAP_EVENT_NOT_FOUND', message: '결원 이벤트를 찾을 수 없습니다.' } };
    const ev = mockState.gapEvents[evIdx];
    const { members: replacementInputs } = body as { members: { worker_id: string; assigned_trade: Trade; offered_wage: number }[] };

    const crew = mockState.crews.find((c) => c.crew_id === ev.crew_id);
    const request = mockState.requests.find((r) => r.request_id === ev.request_id);
    if (!crew || !request) return { success: false, error: { code: 'CREW_INVALID', message: '작업조/요청을 찾을 수 없습니다.' } };

    // 대체 인력 검증
    for (const mi of replacementInputs) {
      const w = mockState.workers.find((x) => x.worker_id === mi.worker_id);
      if (!w) return { success: false, error: { code: 'WORKER_NOT_FOUND', message: `${mi.worker_id}를 찾을 수 없습니다.` } };
      if (w.state !== 'READY') return { success: false, error: { code: 'WORKER_NOT_READY', message: `${w.name}님은 READY 상태가 아닙니다.` } };
      if (w.excluded_trades.includes(mi.assigned_trade)) return { success: false, error: { code: 'CREW_INVALID', message: `${w.name}님은 ${mi.assigned_trade} 직종을 희망하지 않습니다.` } };
    }

    // 고정 팀원 유지, 거절자 제거, 대체 인력 추가(NOTIFIED)
    const fixedMembers = crew.members.filter((m) => m.acceptance !== 'DECLINED');
    const addedMembers: CrewMember[] = replacementInputs.map((mi) => {
      const w = mockState.workers.find((x) => x.worker_id === mi.worker_id)!;
      return { worker_id: w.worker_id, name: w.name, assigned_trade: mi.assigned_trade, career_years: w.career_years, offered_wage: mi.offered_wage, acceptance: 'PENDING' as const, is_replacement: true };
    });

    crew.members = [...fixedMembers, ...addedMembers];
    crew.member_ids = crew.members.map((m) => m.worker_id);
    crew.updated_at = now();

    // 대체 인력에게만 제안 발송
    for (const member of addedMembers) {
      const wi = mockState.workers.findIndex((x) => x.worker_id === member.worker_id);
      if (wi >= 0) {
        mockState.workers[wi] = {
          ...mockState.workers[wi],
          state: 'NOTIFIED',
          current_crew_id: crew.crew_id,
          current_offer: {
            crew_id: crew.crew_id,
            assigned_trade: member.assigned_trade,
            offered_wage: member.offered_wage,
            site_name: request.site_name,
            work_date: request.work_date,
            start_time: request.start_time,
            location_text: request.location_text,
            is_emergency: true,
          },
          state_changed_at: now(),
          updated_at: now(),
        };
        pushNotification(mockState.workers[wi].user_id, 'EMERGENCY_OFFER', '긴급 배정 제안', `${request.site_name} 긴급 대체 인력 제안이 도착했습니다. 확인 후 수락해주세요.`);
      }
    }

    // GapEvent → APPROVED
    mockState.gapEvents[evIdx] = { ...ev, status: 'APPROVED', updated_at: now() };
    return { success: true, data: mockState.gapEvents[evIdx] };
  },

  // 승인 → NOTIFIED (새 플로우: worker에게 제안 전송)
  'POST /office/crews/{crewId}/approve': async (body, crewId?: string) => {
    await delay(400);
    const crewIdx = mockState.crews.findIndex((c) => c.crew_id === crewId);
    if (crewIdx < 0) return { success: false, error: { code: 'CREW_INVALID', message: '작업조를 찾을 수 없습니다.' } };
    const crew = mockState.crews[crewIdx];

    // AI 추천 중 선택한 안(rank)이 있으면 해당 조합으로 멤버 교체
    const { rank } = (body || {}) as { rank?: number };
    if (rank && crew.recommendations) {
      const chosen = crew.recommendations.find((r) => r.rank === rank);
      if (chosen) {
        crew.member_ids = chosen.member_ids;
        crew.members = chosen.members.map((m) => ({ ...m, acceptance: 'PENDING' as const }));
      }
    }

    // 전원 READY 재검증
    for (const memberId of crew.member_ids) {
      const w = mockState.workers.find((x) => x.worker_id === memberId);
      if (!w || w.state !== 'READY') {
        return { success: false, error: { code: 'STATE_CONFLICT', message: '일부 근로자가 이미 다른 작업에 배정되었습니다.' } };
      }
    }

    const request = mockState.requests.find((r) => r.request_id === crew.request_id);

    // worker 상태 → NOTIFIED + current_offer 세팅
    for (const member of crew.members) {
      const wIdx = mockState.workers.findIndex((x) => x.worker_id === member.worker_id);
      if (wIdx >= 0) {
        mockState.workers[wIdx] = {
          ...mockState.workers[wIdx],
          state: 'NOTIFIED',
          current_crew_id: crew.crew_id,
          current_offer: {
            crew_id: crew.crew_id,
            assigned_trade: member.assigned_trade,
            offered_wage: member.offered_wage,
            site_name: request?.site_name || '',
            work_date: request?.work_date || '',
            start_time: request?.start_time || '',
            location_text: request?.location_text || '',
          },
          state_changed_at: now(),
          updated_at: now(),
        };
        pushNotification(mockState.workers[wIdx].user_id, 'OFFER', '배정 제안', `${request?.site_name}에 배정 제안이 도착했습니다. 확인 후 수락해주세요.`);
      }
    }

    // crew → NOTIFIED, request → APPROVED
    mockState.crews[crewIdx] = { ...crew, status: 'NOTIFIED', updated_at: now() };
    if (request) {
      const reqIdx = mockState.requests.findIndex((r) => r.request_id === request.request_id);
      if (reqIdx >= 0) mockState.requests[reqIdx] = { ...mockState.requests[reqIdx], status: 'APPROVED', updated_at: now() };
    }

    return { success: true, data: mockState.crews[crewIdx] };
  },

  // === 공통 ===
  'GET /notifications': async () => {
    await delay(100);
    const userId = getCurrentUserId();
    return { success: true, data: mockState.notifications.filter((n) => n.user_id === userId) };
  },

  // 알림 읽음 처리
  'POST /notifications/read': async (body) => {
    await delay(50);
    const { ids } = (body || {}) as { ids: string[] };
    for (const n of mockState.notifications) {
      if (ids.includes(n.id)) n.read = true;
    }
    return { success: true, data: { updated: ids.length } };
  },
};

// === 헬퍼 ===
function delay(ms: number) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function now() { return new Date().toISOString(); }

const ALL_TRADES: Trade[] = ['FORMWORK', 'REBAR', 'MASONRY', 'MATERIAL_CARRY', 'GENERAL'];

// 직종 무관(ANY) 슬롯에 배정할 실제 직종을 고른다 (excluded 회피).
function assignAnyTrade(w: Worker): Trade | null {
  const excluded = w.excluded_trades;
  const pref = w.preferred_trades.find((t) => !excluded.includes(t));
  if (pref) return pref;
  if (!excluded.includes('GENERAL')) return 'GENERAL';
  return ALL_TRADES.find((t) => !excluded.includes(t)) || null;
}

// company_id(=userId)로 건설사 이름 조회 (office 화면 표시용, C-14).
function companyName(companyId: string | undefined): string {
  if (!companyId) return '';
  for (const acc of Object.values(SEED_ACCOUNTS)) {
    if (acc.user.userId === companyId) return acc.user.name;
  }
  return companyId;
}

// 추천안 적합도(0~100) 계산 — 우선순위(비용/경력/팀워크) 가중. mock 데모용 근사.
function computeFitness(
  members: { offered_wage: number; career_years?: number }[],
  budget: number,
  priority: { cost: number; career: number; teamwork: number } | undefined,
): number {
  if (members.length === 0) return 0;
  const rank = priority || { cost: 2, career: 2, teamwork: 2 };
  const raw = { cost: 4 - rank.cost, career: 4 - rank.career, teamwork: 4 - rank.teamwork };
  const tot = raw.cost + raw.career + raw.teamwork || 1;
  const w = { cost: raw.cost / tot, career: raw.career / tot, teamwork: raw.teamwork / tot };
  const totalCost = members.reduce((s, m) => s + m.offered_wage, 0);
  const costS = budget > 0 ? Math.max(0, Math.min(1, 1 - totalCost / budget)) : 0.6;
  const avgCareer = members.reduce((s, m) => s + (m.career_years || 0), 0) / members.length;
  const careerS = Math.min(avgCareer / 15, 1);
  const teamS = 0; // mock: 협업 이력 미집계
  return Math.round((w.cost * costS + w.career * careerS + w.teamwork * teamS) * 100);
}

// 결원 이벤트 생성 (멤버 상실 시 AI/수동 재편성 경로를 열어준다).
function createGapEvent(crew: Crew, workerId: string, workerName: string, type: GapEvent['type']): GapEvent {
  const gapEvent: GapEvent = {
    event_id: `GAP${String(mockState.gapEvents.length + 1).padStart(3, '0')}`,
    crew_id: crew.crew_id,
    request_id: crew.request_id,
    office_id: crew.office_id,
    type,
    affected_worker_id: workerId,
    affected_worker_name: workerName,
    status: 'DETECTED',
    created_at: now(),
    updated_at: now(),
  };
  mockState.gapEvents.push(gapEvent);
  return gapEvent;
}

function applyApplicationFields(payload: WorkerApplicationRequest) {
  return {
    name: payload.name,
    phone: payload.phone,
    office_id: payload.office_id,
    preferred_trades: payload.preferred_trades,
    excluded_trades: payload.excluded_trades,
    career_years: payload.career_years,
    age: payload.age,
    region: payload.region,
    desired_daily_wage: payload.desired_daily_wage,
    certifications: payload.certifications,
  };
}

function pushNotification(userId: string, type: string, title: string, message: string) {
  mockState.notifications.push({
    id: `NOTI_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
    user_id: userId,
    type,
    title,
    message,
    read: false,
    created_at: now(),
  });
}

// 요청의 활성 crew 조회 (CANCELLED 제외, 가장 최근 것)
function findActiveCrew(requestId: string) {
  const active = mockState.crews.filter((c) => c.request_id === requestId && c.status !== 'CANCELLED');
  return active.length > 0 ? active[active.length - 1] : undefined;
}

// crew의 진행 중인 GapEvent 조회 (FILLED/FAILED 아닌 것 중 최신)
function findActiveGapEvent(crewId: string) {
  const active = mockState.gapEvents.filter((g) => g.crew_id === crewId && g.status !== 'FILLED' && g.status !== 'FAILED');
  return active.length > 0 ? active[active.length - 1] : undefined;
}

// 요청의 기존 crew들을 모두 CANCELLED 처리 (재편성 시)
function cancelExistingCrews(requestId: string) {
  for (const c of mockState.crews) {
    if (c.request_id === requestId && c.status !== 'CANCELLED' && c.status !== 'RUNNING' && c.status !== 'COMPLETED') {
      c.status = 'CANCELLED';
      c.updated_at = now();
    }
  }
}

// 요청에 거절/취소한 worker 기록
function recordDecline(requestId: string, workerId: string) {
  const reqIdx = mockState.requests.findIndex((r) => r.request_id === requestId);
  if (reqIdx < 0) return;
  const req = mockState.requests[reqIdx];
  const declined = req.declined_worker_ids || [];
  if (!declined.includes(workerId)) {
    mockState.requests[reqIdx] = { ...req, declined_worker_ids: [...declined, workerId], updated_at: now() };
  }
}
