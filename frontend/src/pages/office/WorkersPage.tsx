import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import { usePolling } from '../../hooks/usePolling';
import type { Worker, Trade, WorkerState } from '../../api/types';

const TRADE_OPTIONS: { value: Trade | ''; label: string }[] = [
  { value: '', label: '전체 직종' },
  { value: 'FORMWORK', label: '형틀목공' },
  { value: 'REBAR', label: '철근공' },
  { value: 'MASONRY', label: '조적공' },
  { value: 'MATERIAL_CARRY', label: '자재운반' },
  { value: 'GENERAL', label: '보통인부' },
];

const TRADE_LABEL: Record<string, string> = {
  FORMWORK: '🪵 형틀목공',
  REBAR: '🔩 철근공',
  MASONRY: '🧱 조적공',
  MATERIAL_CARRY: '📦 자재운반',
  GENERAL: '👷 보통인부',
};

const STATE_CONFIG: Record<WorkerState, { label: string; color: string }> = {
  INACTIVE: { label: '비활성', color: 'bg-gray-100 text-gray-600' },
  READY: { label: '대기 중', color: 'bg-green-100 text-green-700' },
  NOTIFIED: { label: '제안 중', color: 'bg-purple-100 text-purple-700' },
  RESERVED: { label: '배차 확정', color: 'bg-blue-100 text-blue-700' },
  RUNNING: { label: '작업 중', color: 'bg-orange-100 text-orange-700' },
};

type SortKey = 'name' | 'career_desc' | 'wage_asc' | 'wage_desc' | 'completed_desc';

export default function WorkersPage() {
  const navigate = useNavigate();
  const [filterTrade, setFilterTrade] = useState<Trade | ''>('');
  const [filterState, setFilterState] = useState<WorkerState | ''>('READY');
  const [filterMinCareer, setFilterMinCareer] = useState(0);
  const [filterMaxWage, setFilterMaxWage] = useState(300000);
  const [sortBy, setSortBy] = useState<SortKey>('name');

  const fetchWorkers = useCallback(async () => {
    const res = await api.get<Worker[]>('/office/workers');
    if (res.success) return res.data;
    return [];
  }, []);

  const { data: workers, loading } = usePolling<Worker[]>({
    fetchFn: fetchWorkers,
    interval: 5000,
  });

  const filtered = (workers || []).filter((w) => {
    if (filterState && w.state !== filterState) return false;
    if (filterTrade === 'GENERAL' && w.excluded_trades.includes('GENERAL')) return false;
    if (filterTrade && filterTrade !== 'GENERAL' && !w.preferred_trades.includes(filterTrade)) return false;
    if (w.career_years < filterMinCareer) return false;
    if (w.desired_daily_wage > filterMaxWage) return false;
    return true;
  }).sort((a, b) => {
    if (sortBy === 'career_desc') return b.career_years - a.career_years || a.name.localeCompare(b.name, 'ko');
    if (sortBy === 'wage_asc') return a.desired_daily_wage - b.desired_daily_wage || a.name.localeCompare(b.name, 'ko');
    if (sortBy === 'wage_desc') return b.desired_daily_wage - a.desired_daily_wage || a.name.localeCompare(b.name, 'ko');
    if (sortBy === 'completed_desc') return b.completed_count - a.completed_count || a.name.localeCompare(b.name, 'ko');
    return a.name.localeCompare(b.name, 'ko');
  });

  return (
    <div className="max-w-5xl mx-auto space-y-4">
      <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="text-xl font-semibold text-gray-800">소속 근로자</h2>
        <button onClick={() => navigate('/office')} className="text-sm text-gray-500 hover:text-gray-800">
          ← 요청 목록으로
        </button>
      </div>

      {/* 필터 */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 flex flex-wrap gap-3 items-end">
        <div>
          <label className="block text-xs text-gray-500 mb-1">상태</label>
          <select
            value={filterState}
            onChange={(e) => setFilterState(e.target.value as WorkerState | '')}
            className="border border-gray-300 rounded px-2 py-1.5 text-sm"
          >
            <option value="">전체</option>
            <option value="READY">대기 중 (READY)</option>
            <option value="INACTIVE">비활성</option>
            <option value="RUNNING">작업 중</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">정렬</label>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as SortKey)}
            className="border border-gray-300 rounded px-2 py-1.5 text-sm"
          >
            <option value="name">이름순</option>
            <option value="career_desc">경력 높은순</option>
            <option value="wage_asc">희망 일당 낮은순</option>
            <option value="wage_desc">희망 일당 높은순</option>
            <option value="completed_desc">완료 작업 많은순</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">직종</label>
          <select
            value={filterTrade}
            onChange={(e) => setFilterTrade(e.target.value as Trade | '')}
            className="border border-gray-300 rounded px-2 py-1.5 text-sm"
          >
            {TRADE_OPTIONS.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">최소 경력(년)</label>
          <input
            type="number"
            min={0}
            max={50}
            value={filterMinCareer}
            onChange={(e) => setFilterMinCareer(Number(e.target.value))}
            className="w-16 border border-gray-300 rounded px-2 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">최대 일당</label>
          <input
            type="number"
            step={10000}
            value={filterMaxWage}
            onChange={(e) => setFilterMaxWage(Number(e.target.value))}
            className="w-28 border border-gray-300 rounded px-2 py-1.5 text-sm"
          />
        </div>
        <div className="text-xs text-gray-400 self-center ml-auto">
          {filtered.length}명 표시
        </div>
      </div>

      {/* 테이블 */}
      {loading && !workers ? (
        <p className="text-center text-gray-400 py-6">불러오는 중...</p>
      ) : (
        <div className="bg-white rounded-lg border border-gray-200 overflow-x-auto">
          <table className="w-full text-sm min-w-[720px]">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="text-left px-4 py-3 text-gray-500 font-medium">이름</th>
                <th className="text-left px-4 py-3 text-gray-500 font-medium">희망 직종</th>
                <th className="text-center px-4 py-3 text-gray-500 font-medium">경력</th>
                <th className="text-right px-4 py-3 text-gray-500 font-medium">희망 일당</th>
                <th className="text-left px-4 py-3 text-gray-500 font-medium">지역</th>
                <th className="text-center px-4 py-3 text-gray-500 font-medium">상태</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered.map((w) => {
                const stateInfo = STATE_CONFIG[w.state];
                return (
                  <tr key={w.worker_id}
                    tabIndex={0}
                    role="link"
                    onClick={() => navigate(`/office/workers/${w.worker_id}`)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') navigate(`/office/workers/${w.worker_id}`);
                    }}
                    className="hover:bg-purple-50 cursor-pointer focus:outline-none focus:bg-purple-50">
                    <td className="px-4 py-3 font-medium text-gray-800">
                      {w.name}
                      <span className="block text-[11px] font-normal text-purple-600">지원서 상세 보기</span>
                    </td>
                    <td className="px-4 py-3 text-gray-600">
                      <div className="flex flex-wrap gap-1">
                        {w.preferred_trades.map((t) => (
                          <span key={t} className="text-xs bg-green-50 text-green-700 px-1.5 py-0.5 rounded">{TRADE_LABEL[t]}</span>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-center text-gray-600">{w.career_years}년차</td>
                    <td className="px-4 py-3 text-right text-gray-600">{w.desired_daily_wage.toLocaleString()}원</td>
                    <td className="px-4 py-3 text-gray-600">{w.region}</td>
                    <td className="px-4 py-3 text-center">
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${stateInfo.color}`}>
                        {stateInfo.label}
                      </span>
                    </td>
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                    조건에 맞는 근로자가 없습니다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
