import { useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { api } from '../../api/client';
import { usePolling } from '../../hooks/usePolling';
import type { WorkRequest, Crew, CrewMember, AcceptanceStatus, WorkerState, Recommendation, GapEvent } from '../../api/types';

const STATUS_LABEL: Record<string, string> = {
  REQUESTED: '요청 접수', COMPOSING: '재편성 중', PROPOSED: '추천 완료',
  APPROVED: '수락 대기', DISPATCHED: '배차 완료', RUNNING: '작업 중',
  COMPLETED: '완료', CANCELLED: '취소', NOTIFIED: '수락 대기', DRAFT: '임시', REJECTED: '거절됨',
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
const REJECT_REASONS = ['인원 부족', '해당 직종 근로자 부재', '일정 충돌', '기타'];
const OFFER_TIMEOUT_MS = 30 * 60 * 1000;

interface CrewMemberWithState extends CrewMember { worker_state: WorkerState; }
interface RequestDetail extends WorkRequest { crew: (Crew & { members: CrewMemberWithState[] }) | null; }

export default function OfficeRequestDetailPage() {
  const { requestId } = useParams<{ requestId: string }>();
  const navigate = useNavigate();
  const [rejecting, setRejecting] = useState(false);
  const [rejectReason, setRejectReason] = useState(REJECT_REASONS[0]);
  const [showRejectModal, setShowRejectModal] = useState(false);
  const [cancellingWorker, setCancellingWorker] = useState<string | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState('');
  const [approving, setApproving] = useState(false);
  const [selectedRank, setSelectedRank] = useState(0);

  const fetchDetail = useCallback(async () => {
    if (!requestId) return null;
    const res = await api.get<RequestDetail>(`/office/requests/${requestId}`);
    if (!res.success) return null;
    // 이 요청의 활성 GapEvent 조회
    const gapRes = await api.get<GapEvent[]>('/office/gap-events');
    let activeGap: GapEvent | null = null;
    if (gapRes.success) {
      const gaps = gapRes.data.filter((g) => g.request_id === requestId && g.status !== 'FILLED' && g.status !== 'FAILED');
      activeGap = gaps.length > 0 ? gaps[gaps.length - 1] : null;
    }
    return { ...res.data, activeGap };
  }, [requestId]);

  const { data: detail, refetch } = usePolling<(RequestDetail & { activeGap: GapEvent | null }) | null>({ fetchFn: fetchDetail, interval: 3000 });

  const handleReject = async () => {
    setRejecting(true);
    await api.post(`/office/requests/${requestId}/reject`, { reason: rejectReason });
    setRejecting(false);
    setShowRejectModal(false);
    refetch();
  };

  const handleCancelOffer = async (workerId: string, workerName: string) => {
    if (!confirm(`${workerName}님의 제안을 취소하시겠습니까?\n취소 시 해당 근로자는 다시 대기 상태가 되며, 빈 자리는 재편성이 필요합니다.`)) return;
    setCancellingWorker(workerId);
    const res = await api.post('/office/cancel-offer', { worker_id: workerId });
    setCancellingWorker(null);
    if (res.success) {
      toast.success(`${workerName}님의 제안을 취소했습니다.`);
      refetch();
    } else {
      toast.error(res.error.message);
    }
  };

  const [cancellingComposition, setCancellingComposition] = useState(false);
  const handleCancelComposition = async () => {
    if (!detail?.crew) return;
    if (!confirm('편성을 전체 취소하시겠습니까?\n건설사에 취소 요청이 전달되고, 수락한 근로자는 다시 대기 상태로 전환됩니다.')) return;
    setCancellingComposition(true);
    const res = await api.post<Crew>(`/office/crews/${detail.crew.crew_id}/cancel-composition`);
    setCancellingComposition(false);
    if (res.success) {
      toast.success('편성이 취소되었습니다. 건설사에 취소 요청을 전달했습니다.');
      refetch();
    } else {
      toast.error(res.error.message);
    }
  };

  const handleAiCompose = async () => {
    setAiLoading(true);
    setAiError('');
    const res = await api.post<Crew>(`/office/requests/${requestId}/agent-compose`);
    setAiLoading(false);
    if (res.success) {
      refetch();
    } else {
      setAiError(res.error.message);
    }
  };

  const handleApproveRecommendation = async (rec: Recommendation) => {
    if (!detail?.crew) return;
    setApproving(true);
    const crewId = detail.crew.crew_id;
    // 선택한 추천안(rank)을 전달하여 해당 조합으로 승인
    const res = await api.post<Crew>(`/office/crews/${crewId}/approve`, { rank: rec.rank });
    setApproving(false);
    if (res.success) {
      refetch();
    } else {
      toast.error(res.error.message);
    }
  };

  if (!detail) return <p className="text-center text-gray-400 py-10">불러오는 중...</p>;

  const canCompose = detail.status === 'REQUESTED';
  const hasDeclined = detail.crew?.members.some((m) => m.acceptance === 'DECLINED');
  const hasFixed = detail.crew?.members.some((m) => m.acceptance !== 'DECLINED');
  const isProposed = detail.status === 'PROPOSED' && detail.crew?.recommendations;
  // 노쇼로 인한 긴급 재편성 진행 중 (GapEvent 존재)
  const activeGap = detail.activeGap;
  const isEmergency = !!activeGap;
  // 빈 자리 채우기: 거절된 멤버 + 유지 멤버 (단, 노쇼 긴급건이 아닐 때)
  const needsGapFill = hasDeclined && hasFixed && !isEmergency;
  // 전체 재편성: 전원 거절 (유지할 멤버 없음, 긴급건 아님)
  const needsFullRecompose = hasDeclined && !hasFixed && !isEmergency;
  // 편성 완료(제안 발송/배차) 후에도 작업 시작 전이면 편성 취소 가능 (B-5).
  // 긴급/빈자리 재편성 중에도 "재편성 대신 아예 취소"를 선택할 수 있도록 함께 노출한다.
  const canCancelComposition = !!detail.crew
    && ['APPROVED', 'NOTIFIED', 'DISPATCHED'].includes(detail.crew.status)
    && detail.status !== 'RUNNING' && detail.status !== 'COMPLETED';

  const GAP_STEPS = ['DETECTED', 'RECOMPOSING', 'PROPOSED', 'APPROVED', 'FILLED'];
  const GAP_STEP_LABEL: Record<string, string> = {
    DETECTED: '결원 감지', RECOMPOSING: '재편성 중', PROPOSED: '대체 추천', APPROVED: '제안 발송', FILLED: '충원 완료',
  };

  return (
    <div className="max-w-3xl mx-auto space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-gray-800">{detail.site_name}</h2>
          {detail.company_name && <p className="text-sm text-gray-500 mt-0.5">🏢 {detail.company_name}</p>}
        </div>
        <button onClick={() => navigate('/office')} className="text-sm text-gray-500 hover:text-gray-800">← 목록으로</button>
      </div>

      {/* 상태 + 액션 */}
      <div className="bg-white rounded-lg border border-gray-200 p-5 flex items-center justify-between flex-wrap gap-2">
        <div>
          <span className="text-sm text-gray-500">상태: </span>
          <span className="font-medium text-gray-800">{STATUS_LABEL[detail.status] || detail.status}</span>
        </div>
        <div className="flex gap-2 flex-wrap">
          {isEmergency && activeGap && (activeGap.status === 'DETECTED' || activeGap.status === 'RECOMPOSING' || activeGap.status === 'PROPOSED') && (
            <button onClick={() => navigate(`/office/emergency/${activeGap.event_id}`)}
              className="bg-red-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-red-700 transition-colors">
              🚨 긴급 재편성
            </button>
          )}
          {canCompose && (
            <>
              <button onClick={handleAiCompose} disabled={aiLoading}
                className="bg-indigo-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors">
                {aiLoading ? '🤖 AI 분석 중...' : '🤖 AI 자동 편성'}
              </button>
              <button onClick={() => navigate(`/office/compose/${requestId}`)}
                className="bg-purple-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-purple-700 transition-colors">
                수동 편성
              </button>
              <button onClick={() => setShowRejectModal(true)}
                className="bg-white border border-red-300 text-red-600 px-4 py-2 rounded-md text-sm font-medium hover:bg-red-50 transition-colors">
                거절
              </button>
            </>
          )}
          {isProposed && (
            <button onClick={() => navigate(`/office/compose/${requestId}`)}
              className="bg-purple-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-purple-700 transition-colors">
              수동으로 편성하기
            </button>
          )}
          {needsGapFill && (
            <button onClick={() => navigate(`/office/compose/${requestId}`)}
              className="bg-purple-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-purple-700 transition-colors">
              빈 자리 채우기
            </button>
          )}
          {needsFullRecompose && (
            <>
              <button onClick={handleAiCompose} disabled={aiLoading}
                className="bg-indigo-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors">
                {aiLoading ? '🤖 AI 분석 중...' : '🤖 AI 재편성'}
              </button>
              <button onClick={() => navigate(`/office/compose/${requestId}`)}
                className="bg-purple-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-purple-700 transition-colors">
                수동 재편성
              </button>
            </>
          )}
          {canCancelComposition && (
            <button onClick={handleCancelComposition} disabled={cancellingComposition}
              className="bg-white border border-red-300 text-red-600 px-4 py-2 rounded-md text-sm font-medium hover:bg-red-50 disabled:opacity-50 transition-colors">
              {cancellingComposition ? '취소 중...' : '편성 취소'}
            </button>
          )}
        </div>
      </div>

      {/* 긴급 재편성 진행 상태 바 */}
      {isEmergency && activeGap && (
        <div className="bg-white rounded-lg border-2 border-red-200 p-5">
          <h3 className="text-sm font-medium text-red-700 mb-3">
            🚨 긴급 재편성 진행 — {activeGap.affected_worker_name}님 결원 ({activeGap.type})
          </h3>
          <div className="flex items-center gap-1">
            {GAP_STEPS.map((step) => {
              const curIdx = GAP_STEPS.indexOf(activeGap.status);
              const stepIdx = GAP_STEPS.indexOf(step);
              const isActive = stepIdx <= curIdx;
              const isCurrent = stepIdx === curIdx;
              return (
                <div key={step} className="flex-1 flex flex-col items-center">
                  <div className={`w-full h-2 rounded-full ${isActive ? 'bg-red-500' : 'bg-gray-200'} ${isCurrent ? 'animate-pulse' : ''}`} />
                  <span className={`text-[10px] mt-1 ${isActive ? 'text-red-600 font-medium' : 'text-gray-400'}`}>{GAP_STEP_LABEL[step]}</span>
                </div>
              );
            })}
          </div>
          {activeGap.status === 'FILLED' && (
            <p className="text-sm text-green-600 mt-3">✓ 대체 인력 충원이 완료되었습니다.</p>
          )}
        </div>
      )}

      {/* AI 에러 */}
      {aiError && (
        <div className="bg-yellow-50 border border-yellow-200 p-4 rounded-lg">
          <p className="text-yellow-800 font-medium text-sm">AI 편성 실패</p>
          <p className="text-yellow-700 text-sm mt-1">{aiError}</p>
          <button onClick={() => navigate(`/office/compose/${requestId}`)}
            className="mt-2 text-sm text-purple-600 hover:underline">수동 편성으로 진행 →</button>
        </div>
      )}

      {/* 거절 모달 */}
      {showRejectModal && (
        <div className="bg-red-50 border border-red-200 p-5 rounded-lg">
          <h3 className="text-sm font-medium text-red-700 mb-3">요청 거절 사유 선택</h3>
          <div className="space-y-2 mb-4">
            {REJECT_REASONS.map((reason) => (
              <label key={reason} className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                <input type="radio" name="rejectReason" value={reason} checked={rejectReason === reason}
                  onChange={(e) => setRejectReason(e.target.value)} className="text-red-600" />
                {reason}
              </label>
            ))}
          </div>
          <div className="flex gap-2">
            <button onClick={handleReject} disabled={rejecting}
              className="bg-red-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-red-700 disabled:opacity-50">
              {rejecting ? '처리 중...' : '거절 확정'}</button>
            <button onClick={() => setShowRejectModal(false)}
              className="px-4 py-2 border border-gray-300 text-gray-700 rounded-md text-sm hover:bg-gray-50">취소</button>
          </div>
        </div>
      )}

      {/* AI 추천 카드 (PROPOSED 상태일 때) */}
      {isProposed && detail.crew?.recommendations && (
        <div className="space-y-4">
          <h3 className="text-sm font-medium text-gray-700">🤖 AI 추천 ({detail.crew.recommendations.length}안)</h3>
          {detail.crew.recommendations.map((rec, idx) => (
            <div key={rec.rank}
              onClick={() => setSelectedRank(idx)}
              className={`bg-white rounded-lg border-2 p-5 cursor-pointer transition-all ${
                selectedRank === idx ? 'border-indigo-500 shadow-md' : 'border-gray-200 hover:border-indigo-300'
              }`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold text-indigo-700">AI 추천 {rec.rank}안</span>
                  {typeof rec.fitness === 'number' && (
                    <span className="text-xs font-medium bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded-full">
                      적합도 {rec.fitness}%
                    </span>
                  )}
                </div>
                <span className="text-sm font-medium text-gray-800">{rec.total_cost.toLocaleString()}원</span>
              </div>
              <div className="space-y-1.5 mb-3">
                {rec.members.map((m) => (
                  <div key={m.worker_id} className="flex items-center justify-between text-sm py-1 px-2 bg-indigo-50 rounded">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-gray-800">{m.name}</span>
                      <span className="text-xs text-gray-500">{TRADE_LABEL[m.assigned_trade]}</span>
                    </div>
                    <span className="text-xs text-gray-500">{m.offered_wage.toLocaleString()}원</span>
                  </div>
                ))}
              </div>
              <p className="text-sm text-gray-600 bg-gray-50 rounded p-2">{rec.reason}</p>
              {rec.considerations && (
                <div className="flex flex-wrap gap-1 mt-2">
                  {rec.considerations.map((c, i) => (
                    <span key={i} className="text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full">{c}</span>
                  ))}
                </div>
              )}
              {selectedRank === idx && (
                <button onClick={() => handleApproveRecommendation(rec)} disabled={approving}
                  className="mt-3 w-full bg-indigo-600 text-white py-2 rounded-md text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors">
                  {approving ? '승인 처리 중...' : `${rec.rank}안 승인`}
                </button>
              )}
            </div>
          ))}
          <button onClick={() => navigate(`/office/compose/${requestId}`)}
            className="w-full py-2 border border-purple-300 text-purple-600 rounded-md text-sm font-medium hover:bg-purple-50 transition-colors">
            AI 추천 무시하고 수동으로 편성하기
          </button>
        </div>
      )}

      {/* 배너들 */}
      {detail.status === 'REJECTED' && (
        <div className="bg-red-50 border border-red-200 p-4 rounded-lg text-center">
          <p className="text-red-700 font-medium">이 요청을 거절했습니다</p>
          {detail.rejection_reason && <p className="text-red-600 text-sm mt-1">사유: {detail.rejection_reason}</p>}
        </div>
      )}
      {hasDeclined && (
        <div className="bg-red-50 border border-red-200 p-4 rounded-lg">
          <p className="text-red-700 font-medium text-sm">⚠ 일부 근로자가 배정을 거절/취소되었습니다</p>
          <p className="text-red-600 text-sm mt-1">
            {needsGapFill ? '기존 팀원은 유지되며, 빈 자리만 새로 채우면 됩니다.' : '거절한 인원을 교체하여 재편성해주세요.'}
          </p>
        </div>
      )}
      {detail.status === 'COMPLETED' && (
        <div className="bg-gray-50 border border-gray-200 p-4 rounded-lg text-center">
          <p className="text-gray-700 font-medium">✓ 작업 완료</p>
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

      {/* 작업조 (PROPOSED가 아닌 상태일 때) */}
      {detail.crew && detail.crew.members.length > 0 && !isProposed && (
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-500 mb-3">
            작업조 <span className="text-xs text-gray-400">({STATUS_LABEL[detail.crew.status] || detail.crew.status})</span>
          </h3>
          <div className="space-y-2">
            {detail.crew.members.map((member: CrewMemberWithState) => {
              const accInfo = ACCEPTANCE_CONFIG[member.acceptance];
              const stateInfo = WORKER_STATE_BADGE[member.worker_state];
              const isPending = member.acceptance === 'PENDING';
              const isTimedOut = isPending && member.notified_at && (Date.now() - new Date(member.notified_at).getTime() > OFFER_TIMEOUT_MS);
              return (
                <div key={member.worker_id} className={`flex items-center justify-between text-sm py-2.5 px-3 rounded ${isPending ? 'bg-yellow-50' : 'bg-purple-50'}`}>
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-gray-800">{member.name}</span>
                    <span className="text-xs text-gray-500">{TRADE_LABEL[member.assigned_trade]}</span>
                    {member.acceptance !== 'ACCEPTED' && <span className={`text-xs px-1.5 py-0.5 rounded-full ${accInfo.color}`}>{accInfo.label}</span>}
                    {member.acceptance === 'ACCEPTED' && <span className={`text-xs px-1.5 py-0.5 rounded-full ${stateInfo.color}`}>{stateInfo.label}</span>}
                    {isTimedOut && <span className="text-xs text-red-500 font-medium">⏰ 타임아웃</span>}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-gray-400">{member.offered_wage.toLocaleString()}원</span>
                    {isPending && (
                      <button onClick={() => handleCancelOffer(member.worker_id, member.name)}
                        disabled={cancellingWorker === member.worker_id}
                        className="px-2 py-1 bg-white border border-red-300 text-red-600 text-xs rounded hover:bg-red-50 disabled:opacity-50">
                        {cancellingWorker === member.worker_id ? '...' : '제안 취소'}</button>
                    )}
                  </div>
                </div>
              );
            })}
            <div className="flex justify-between text-sm pt-2 border-t border-gray-200 font-medium">
              <span>예상 총 비용</span>
              <span>{detail.crew.members.reduce((s, m) => s + m.offered_wage, 0).toLocaleString()}원</span>
            </div>
          </div>
        </div>
      )}

      {detail.notes && (
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <h3 className="text-sm font-medium text-gray-500 mb-2">비고</h3>
          <p className="text-sm text-gray-700">{detail.notes}</p>
        </div>
      )}
    </div>
  );
}
