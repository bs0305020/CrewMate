import { useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { api } from '../../api/client';
import { usePolling } from '../../hooks/usePolling';
import type { WorkRequest, WorkRequestStatus, Crew, CrewMember, AcceptanceStatus, WorkerState, GapEvent } from '../../api/types';

const GAP_STEPS = ['DETECTED', 'RECOMPOSING', 'PROPOSED', 'APPROVED', 'FILLED'];
const GAP_STEP_LABEL: Record<string, string> = {
  DETECTED: '결원 감지', RECOMPOSING: '재편성 중', PROPOSED: '대체 추천', APPROVED: '제안 발송', FILLED: '충원 완료',
};

const STATUS_STEPS: WorkRequestStatus[] = ['REQUESTED', 'APPROVED', 'DISPATCHED', 'RUNNING', 'COMPLETED'];

const STATUS_LABEL: Record<string, string> = {
  REQUESTED: '요청됨', COMPOSING: '재편성 중', PROPOSED: '추천 완료',
  APPROVED: '수락 대기', DISPATCHED: '배차 완료', RUNNING: '작업 중',
  COMPLETED: '완료', CANCELLED: '취소',
};

const TRADE_LABEL: Record<string, string> = {
  FORMWORK: '🪵 형틀목공', REBAR: '🔩 철근공', MASONRY: '🧱 조적공',
  MATERIAL_CARRY: '📦 자재운반', GENERAL: '👷 보통인부', ANY: '🔀 직종 무관',
};

const PRIORITY_LABEL: Record<number, string> = { 1: '1순위', 2: '2순위', 3: '3순위' };

const ACCEPTANCE_CONFIG: Record<AcceptanceStatus, { label: string; color: string }> = {
  PENDING: { label: '응답 대기', color: 'bg-yellow-100 text-yellow-700' },
  ACCEPTED: { label: '수락', color: 'bg-green-100 text-green-700' },
  DECLINED: { label: '거절', color: 'bg-red-100 text-red-700' },
};

const WORKER_STATE_BADGE: Record<WorkerState, { label: string; color: string }> = {
  INACTIVE: { label: '퇴근 완료', color: 'bg-gray-100 text-gray-600' },
  READY: { label: '대기', color: 'bg-gray-100 text-gray-600' },
  NOTIFIED: { label: '제안 중', color: 'bg-purple-100 text-purple-600' },
  RESERVED: { label: '배차 완료', color: 'bg-blue-100 text-blue-700' },
  RUNNING: { label: '작업 중', color: 'bg-orange-100 text-orange-700' },
};

interface CrewMemberWithState extends CrewMember { worker_state: WorkerState; }
interface RequestDetail extends WorkRequest { crew: (Crew & { members: CrewMemberWithState[] }) | null; activeGap?: GapEvent | null; }

export default function RequestDetailPage() {
  const { requestId } = useParams<{ requestId: string }>();
  const navigate = useNavigate();
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchDetail = useCallback(async () => {
    if (!requestId) return null;
    const res = await api.get<RequestDetail>(`/company/requests/${requestId}`);
    if (res.success) return res.data;
    return null;
  }, [requestId]);

  const { data: detail, refetch } = usePolling<RequestDetail | null>({ fetchFn: fetchDetail, interval: 3000 });

  const handleCheckin = async (workerId: string, workerName: string) => {
    if (!confirm(`${workerName}님을 출근 처리하시겠습니까?`)) return;
    setActionLoading(workerId + '_in');
    const res = await api.post(`/company/crews/${detail?.crew?.crew_id}/checkin/${workerId}`, { worker_id: workerId });
    setActionLoading(null);
    if (!res.success) toast.error(res.error.message);
    refetch();
  };

  // 퇴근 처리 시 별점(1~5) 평가 모달
  const [checkoutTarget, setCheckoutTarget] = useState<{ workerId: string; workerName: string } | null>(null);
  const [stars, setStars] = useState(5);

  const handleCheckout = (workerId: string, workerName: string) => {
    setStars(5);
    setCheckoutTarget({ workerId, workerName });
  };

  const submitCheckout = async () => {
    if (!checkoutTarget) return;
    const { workerId } = checkoutTarget;
    setActionLoading(workerId + '_out');
    const res = await api.post(`/company/crews/${detail?.crew?.crew_id}/checkout/${workerId}`, { worker_id: workerId, rating: stars });
    setActionLoading(null);
    setCheckoutTarget(null);
    if (res.success) toast.success('퇴근 처리 완료. 평점이 반영되었습니다.');
    else toast.error(res.error.message);
    refetch();
  };

  const handleGapEvent = async (workerId: string, workerName: string, type: 'NO_SHOW' | 'LEFT_SITE') => {
    const label = type === 'NO_SHOW' ? '노쇼' : '작업 중 이탈';
    if (!confirm(`${workerName}님을 ${label} 처리하시겠습니까?\n긴급 재편성이 필요해집니다.`)) return;
    setActionLoading(workerId + '_gap');
    const res = await api.post(`/company/crews/${detail?.crew?.crew_id}/gap-events`, { type, affected_worker_id: workerId });
    setActionLoading(null);
    if (!res.success) toast.error(res.error.message);
    refetch();
  };

  if (!detail) return <p className="text-center text-gray-400 py-10">불러오는 중...</p>;

  const currentStepIdx = STATUS_STEPS.indexOf(detail.status);
  const showAttendance = detail.status === 'DISPATCHED' || detail.status === 'RUNNING';

  return (
    <div className="max-w-2xl mx-auto space-y-5">
      {/* 퇴근 별점 평가 모달 */}
      {checkoutTarget && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setCheckoutTarget(null)}>
          <div className="bg-white rounded-lg p-6 w-full max-w-sm" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-gray-800 mb-1">{checkoutTarget.workerName}님 퇴근 처리</h3>
            <p className="text-sm text-gray-500 mb-4">오늘 작업에 대한 평점을 남겨주세요. (퇴근 후 되돌릴 수 없습니다)</p>
            <div className="flex justify-center gap-1 mb-5">
              {[1, 2, 3, 4, 5].map((s) => (
                <button key={s} type="button" onClick={() => setStars(s)}
                  className={`text-3xl transition-transform hover:scale-110 ${s <= stars ? 'text-yellow-400' : 'text-gray-300'}`}
                  aria-label={`${s}점`}>★</button>
              ))}
            </div>
            <p className="text-center text-sm text-gray-600 mb-4">{stars}점</p>
            <div className="flex gap-2">
              <button type="button" onClick={submitCheckout}
                disabled={actionLoading === checkoutTarget.workerId + '_out'}
                className="flex-1 bg-orange-600 text-white py-2.5 rounded-md text-sm font-medium hover:bg-orange-700 disabled:opacity-50">
                {actionLoading === checkoutTarget.workerId + '_out' ? '처리 중...' : '퇴근 처리 + 평점 저장'}
              </button>
              <button type="button" onClick={() => setCheckoutTarget(null)}
                className="px-4 py-2.5 border border-gray-300 text-gray-700 rounded-md text-sm hover:bg-gray-50">취소</button>
            </div>
          </div>
        </div>
      )}
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-800">{detail.site_name}</h2>
        <button onClick={() => navigate('/company')} className="text-sm text-gray-500 hover:text-gray-800">← 목록으로</button>
      </div>

      {/* 상태 진행 표시 */}
      {detail.status !== 'CANCELLED' && (
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-500 mb-3">진행 상태</h3>
          <div className="flex items-center gap-1">
            {STATUS_STEPS.map((step, idx) => {
              const isActive = idx <= currentStepIdx;
              const isCurrent = idx === currentStepIdx;
              return (
                <div key={step} className="flex-1 flex flex-col items-center">
                  <div className={`w-full h-2 rounded-full ${isActive ? 'bg-orange-500' : 'bg-gray-200'} ${isCurrent ? 'animate-pulse' : ''}`} />
                  <span className={`text-[10px] mt-1 ${isActive ? 'text-orange-600 font-medium' : 'text-gray-400'}`}>{STATUS_LABEL[step]}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 배차 완료 배너 */}
      {detail.status === 'DISPATCHED' && (
        <div className="bg-green-50 border border-green-200 p-4 rounded-lg text-center">
          <p className="text-green-700 font-medium">✓ 전원 수락 완료 — 배차 확정</p>
          <p className="text-green-600 text-sm mt-1">출근 확인 버튼으로 출석을 처리해주세요.</p>
        </div>
      )}

      {/* 작업 완료 배너 */}
      {detail.status === 'COMPLETED' && (
        <div className="bg-gray-50 border border-gray-200 p-4 rounded-lg text-center">
          <p className="text-gray-700 font-medium">✓ 작업 완료</p>
          <p className="text-gray-500 text-sm mt-1">모든 인원이 퇴근 처리되었습니다.</p>
        </div>
      )}

      {/* 긴급 재편성 진행 상태 (노쇼 발생 시) */}
      {detail.activeGap && detail.activeGap.status !== 'FAILED' && (
        <div className={`bg-white rounded-lg border-2 p-5 ${detail.activeGap.status === 'FILLED' ? 'border-green-200' : 'border-red-200'}`}>
          <h3 className={`text-sm font-medium mb-3 ${detail.activeGap.status === 'FILLED' ? 'text-green-700' : 'text-red-700'}`}>
            {detail.activeGap.status === 'FILLED' ? '✓ 긴급 충원 완료' : '🚨 결원 발생 — 긴급 재편성 진행 중'}
            <span className="text-gray-500 font-normal"> ({detail.activeGap.affected_worker_name}님 {detail.activeGap.type})</span>
          </h3>
          <div className="flex items-center gap-1">
            {GAP_STEPS.map((step) => {
              const curIdx = GAP_STEPS.indexOf(detail.activeGap!.status);
              const stepIdx = GAP_STEPS.indexOf(step);
              const isActive = stepIdx <= curIdx;
              const isCurrent = stepIdx === curIdx;
              const barColor = detail.activeGap!.status === 'FILLED' ? 'bg-green-500' : 'bg-red-500';
              return (
                <div key={step} className="flex-1 flex flex-col items-center">
                  <div className={`w-full h-2 rounded-full ${isActive ? barColor : 'bg-gray-200'} ${isCurrent && detail.activeGap!.status !== 'FILLED' ? 'animate-pulse' : ''}`} />
                  <span className={`text-[10px] mt-1 ${isActive ? 'text-gray-700 font-medium' : 'text-gray-400'}`}>{GAP_STEP_LABEL[step]}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 요청 정보 */}
      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <h3 className="text-sm font-medium text-gray-500 mb-3">요청 정보</h3>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div><span className="text-gray-500">작업일</span><p className="font-medium text-gray-800">{detail.work_date}</p></div>
          <div><span className="text-gray-500">시작 시간</span><p className="font-medium text-gray-800">{detail.start_time}</p></div>
          <div className="col-span-2"><span className="text-gray-500">위치</span><p className="font-medium text-gray-800">{detail.location_text}</p></div>
          <div><span className="text-gray-500">총예산</span><p className="font-medium text-gray-800">{detail.budget.toLocaleString()}원</p></div>
          <div><span className="text-gray-500">우선순위</span><p className="font-medium text-gray-800 text-xs">비용 {PRIORITY_LABEL[detail.priority.cost]} / 경력 {PRIORITY_LABEL[detail.priority.career]} / 팀워크 {PRIORITY_LABEL[detail.priority.teamwork]}</p></div>
        </div>
      </div>

      {/* 작업조 + 출퇴근 관리 */}
      {detail.crew && detail.crew.members.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-500 mb-3">
            작업조 {showAttendance && <span className="text-xs text-orange-600 ml-2">출퇴근 관리</span>}
          </h3>
          <div className="space-y-2">
            {detail.crew.members.map((member: CrewMemberWithState) => {
              const accInfo = ACCEPTANCE_CONFIG[member.acceptance];
              const stateInfo = WORKER_STATE_BADGE[member.worker_state];
              const canCheckin = member.worker_state === 'RESERVED' && showAttendance;
              const canCheckout = member.worker_state === 'RUNNING' && showAttendance;

              return (
                <div key={member.worker_id} className={`flex items-center justify-between text-sm py-2.5 px-3 rounded ${member.is_replacement ? 'bg-green-50 ring-1 ring-green-300' : 'bg-orange-50'}`}>
                  <div className="flex items-center gap-2">
                    {member.is_replacement && (
                      <span className="text-xs bg-green-600 text-white px-1.5 py-0.5 rounded-full">신규 투입</span>
                    )}
                    <span className="font-medium text-gray-800">{member.name}</span>
                    <span className="text-xs text-gray-500">{TRADE_LABEL[member.assigned_trade]}</span>
                    {/* 수락 상태 (아직 수락 대기 중일 때) */}
                    {member.acceptance !== 'ACCEPTED' && (
                      <span className={`text-xs px-1.5 py-0.5 rounded-full ${accInfo.color}`}>{accInfo.label}</span>
                    )}
                    {/* worker 현재 상태 */}
                    {member.acceptance === 'ACCEPTED' && (
                      <span className={`text-xs px-1.5 py-0.5 rounded-full ${stateInfo.color}`}>{stateInfo.label}</span>
                    )}
                    {/* 긴급 대체 인력 예상 도착시간 */}
                    {member.eta && member.worker_state === 'RESERVED' && (
                      <span className="text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full">🕒 도착 {member.eta}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-gray-400">{member.offered_wage.toLocaleString()}원</span>
                    {canCheckin && (
                      <button onClick={() => handleCheckin(member.worker_id, member.name)}
                        disabled={actionLoading === member.worker_id + '_in'}
                        className="px-2.5 py-1 bg-green-600 text-white text-xs rounded hover:bg-green-700 disabled:opacity-50">
                        {actionLoading === member.worker_id + '_in' ? '...' : '출근'}
                      </button>
                    )}
                    {canCheckout && (
                      <button onClick={() => handleCheckout(member.worker_id, member.name)}
                        disabled={actionLoading === member.worker_id + '_out'}
                        className="px-2.5 py-1 bg-gray-600 text-white text-xs rounded hover:bg-gray-700 disabled:opacity-50">
                        {actionLoading === member.worker_id + '_out' ? '...' : '퇴근'}
                      </button>
                    )}
                    {/* 배차완료(출근 전) → 노쇼 */}
                    {canCheckin && (
                      <button onClick={() => handleGapEvent(member.worker_id, member.name, 'NO_SHOW')}
                        disabled={actionLoading === member.worker_id + '_gap'}
                        className="px-2.5 py-1 bg-red-600 text-white text-xs rounded hover:bg-red-700 disabled:opacity-50">
                        {actionLoading === member.worker_id + '_gap' ? '...' : '노쇼'}
                      </button>
                    )}
                    {/* 작업 중 → 이탈 */}
                    {canCheckout && (
                      <button onClick={() => handleGapEvent(member.worker_id, member.name, 'LEFT_SITE')}
                        disabled={actionLoading === member.worker_id + '_gap'}
                        className="px-2.5 py-1 bg-red-600 text-white text-xs rounded hover:bg-red-700 disabled:opacity-50">
                        {actionLoading === member.worker_id + '_gap' ? '...' : '이탈'}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
            <div className="flex justify-between text-sm pt-2 border-t border-gray-200 font-medium">
              <span>총 비용</span>
              <span>{detail.crew.members.reduce((s, m) => s + m.offered_wage, 0).toLocaleString()}원</span>
            </div>
          </div>
        </div>
      )}

      {/* 필요 인원 */}
      <div className="bg-white rounded-lg border border-gray-200 p-5">
        <h3 className="text-sm font-medium text-gray-500 mb-3">필요 인원</h3>
        <div className="space-y-2">
          {detail.required_workers.map((rw, idx) => (
            <div key={idx} className="flex items-center justify-between text-sm py-1.5 px-3 bg-gray-50 rounded">
              <span className="text-gray-700">{TRADE_LABEL[rw.trade] || rw.trade}</span>
              <span className="font-medium text-gray-800">{rw.count}명</span>
            </div>
          ))}
        </div>
      </div>

      {detail.notes && (
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-500 mb-2">비고</h3>
          <p className="text-sm text-gray-700">{detail.notes}</p>
        </div>
      )}
    </div>
  );
}
