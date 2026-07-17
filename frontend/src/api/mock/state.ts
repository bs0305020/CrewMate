import type {
  AuthUser,
  Worker,
  WorkRequest,
  Crew,
  GapEvent,
  Notification,
  Office,
} from '../types';
import { loadAuthSession } from '../../auth/session';

// 시드 인력사무소 목록 (GET /offices 로 제공)
export const SEED_OFFICES: Office[] = [
  { office_id: 'OFFICE001', name: '부산인력사무소', region: '부산 해운대구', worker_count: 6, active: true },
  { office_id: 'OFFICE002', name: '김해인력사무소', region: '경남 김해시', worker_count: 0, active: false },
];

// 시드 데모 계정 3종
export const SEED_ACCOUNTS: Record<string, { password: string; user: AuthUser }> = {
  worker1: {
    password: 'demo1234',
    user: {
      userId: 'USER_WORKER_001',
      role: 'WORKER',
      name: '김건우',
      token: 'mock-token-worker1',
    },
  },
  worker2: {
    password: 'demo1234',
    user: {
      userId: 'USER_WORKER_002',
      role: 'WORKER',
      name: '박철수',
      token: 'mock-token-worker2',
    },
  },
  worker3: {
    password: 'demo1234',
    user: {
      userId: 'USER_WORKER_003',
      role: 'WORKER',
      name: '이영희',
      token: 'mock-token-worker3',
    },
  },
  office1: {
    password: 'demo1234',
    user: {
      userId: 'USER_OFFICE_001',
      role: 'OFFICE',
      name: '부산인력사무소',
      token: 'mock-token-office1',
    },
  },
  company1: {
    password: 'demo1234',
    user: {
      userId: 'USER_COMPANY_001',
      role: 'COMPANY',
      name: '해운대건설',
      token: 'mock-token-company1',
    },
  },
};

// 회원가입으로 새 계정 등록 (mock — 메모리에만 저장, 새로고침 시 초기화)
let signupSeq = 1;
export function registerAccount(username: string, password: string, role: AuthUser['role'], name: string, region?: string): { ok: boolean; error?: string; user?: AuthUser } {
  if (SEED_ACCOUNTS[username]) {
    return { ok: false, error: '이미 사용 중인 아이디입니다.' };
  }
  const seq = signupSeq++;
  const userId = `USER_${role}_NEW_${seq}`;
  const user: AuthUser = { userId, role, name, region, token: `mock-token-${username}` };
  SEED_ACCOUNTS[username] = { password, user };

  // 인력사무소로 가입하면 사무소 목록에도 등록 → worker/company가 선택 가능
  if (role === 'OFFICE') {
    SEED_OFFICES.push({
      office_id: `OFFICE_NEW_${seq}`,
      name,
      region: region || '지역 미설정',
      worker_count: 0,
      active: true,
    });
  }
  return { ok: true, user };
}

// 메모리 상태
export interface MockState {
  workers: Worker[];
  requests: WorkRequest[];
  crews: Crew[];
  gapEvents: GapEvent[];
  notifications: Notification[];
}

