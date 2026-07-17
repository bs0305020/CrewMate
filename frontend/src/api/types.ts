// === 공유 계약 기반 타입 정의 ===

// 역할
export type UserRole = 'WORKER' | 'OFFICE' | 'COMPANY';

// 근로자 상태
// INACTIVE → READY → NOTIFIED → RESERVED → RUNNING → INACTIVE
export type WorkerState = 'INACTIVE' | 'READY' | 'NOTIFIED' | 'RESERVED' | 'RUNNING';

// 수락 상태
export type AcceptanceStatus = 'PENDING' | 'ACCEPTED' | 'DECLINED';

// 요청 상태
export type WorkRequestStatus =
  | 'REQUESTED'
  | 'COMPOSING'
  | 'PROPOSED'
  | 'APPROVED'
  | 'DISPATCHED'
  | 'RUNNING'
  | 'COMPLETED'
  | 'REJECTED'
  | 'CANCELLED';

// 작업조 상태
export type CrewStatus =
  | 'DRAFT'
  | 'PROPOSED'
  | 'APPROVED'
  | 'NOTIFIED'
  | 'DISPATCHED'
  | 'RUNNING'
  | 'COMPLETED'
  | 'CANCELLED';

// 결원 이벤트 상태
export type GapEventStatus =
  | 'DETECTED'
  | 'RECOMPOSING'
  | 'PROPOSED'
  | 'APPROVED'
  | 'FILLED'
  | 'FAILED';

// 결원 유형
export type GapEventType = 'NO_SHOW' | 'LEFT_SITE' | 'UNAVAILABLE' | 'DECLINED';

// 직종
export type Trade =
  | 'FORMWORK'
  | 'REBAR'
  | 'MASONRY'
  | 'MATERIAL_CARRY'
  | 'GENERAL';

// 우선순위 순위 (1=최우선, 3=최하위). cost/career/teamwork에 1·2·3을 중복 없이 배정.
export type PriorityRank = 1 | 2 | 3;
export type PriorityAxis = 'cost' | 'career' | 'teamwork';

// === API 응답 형식 ===

export interface ApiSuccessResponse<T> {
  success: true;
  data: T;
}

export interface ApiErrorResponse {
  success: false;
  error: {
    code: string;
    message: string;
  };
}

export type ApiResponse<T> = ApiSuccessResponse<T> | ApiErrorResponse;

// === 엔터티 ===

export interface Office {
  office_id: string;
  name: string;
  region: string;
  worker_count: number;
  active: boolean; // 현재 요청 접수 가능 여부
}

export interface Worker {
  worker_id: string;
  user_id: string;
  name: string;
  phone: string;
  office_id: string;
  state: WorkerState;
  // 직종: 단일이 아닌 희망/비희망 복수 선택
  preferred_trades: Trade[];
  excluded_trades: Trade[];
  career_years: number;
  age: number;
  region: string;
  desired_daily_wage: number;
  certifications: string[];
  abilities?: string[];
  introduction?: string;
  completed_count: number;
  no_show_count: number;
  // 평점(5점 만점 평균)·출근 수·배차완료 수 (본인/사무소 응답에 노출)
  rating?: number | null;
  rating_count?: number;
  attended_count?: number;
  dispatched_count?: number;
  current_crew_id: string | null;
  // 현재 배정 제안 정보 (NOTIFIED 상태일 때)
  current_offer?: {
    crew_id: string;
    assigned_trade: Trade;
    offered_wage: number;
    site_name: string;
    work_date: string;
    start_time: string;
    location_text: string;
    is_emergency?: boolean; // 긴급 배차 제안 여부 (수락 시 예상 도착시간 선택)
  } | null;
  state_changed_at: string;
  created_at: string;
  updated_at: string;
  // 작업 이력
  work_history: WorkHistoryEntry[];
}

export interface WorkHistoryEntry {
  crew_id: string;
  request_id: string;
  site_name: string;
  work_date: string;
  start_time?: string;
  location_text?: string;
  company_name?: string;
  required_workers?: RequiredWorker[];
  notes?: string;
  assigned_trade: Trade;
  offered_wage: number;
  completed_at: string;
}

// 요청 직종: 실제 직종 + 직종 무관(ANY). 근로자 직종에는 ANY를 쓰지 않는다.
export type RequiredTrade = Trade | 'ANY';

export interface RequiredWorker {
  trade: RequiredTrade;
  count: number;
}

export interface Priority {
  cost: PriorityRank;
  career: PriorityRank;
  teamwork: PriorityRank;
}

export interface WorkRequest {
  request_id: string;
  company_id: string;
  office_id: string;
  site_name: string;
  work_date: string;
  start_time: string;
  location_text: string;
  required_workers: RequiredWorker[];
  budget: number;
  priority: Priority;
  notes: string;
  status: WorkRequestStatus;
  rejection_reason?: string;
  composition_error?: string;
  declined_worker_ids?: string[];
  company_name?: string; // office 화면에서 요청한 건설사 표시용
  created_at: string;
  updated_at: string;
}

// 근로자가 수락한 작업 이력 (대시보드)
export interface AcceptedJob {
  crew_id: string;
  request_id: string;
  site_name: string;
  work_date: string;
  start_time: string;
  location_text: string;
  assigned_trade: Trade;
  offered_wage: number;
  status: string;
  accepted_at: string;
}

