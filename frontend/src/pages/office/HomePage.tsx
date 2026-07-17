import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import { usePolling } from '../../hooks/usePolling';
import type { WorkRequest, WorkRequestStatus } from '../../api/types';

const STATUS_CONFIG: Record<WorkRequestStatus, { label: string; color: string }> = {
  REQUESTED: { label: '요청 접수', color: 'bg-yellow-100 text-yellow-700' },
  COMPOSING: { label: 'AI 편성 중', color: 'bg-indigo-100 text-indigo-700' },
  PROPOSED: { label: '추천 완료', color: 'bg-indigo-100 text-indigo-700' },
  APPROVED: { label: '수락 대기', color: 'bg-blue-100 text-blue-700' },
  DISPATCHED: { label: '배차 완료', color: 'bg-teal-100 text-teal-700' },
  RUNNING: { label: '작업 중', color: 'bg-orange-100 text-orange-700' },
  COMPLETED: { label: '완료', color: 'bg-green-100 text-green-700' },
  REJECTED: { label: '거절됨', color: 'bg-red-100 text-red-600' },
  CANCELLED: { label: '취소', color: 'bg-gray-100 text-gray-500' },
};

const TRADE_LABEL: Record<string, string> = {
  FORMWORK: '🪵 형틀목공', REBAR: '🔩 철근공', MASONRY: '🧱 조적공',
  MATERIAL_CARRY: '📦 자재운반', GENERAL: '👷 보통인부', ANY: '🔀 직종 무관',
};

type Tab = 'active' | 'running' | 'completed';

export default function OfficeHomePage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>('active');

  const fetchRequests = useCallback(async () => {
    const res = await api.get<WorkRequest[]>('/office/requests');
    if (res.success) return res.data;
    return [];
  }, []);

  const { data: requests, loading } = usePolling<WorkRequest[]>({ fetchFn: fetchRequests, interval: 5000 });

  // 탭별 필터
  const activeStatuses: WorkRequestStatus[] = ['REQUESTED', 'COMPOSING', 'PROPOSED', 'APPROVED', 'DISPATCHED'];
  const runningStatuses: WorkRequestStatus[] = ['RUNNING'];
  const completedStatuses: WorkRequestStatus[] = ['COMPLETED', 'REJECTED', 'CANCELLED'];

  const filtered = (requests || []).filter((r) => {
    if (tab === 'active') return activeStatuses.includes(r.status);
    if (tab === 'running') return runningStatuses.includes(r.status);
    return completedStatuses.includes(r.status);
  });

  const counts = {
    active: (requests || []).filter((r) => activeStatuses.includes(r.status)).length,
    running: (requests || []).filter((r) => runningStatuses.includes(r.status)).length,
    completed: (requests || []).filter((r) => completedStatuses.includes(r.status)).length,
  };

  const TAB_CONFIG: { key: Tab; label: string; }[] = [
    { key: 'active', label: `요청 접수 (${counts.active})` },
    { key: 'running', label: `작업 중 (${counts.running})` },
    { key: 'completed', label: `완료 (${counts.completed})` },
  ];

  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xl font-semibold text-gray-800">인력 요청 관리</h2>
        <button onClick={() => navigate('/office/workers')}
          className="text-sm text-purple-600 hover:text-purple-800 transition-colors">
          근로자 목록 보기 →
        </button>
      </div>

      {/* 탭 */}
      <div className="flex border-b border-gray-200">
        {TAB_CONFIG.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              tab === t.key ? 'border-purple-600 text-purple-700' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}>
            {t.label}
          </button>
        ))}
      </div>

      {loading && !requests && (
        <div className="bg-white rounded-lg border border-gray-200 p-6 text-center">
          <p className="text-gray-400">불러오는 중...</p>
        </div>
      )}

      {filtered.length === 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-10 text-center">
          <p className="text-gray-500">
            {tab === 'active' && '접수된 인력 요청이 없습니다.'}
            {tab === 'running' && '현재 작업 중인 요청이 없습니다.'}
            {tab === 'completed' && '완료된 요청이 없습니다.'}
          </p>
        </div>
      )}

      {filtered.length > 0 && (
        <div className="space-y-3">
          {filtered.map((req) => {
            const statusInfo = STATUS_CONFIG[req.status];
            const totalWorkers = req.required_workers.reduce((s, w) => s + w.count, 0);
            const canCompose = req.status === 'REQUESTED';

            return (
              <div key={req.request_id}
                onClick={() => navigate(`/office/requests/${req.request_id}`)}
                className="bg-white rounded-lg border border-gray-200 p-5 hover:border-purple-300 hover:shadow-sm cursor-pointer transition-all">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <h3 className="font-medium text-gray-800">{req.site_name}</h3>
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusInfo.color}`}>{statusInfo.label}</span>
                      {canCompose && <span className="text-xs bg-purple-50 text-purple-600 px-2 py-0.5 rounded-full">편성 가능</span>}
                    </div>
                    {req.company_name && (
                      <p className="text-xs text-gray-500 mb-0.5">🏢 {req.company_name}</p>
                    )}
                    <p className="text-sm text-gray-500 break-words">{req.location_text}</p>
                  </div>
                  <div className="text-right text-sm">
                    <p className="text-gray-800 font-medium">{req.work_date}</p>
                    <p className="text-gray-400">{req.start_time}</p>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
                  <span>필요 인원: {totalWorkers}명</span>
                  <span>{req.required_workers.map((w) => `${TRADE_LABEL[w.trade] || w.trade} ${w.count}명`).join(', ')}</span>
                  <span className="sm:ml-auto">총예산: {req.budget.toLocaleString()}원</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