// 시드 근로자 데이터 (새 필드 반영)
const SEED_WORKERS: Worker[] = [
  {
    worker_id: 'W001',
    user_id: 'USER_WORKER_001',
    name: '김건우',
    phone: '010-1234-5678',
    office_id: 'OFFICE001',
    state: 'INACTIVE',
    preferred_trades: ['FORMWORK', 'MASONRY'],
    excluded_trades: ['MATERIAL_CARRY'],
    career_years: 8,
    age: 35,
    region: '부산 해운대구',
    desired_daily_wage: 180000,
    certifications: ['건설기능사', '안전교육이수'],
    abilities: ['거푸집 설치', '시공 전 준비'],
    introduction: '안전수칙을 지키며 형틀과 조적 작업을 해왔습니다.',
    completed_count: 42,
    attended_count: 3,
    dispatched_count: 3,
    no_show_count: 0,
    current_crew_id: null,
    current_offer: null,
    work_history: [
      { crew_id: 'CREW_H01', request_id: 'REQ_H01', site_name: '해운대 주거현장', work_date: '2026-07-03', assigned_trade: 'FORMWORK', offered_wage: 180000, completed_at: '2026-07-03T17:00:00Z' },
      { crew_id: 'CREW_H02', request_id: 'REQ_H02', site_name: '센텀 업무시설', work_date: '2026-07-08', assigned_trade: 'MASONRY', offered_wage: 180000, completed_at: '2026-07-08T17:00:00Z' },
      { crew_id: 'CREW_H03', request_id: 'REQ_H03', site_name: '수영구 공동주택', work_date: '2026-07-14', assigned_trade: 'FORMWORK', offered_wage: 185000, completed_at: '2026-07-14T17:00:00Z' },
    ],
    state_changed_at: '2026-07-10T08:00:00Z',
    created_at: '2026-01-15T09:00:00Z',
    updated_at: '2026-07-10T08:00:00Z',
  },
  {
    worker_id: 'W002',
    user_id: 'USER_WORKER_002',
    name: '박철수',
    phone: '010-2345-6789',
    office_id: 'OFFICE001',
    state: 'READY',
    preferred_trades: ['REBAR', 'GENERAL'],
    excluded_trades: [],
    career_years: 5,
    age: 29,
    region: '부산 사하구',
    desired_daily_wage: 160000,
    certifications: ['건설기능사'],
    abilities: ['철근가공 조립검사'],
    introduction: '철근 작업을 중심으로 현장 경험을 쌓았습니다.',
    completed_count: 28,
    no_show_count: 1,
    current_crew_id: null,
    current_offer: null,
    work_history: [],
    state_changed_at: '2026-07-11T07:00:00Z',
    created_at: '2026-03-10T09:00:00Z',
    updated_at: '2026-07-11T07:00:00Z',
  },
  {
    worker_id: 'W003',
    user_id: 'USER_WORKER_003',
    name: '이영희',
    phone: '010-3456-7890',
    office_id: 'OFFICE001',
    state: 'READY',
    preferred_trades: ['GENERAL', 'FORMWORK'],
    excluded_trades: ['REBAR'],
    career_years: 10,
    age: 41,
    region: '부산 동래구',
    desired_daily_wage: 150000,
    certifications: ['안전교육이수'],
    abilities: ['조적미장시공 작업준비', '벽돌 쌓기 작업'],
    introduction: '조적과 일반 현장 업무가 가능합니다.',
    completed_count: 56,
    no_show_count: 0,
    current_crew_id: null,
    current_offer: null,
    work_history: [],
    state_changed_at: '2026-07-11T06:30:00Z',
    created_at: '2026-02-01T09:00:00Z',
    updated_at: '2026-07-11T06:30:00Z',
  },
  {
    worker_id: 'W004',
    user_id: 'USER_WORKER_004',
    name: '최민수',
    phone: '010-4567-8901',
    office_id: 'OFFICE001',
    state: 'READY',
    preferred_trades: ['MASONRY', 'FORMWORK', 'REBAR'],
    excluded_trades: [],
    career_years: 15,
    age: 48,
    region: '부산 수영구',
    desired_daily_wage: 200000,
    certifications: ['건설기능사', '특급기능사', '안전교육이수'],
    completed_count: 120,
    no_show_count: 0,
    current_crew_id: null,
    current_offer: null,
    work_history: [],
    state_changed_at: '2026-07-11T07:30:00Z',
    created_at: '2025-11-01T09:00:00Z',
    updated_at: '2026-07-11T07:30:00Z',
  },
  {
    worker_id: 'W005',
    user_id: 'USER_WORKER_005',
    name: '정대호',
    phone: '010-5678-9012',
    office_id: 'OFFICE001',
    state: 'READY',
    preferred_trades: ['FORMWORK', 'GENERAL'],
    excluded_trades: ['MASONRY'],
    career_years: 4,
    age: 27,
    region: '부산 남구',
    desired_daily_wage: 170000,
    certifications: ['건설기능사'],
    completed_count: 18,
    no_show_count: 2,
    current_crew_id: null,
    current_offer: null,
    work_history: [],
    state_changed_at: '2026-07-12T07:00:00Z',
    created_at: '2026-04-01T09:00:00Z',
    updated_at: '2026-07-12T07:00:00Z',
  },
  {
    worker_id: 'W006',
    user_id: 'USER_WORKER_006',
    name: '한승우',
    phone: '010-6789-0123',
    office_id: 'OFFICE001',
    state: 'READY',
    preferred_trades: ['MATERIAL_CARRY', 'GENERAL'],
    excluded_trades: ['FORMWORK'],
    career_years: 2,
    age: 24,
    region: '부산 해운대구',
    desired_daily_wage: 140000,
    certifications: ['안전교육이수'],
    completed_count: 10,
    no_show_count: 0,
    current_crew_id: null,
    current_offer: null,
    work_history: [],
    state_changed_at: '2026-07-11T08:00:00Z',
    created_at: '2026-05-20T09:00:00Z',
    updated_at: '2026-07-11T08:00:00Z',
  },
];

// 시드 요청 데이터 (없음 - 건설사가 직접 생성)
const SEED_REQUESTS: WorkRequest[] = [];

export const mockState: MockState = {
  workers: [...SEED_WORKERS],
  requests: [...SEED_REQUESTS],
  crews: [],
  gapEvents: [],
  notifications: [],
};

// 상태 리셋 (데모용)
export function resetMockState() {
  mockState.workers = [...SEED_WORKERS];
  mockState.requests = [...SEED_REQUESTS];
  mockState.crews = [];
  mockState.gapEvents = [];
  mockState.notifications = [];
}

// 유틸: 현재 로그인 유저 ID 추적
let currentUserId: string | null = null;
export function setCurrentUserId(id: string | null) {
  currentUserId = id;
}
export function getCurrentUserId(): string | null {
  return currentUserId || loadAuthSession()?.userId || null;
}
