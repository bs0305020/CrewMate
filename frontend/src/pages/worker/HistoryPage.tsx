import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { WorkHistoryEntry } from '../../api/types';
import { tradeLabel } from '../../lib/trades';

export default function HistoryPage() {
  const navigate = useNavigate();
  const [history, setHistory] = useState<WorkHistoryEntry[] | null>(null);

  const load = useCallback(async () => {
    const h = await api.get<WorkHistoryEntry[]>('/worker/history');
    if (h.success) setHistory(h.data);
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="max-w-lg mx-auto space-y-4">
      <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="text-xl font-semibold text-gray-800">작업 이력</h2>
        <button onClick={() => navigate('/worker')}
          className="text-sm text-gray-500 hover:text-gray-800">← 돌아가기</button>
      </div>

      {/* 완료 이력 */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-gray-500">완료 작업</h3>
          {history && history.length > 0 && (
            <span className="text-xs text-gray-400">
              총 {history.reduce((s, e) => s + e.offered_wage, 0).toLocaleString()}원
            </span>
          )}
        </div>
        {!history ? (
          <p className="text-sm text-gray-400">불러오는 중...</p>
        ) : history.length === 0 ? (
          <p className="text-sm text-gray-400">아직 완료된 작업이 없습니다.</p>
        ) : (
          <div className="space-y-3">
            {history.map((entry, idx) => (
              <article key={`${entry.crew_id}-${idx}`} className="text-sm border border-gray-200 rounded-lg p-4">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <p className="font-semibold text-gray-800">{entry.site_name}</p>
                    {entry.company_name && <p className="text-xs text-gray-500 mt-0.5">{entry.company_name}</p>}
                  </div>
                  <span className="font-medium text-orange-700 whitespace-nowrap">{entry.offered_wage.toLocaleString()}원</span>
                </div>
                <dl className="grid grid-cols-[72px_1fr] gap-x-3 gap-y-1.5 mt-3 text-xs">
                  <dt className="text-gray-400">근무 일시</dt>
                  <dd className="text-gray-700">{entry.work_date}{entry.start_time ? ` ${entry.start_time}` : ''}</dd>
                  <dt className="text-gray-400">현장 위치</dt>
                  <dd className="text-gray-700">{entry.location_text || '정보 없음'}</dd>
                  <dt className="text-gray-400">담당 직종</dt>
                  <dd className="text-gray-700">{tradeLabel(entry.assigned_trade)}</dd>
                  {entry.required_workers && entry.required_workers.length > 0 && (
                    <>
                      <dt className="text-gray-400">현장 인원</dt>
                      <dd className="text-gray-700">
                        {entry.required_workers.map((worker) => `${tradeLabel(worker.trade)} ${worker.count}명`).join(', ')}
                      </dd>
                    </>
                  )}
                  <dt className="text-gray-400">완료 처리</dt>
                  <dd className="text-gray-700">{new Date(entry.completed_at).toLocaleString('ko-KR')}</dd>
                </dl>
                {entry.notes && (
                  <div className="mt-3 pt-3 border-t border-gray-100">
                    <p className="text-xs text-gray-400 mb-1">작업 메모</p>
                    <p className="text-xs leading-5 text-gray-600 whitespace-pre-wrap">{entry.notes}</p>
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