export interface WorkerAssignment {
  crew_id: string;
  request_id: string;
  site_name: string;
  work_date: string;
  start_time: string;
  location_text: string;
  status: string;
  assigned_trade: Trade;
  offered_wage: number;
  acceptance: AcceptanceStatus;
  is_replacement: boolean;
  eta?: string | null;
  required_workers: RequiredWorker[];
  notes: string;
}

// 출근일 히트맵: work_date -> 출근 횟수
export type AttendanceMap = Record<string, number>;

// 경력 연차별 평균 희망 일당
export interface WageStats {
  career_years: number;
  average_wage: number | null;
  sample_count: number;
}

export interface CrewMember {
  worker_id: string;
  name: string;
  assigned_trade: Trade;
  career_years: number;
  offered_wage: number;
  acceptance: AcceptanceStatus;
  notified_at?: string;
  is_replacement?: boolean; // 긴급 재편성/빈자리 채우기로 신규 투입된 인원
  eta?: string; // 긴급 배차 대체 인력의 예상 도착시간
}

export interface Recommendation {
  rank: number;
  member_ids: string[];
  members: CrewMember[];
  total_cost: number;
  reason: string;
  considerations: string[];
  fitness?: number; // 적합도(0~100). AI 추천안의 종합 매치 점수.
}

export interface Crew {
  crew_id: string;
  request_id: string;
  office_id: string;
  status: CrewStatus;
  source: 'MANUAL' | 'AGENT';
  member_ids: string[];
  members: CrewMember[];
  recommendations?: Recommendation[];
  created_at: string;
  updated_at: string;
}

export interface GapEvent {
  event_id: string;
  crew_id: string;
  request_id: string;
  office_id: string;
  type: GapEventType;
  affected_worker_id: string;
  affected_worker_name?: string;
  status: GapEventStatus;
  recommendations?: Recommendation[];
  created_at: string;
  updated_at: string;
}

export interface Notification {
  id: string;
  user_id: string;
  type: string;
  title: string;
  message: string;
  read: boolean;
  created_at: string;
}

// === 인증 ===

export interface AuthUser {
  userId: string;
  role: UserRole;
  name: string;
  region?: string;
  token: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface SignupRequest {
  username: string;
  password: string;
  role: UserRole;
  name: string;
  region?: string; // 인력사무소 가입 시 활동 지역
}

export interface LoginResponse {
  user: AuthUser;
}

// === Worker API 요청 ===

export interface WorkerApplicationRequest {
  name: string;
  phone: string;
  office_id: string;
  preferred_trades: Trade[];
  excluded_trades: Trade[];
  career_years: number;
  age: number;
  region: string;
  desired_daily_wage: number;
  certifications: string[];
  abilities?: string[];
  introduction?: string;
}

export interface SpecReportRequest {
  targetTrade: string;
  targetSpecialty?: string;
  certifications: string[];
  abilities: string[];
  persistReport: boolean;
}

export interface SpecCertificationGroup {
  groupName: string;
  importance: string;
  selectionRule: string;
  certificationNames: string[];
  matchedCertifications: string[];
  satisfied: boolean;
}

export interface SpecAbilityDecision {
  abilityName: string;
  ncsCode: string;
  ncsSubcategory: string;
  matched: boolean;
  matchedInput?: string | null;
}

export interface SpecGapReport {
  reportId: string;
  targetTrade: string;
  targetSpecialty?: string | null;
  analysisScope: string;
  normalizedCertifications: Array<{
    inputName: string;
    normalizedName?: string | null;
    matched: boolean;
    qualificationStatus?: string | null;
    qnetUrl?: string | null;
  }>;
  satisfiedCertificationGroups: SpecCertificationGroup[];
  missingCoreCertificationGroups: SpecCertificationGroup[];
  recommendedCertificationGroups: SpecCertificationGroup[];
  abilityCoverage: { matched: number; required: number; percentage: number };
  matchedAbilities: SpecAbilityDecision[];
  missingAbilities: SpecAbilityDecision[];
  priorityActions: Array<{ priority: number; itemName: string; itemType: string; reason: string }>;
  limitations: string[];
  humanReviewItems: string[];
  generatedAt: string;
}

export interface SpecReportResponse {
  report: SpecGapReport;
  markdown?: string;
  persisted: boolean;
  status?: 'COMPLETED';
}

export interface SpecReportJobStart {
  reportId: string;
  status: 'PROCESSING';
}

export interface SpecReportJobState {
  reportId?: string;
  status: 'PROCESSING' | 'COMPLETED' | 'FAILED';
  report?: SpecGapReport;
  markdown?: string;
  persisted?: boolean;
  error?: { code: string; message: string };
}

export interface SpecReportJobSummary {
  reportId: string;
  targetTrade: string;
  status: 'PROCESSING' | 'COMPLETED' | 'FAILED';
  createdAt?: string;
  completedAt?: string;
  errorCode?: string;
}

// === Company API 요청 ===

export interface CreateWorkRequestPayload {
  office_id: string;
  site_name: string;
  work_date: string;
  start_time: string;
  location_text: string;
  required_workers: RequiredWorker[];
  budget: number;
  priority: Priority;
  notes: string;
}

export interface CreateGapEventPayload {
  type: GapEventType;
  affected_worker_id: string;
}
