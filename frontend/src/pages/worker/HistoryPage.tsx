import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { WorkHistoryEntry, AcceptedJob, AttendanceMap } from '../../api/types';
import { tradeLabel } from '../../lib/trades';
import AttendanceHeatmap from '../../components/AttendanceHeatmap';

const JOB_STATUS_LABEL: Record<string, string> = {
  RESERVED: '배차 완료', RUNNING: '작업 중', COMPLETED: '완료',
  CANCELLED: '취소', NO_SHOW: '노쇼', LEFT_SITE: '이탈', DECLINED: '거절',
};

export default function HistoryPage() {
  const navigate = useNavigate();
  const [history, setHistory] = useState<WorkHistoryEntry[] | null>(null);
  const [accepted, setAccepted] = useState<AcceptedJob[]>([]);
  const [attendance, setAttendance] = useState<AttendanceMap>({});

  const load = useCallback(async () => {
    const [h, a, at] = await Promise.all([
      api.get<WorkHistoryEntry[]>('/worker/history'),
      api.get<AcceptedJob[]>('/worker/accepted-jobs'),
      api.get<AttendanceMap>('/worker/attendance'),
    ]);
    if (h.success) setHistory(h.data);
    if (a.success) setAccepted(a.data);
    if (at.success) setAttendance(at.data);
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="max-w-lg mx-auto space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-800">작업 이력</h2>
        <button onClick={() => navigate('/worker')}
          className="text-sm text-gray-500 hover:text-gray-800">← 돌아가기</button>
      </div>

      {/* 출근일 히트맵 (잔디) */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="text-sm font-medium text-gray-500 mb-3">출근 기록</h3>
        <AttendanceHeatmap attendance={attendance} />
      </div>

      {/* 수락한 작업 */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="text-sm font-medium text-gray-500 mb-3">수락한 작업 ({accepted.length})</h3>
        {accepted.length === 0 ? (
          <p className="text-sm text-gray-400">수락한 작업이 없습니다.</p>
        ) : (
          <div className="space-y-2">
            {accepted.map((job, idx) => (
              <div key={idx} className="flex items-center justify-between text-sm border border-gray-100 rounded-md px-3 py-2">
                <div>
                  <p className="font-medium text-gray-800">{job.site_name}</p>
                  <p className="text-xs text-gray-500">{job.work_date} · {tradeLabel(job.assigned_trade)}</p>
                </div>
                <div className="text-right">
                  <p className="text-gray-700">{job.offered_wage.toLocaleString()}원</p>
                  <p className="text-xs text-gray-400">{JOB_STATUS_LABEL[job.status] || job.status}</p>
                </div>
              </div>
            ))}
          </div>
        )}
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
          <div className="space-y-2">
            {history.map((entry, idx) => (
              <div key={idx} className="flex items-center justify-between text-sm border border-gray-100 rounded-md px-3 py-2">
                <div>
                  <p className="font-medium text-gray-800">{entry.site_name}</p>
                  <p className="text-xs text-gray-500">{entry.work_date} · {tradeLabel(entry.assigned_trade)}</p>
                </div>
                <span className="text-gray-700">{entry.offered_wage.toLocaleString()}원</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
