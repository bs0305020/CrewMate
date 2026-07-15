import { useState, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { api } from '../../api/client';
import { usePolling } from '../../hooks/usePolling';
import { useAuth } from '../../auth/AuthContext';
import type { Worker, WorkerState } from '../../api/types';

const STATE_CONFIG: Record<WorkerState, { label: string; color: string; bgColor: string }> = {
  INACTIVE: { label: '비활성', color: 'text-gray-700', bgColor: 'bg-gray-100' },
  READY: { label: '대기 중', color: 'text-green-700', bgColor: 'bg-green-100' },
  NOTIFIED: { label: '배정 제안 도착', color: 'text-purple-700', bgColor: 'bg-purple-100' },
  RESERVED: { label: '배차 완료', color: 'text-blue-700', bgColor: 'bg-blue-100' },
  RUNNING: { label: '작업 중', color: 'text-orange-700', bgColor: 'bg-orange-100' },
};

const TRADE_LABEL: Record<string, string> = {
  FORMWORK: '🪵 형틀목공',
  REBAR: '🔩 철근공',
  MASONRY: '🧱 조적공',
  MATERIAL_CARRY: '📦 자재운반',
  GENERAL: '👷 보통인부',
};

export default function WorkerHomePage() {
  const navigate = useNavigate();
  const { updateName } = useAuth();
  const [actionLoading, setActionLoading] = useState(false);
  const [selectedEta, setSelectedEta] = useState('30분 이내');

  const fetchWorker = useCallback(async () => {
    const res = await api.get<Worker>('/worker/me');
    if (res.success) return res.data;
    return null;
  }, []);

  const { data: worker, refetch } = usePolling<Worker | null>({
    fetchFn: fetchWorker,
    interval: 5000,
  });

  useEffect(() => {
    if (worker?.name) updateName(worker.name);
  }, [worker?.name, updateName]);

  const handleReady = async () => {
    setActionLoading(true);
    const res = await api.post('/worker/state/ready');
    setActionLoading(false);
    if (res.success) refetch();
    else if (!res.success) toast.error(res.error.message);
  };

  const handleInactive = async () => {
    setActionLoading(true);
    const res = await api.post('/worker/state/inactive');
    setActionLoading(false);
    if (res.success) refetch();
    else if (!res.success) toast.error(res.error.message);
  };

  const handleAccept = async (eta?: string) => {
    setActionLoading(true);
    const res = await api.post('/worker/offer/accept', eta ? { eta } : undefined);
    setActionLoading(false);
    if (res.success) refetch();
    else if (!res.success) toast.error(res.error.message);
  };

  const handleDecline = async () => {
    if (!confirm('정말 거절하시겠습니까? 거절 시 다시 대기 상태로 돌아갑니다.')) return;
    setActionLoading(true);
    const res = await api.post('/worker/offer/decline');
    setActionLoading(false);
    if (res.success) refetch();
    else if (!res.success) toast.error(res.error.message);
  };

  const handleCancelReservation = async () => {
    if (!confirm('배차를 취소하시겠습니까?\n취소 시 다시 대기 상태로 전환되며, 인력사무소에 재편성이 요청됩니다.')) return;
    setActionLoading(true);
    const res = await api.post('/worker/reservation/cancel');
    setActionLoading(false);
    if (res.success) { toast.success('배차를 취소했습니다.'); refetch(); }
    else toast.error(res.error.message);
  };

  if (!worker) {
    return (
      <div className="max-w-lg mx-auto">
        <h2 className="text-xl font-semibold text-gray-800 mb-4">근로자 대시보드</h2>
        <div className="bg-white rounded-lg border border-gray-200 p-6 text-center">
          <p className="text-gray-500 mb-4">지원서를 먼저 작성해주세요.</p>
          <button onClick={() => navigate('/worker/application')}
            className="bg-green-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-green-700 transition-colors">
            지원서 작성하기
          </button>
        </div>
      </div>
    );
  }

  const stateInfo = STATE_CONFIG[worker.state];

  return (
    <div className="max-w-lg mx-auto space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-800">근로자 대시보드</h2>
        <button onClick={() => navigate('/worker/application')}
          className="text-sm text-gray-500 hover:text-gray-800 transition-colors">
          지원서 수정
        </button>
      </div>

      {/* 상태 카드 */}
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <div className="text-center mb-6">
          <p className="text-sm text-gray-500 mb-2">현재 상태</p>
          <span className={`inline-block text-2xl font-bold px-4 py-2 rounded-lg ${stateInfo.bgColor} ${stateInfo.color}`}>
            {stateInfo.label}
          </span>
        </div>

        {/* 상태별 액션 */}
        <div className="flex justify-center">
          {worker.state === 'INACTIVE' && (
            <button onClick={handleReady} disabled={actionLoading}
              className="bg-green-600 text-white px-6 py-2.5 rounded-md text-sm font-medium hover:bg-green-700 disabled:opacity-50 transition-colors">
              {actionLoading ? '처리 중...' : '대기 시작'}
            </button>
          )}
          {worker.state === 'READY' && (
            <button onClick={handleInactive} disabled={actionLoading}
              className="bg-gray-600 text-white px-6 py-2.5 rounded-md text-sm font-medium hover:bg-gray-700 disabled:opacity-50 transition-colors">
              {actionLoading ? '처리 중...' : '대기 취소'}
            </button>
          )}
          {worker.state === 'RESERVED' && (() => {
            const offer = worker.current_offer;
            const startMs = offer ? new Date(`${offer.work_date}T${offer.start_time || '00:00'}:00`).getTime() : NaN;
            const canCancel = !Number.isNaN(startMs) && startMs - Date.now() >= 24 * 60 * 60 * 1000;
            return (
              <div className="flex flex-col items-center gap-2">
                <p className="text-sm text-blue-600">배차 완료! 작업 시간에 현장으로 출근해주세요.</p>
                <button onClick={handleCancelReservation} disabled={actionLoading || !canCancel}
                  className="bg-white border border-red-300 text-red-600 px-5 py-2 rounded-md text-sm font-medium hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                  {actionLoading ? '처리 중...' : '배차 취소'}
                </button>
                {!canCancel && (
                  <p className="text-xs text-gray-400">작업 시작 24시간 이전에만 취소할 수 있습니다.</p>
                )}
              </div>
            );
          })()}
          {worker.state === 'RUNNING' && (
            <button onClick={() => navigate('/worker/assignments')}
              className="bg-orange-600 text-white px-6 py-2.5 rounded-md text-sm font-medium hover:bg-orange-700 transition-colors">
              배정 정보 보기
            </button>
          )}
        </div>
      </div>

      {/* NOTIFIED: 배정 제안 카드 */}
      {worker.state === 'NOTIFIED' && worker.current_offer && (
        <div className="bg-purple-50 rounded-lg border-2 border-purple-300 p-6">
          <h3 className="text-sm font-medium text-purple-700 mb-3">
            {worker.current_offer.is_emergency ? '🚨 긴급 배정 제안' : '📋 배정 제안'}
          </h3>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <span className="text-purple-500">현장</span>
                <p className="font-semibold text-gray-800">{worker.current_offer.site_name}</p>
              </div>
              <div>
                <span className="text-purple-500">배정 직종</span>
                <p className="font-semibold text-gray-800">{TRADE_LABEL[worker.current_offer.assigned_trade]}</p>
              </div>
              <div>
                <span className="text-purple-500">작업일</span>
                <p className="font-medium text-gray-800">{worker.current_offer.work_date}</p>
              </div>
              <div>
                <span className="text-purple-500">시작 시간</span>
                <p className="font-medium text-gray-800">{worker.current_offer.start_time}</p>
              </div>
              <div className="col-span-2">
                <span className="text-purple-500">위치</span>
                <p className="font-medium text-gray-800">{worker.current_offer.location_text}</p>
              </div>
            </div>

            <div className="bg-white rounded-lg p-3 text-center">
              <span className="text-sm text-gray-500">제안 일당</span>
              <p className="text-2xl font-bold text-purple-700">
                {worker.current_offer.offered_wage.toLocaleString()}원
              </p>
            </div>

            {/* 긴급 배차: 예상 도착시간 선택 */}
            {worker.current_offer.is_emergency && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-3">
                <p className="text-sm font-medium text-red-700 mb-2">🚨 긴급 배차 — 예상 도착시간을 선택해주세요</p>
                <select value={selectedEta} onChange={(e) => setSelectedEta(e.target.value)}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-red-400">
                  <option value="30분 이내">30분 이내</option>
                  <option value="1시간 이내">1시간 이내</option>
                  <option value="1시간 30분 이내">1시간 30분 이내</option>
                  <option value="2시간 이내">2시간 이내</option>
                </select>
              </div>
            )}

            <div className="flex gap-3 pt-2">
              <button onClick={() => handleAccept(worker.current_offer?.is_emergency ? selectedEta : undefined)} disabled={actionLoading}
                className="flex-1 bg-purple-600 text-white py-2.5 rounded-md text-sm font-medium hover:bg-purple-700 disabled:opacity-50 transition-colors">
                {actionLoading ? '처리 중...' : '수락'}
              </button>
              <button onClick={handleDecline} disabled={actionLoading}
                className="flex-1 bg-white border border-red-300 text-red-600 py-2.5 rounded-md text-sm font-medium hover:bg-red-50 disabled:opacity-50 transition-colors">
                {actionLoading ? '처리 중...' : '거절'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 프로필 요약 */}
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h3 className="text-sm font-medium text-gray-500 mb-3">내 정보</h3>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <span className="text-gray-500">이름</span>
            <p className="font-medium text-gray-800">{worker.name}</p>
          </div>
          <div>
            <span className="text-gray-500">경력</span>
            <p className="font-medium text-gray-800">{worker.career_years}년</p>
          </div>
          <div>
            <span className="text-gray-500">지역</span>
            <p className="font-medium text-gray-800">{worker.region}</p>
          </div>
          <div>
            <span className="text-gray-500">희망 일당</span>
            <p className="font-medium text-gray-800">{worker.desired_daily_wage.toLocaleString()}원</p>
          </div>
        </div>
        {worker.preferred_trades.length > 0 && (
          <div className="mt-3 pt-3 border-t border-gray-100">
            <span className="text-xs text-gray-500">희망 직종</span>
            <div className="flex flex-wrap gap-1 mt-1">
              {worker.preferred_trades.map((t) => (
                <span key={t} className="text-xs bg-green-50 text-green-700 px-2 py-0.5 rounded-full">
                  {TRADE_LABEL[t]}
                </span>
              ))}
            </div>
          </div>
        )}
        {worker.certifications.length > 0 && (
          <div className="mt-3 pt-3 border-t border-gray-100">
            <span className="text-xs text-gray-500">자격증</span>
            <div className="flex flex-wrap gap-1 mt-1">
              {worker.certifications.map((cert) => (
                <span key={cert} className="text-xs bg-green-50 text-green-700 px-2 py-0.5 rounded-full">{cert}</span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 실적 */}
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-gray-500">작업 실적</h3>
          <button onClick={() => navigate('/worker/history')}
            className="text-xs text-green-600 hover:underline">이력 보기 →</button>
        </div>
        <div className="grid grid-cols-4 gap-2 text-center">
          <div>
            <p className="text-2xl font-bold text-yellow-500">
              {worker.rating != null ? `★${worker.rating}` : '-'}
            </p>
            <p className="text-xs text-gray-500">평점</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-gray-800">{worker.attended_count ?? 0}</p>
            <p className="text-xs text-gray-500">출근</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-gray-800">{worker.dispatched_count ?? 0}</p>
            <p className="text-xs text-gray-500">배차완료</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-gray-800">{worker.completed_count}</p>
            <p className="text-xs text-gray-500">완료</p>
          </div>
        </div>
        <p className="text-[11px] text-gray-400 mt-2 text-center">배차완료는 작업 24시간 이전에 취소한 건은 제외됩니다.</p>
      </div>
    </div>
  );
}
