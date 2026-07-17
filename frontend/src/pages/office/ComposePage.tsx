import { useState, useEffect } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { api } from '../../api/client';
import type { Worker, WorkRequest, Crew, CrewMember, Trade, RequiredTrade, RequiredWorker } from '../../api/types';
import { tradeMeta } from '../../lib/trades';
import { commaInputValue, parseDigits } from '../../lib/format';

const TRADE_LABEL: Record<string, string> = {
  FORMWORK: '🪵 형틀목공',
  REBAR: '🔩 철근공',
  MASONRY: '🧱 조적공',
  MATERIAL_CARRY: '📦 자재운반',
  GENERAL: '👷 보통인부',
  ANY: '🔀 직종 무관',
};

const ALL_TRADES: Trade[] = ['FORMWORK', 'REBAR', 'MASONRY', 'MATERIAL_CARRY', 'GENERAL'];

// 직종 무관(ANY) 슬롯에 배정할 실제 직종 (excluded 회피).
function resolveAnyTrade(worker: Worker): Trade {
  const pref = worker.preferred_trades.find((t) => !worker.excluded_trades.includes(t));
  if (pref) return pref;
  return ALL_TRADES.find((t) => !worker.excluded_trades.includes(t)) || ALL_TRADES[0];
}

// 빈 자리 채우기(gap-fill) 모드는 "승인·배차되어 실제 확정된 팀원"이 있을 때만.
// PROPOSED(승인 전 AI 추천안)는 확정 팀원이 아니므로 수동 편성 시 그대로 시작한다. (A-4)
const GAP_FILL_CREW_STATUSES = ['APPROVED', 'NOTIFIED', 'DISPATCHED', 'RUNNING'];

interface SelectedMember {
  worker_id: string;
  assigned_trade: Trade;
  offered_wage: number;
}

export default function ComposePage() {
  const { requestId } = useParams<{ requestId: string }>();
  const navigate = useNavigate();
  const location = useLocation();

  const [request, setRequest] = useState<WorkRequest | null>(null);
  const [candidates, setCandidates] = useState<Worker[]>([]);
  const [selected, setSelected] = useState<SelectedMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [approving, setApproving] = useState(false);
  const [error, setError] = useState('');
  const [conflictError, setConflictError] = useState('');
  // 빈 자리 채우기(부분 재편성) 모드
  const [activeCrew, setActiveCrew] = useState<Crew | null>(null);
  const [fixedMembers, setFixedMembers] = useState<CrewMember[]>([]);
  // 후보 필터 (D-18)
  const [filterTrade, setFilterTrade] = useState<Trade | ''>('');
  const [onlyNeeded, setOnlyNeeded] = useState(false);
  const [searchName, setSearchName] = useState('');
  const [filterRegion, setFilterRegion] = useState('');
  const [minCareer, setMinCareer] = useState('');
  const [maxWage, setMaxWage] = useState('');
  const [sortBy, setSortBy] = useState('CAREER_DESC');
  const [detailWorker, setDetailWorker] = useState<Worker | null>(null);

  useEffect(() => {
    (async () => {
      const [reqRes, workersRes] = await Promise.all([
        api.get<WorkRequest & { crew: Crew | null }>(`/office/requests/${requestId}`),
        api.get<Worker[]>('/office/workers'),
      ]);
      if (reqRes.success) {
        setRequest(reqRes.data);
        const crew = reqRes.data.crew;
        // 승인·배차된 crew에 확정(거절 아닌) 멤버가 있을 때만 빈 자리 채우기 모드.
        // 승인 전 AI 추천(PROPOSED)은 확정 팀원으로 취급하지 않는다.
        if (crew && GAP_FILL_CREW_STATUSES.includes(crew.status)) {
          const fixed = crew.members.filter((m) => m.acceptance !== 'DECLINED');
          if (fixed.length > 0) {
            setActiveCrew(crew);
            setFixedMembers(fixed);
          }
        }
      }
      if (workersRes.success) {
        const ready = workersRes.data.filter((w) => w.state === 'READY');
        setCandidates(ready);
        const recommended = (location.state as { recommendedMembers?: SelectedMember[] } | null)?.recommendedMembers || [];
        if (recommended.length > 0) {
          const readyIds = new Set(ready.map((worker) => worker.worker_id));
          setSelected(recommended.filter((member) => readyIds.has(member.worker_id)));
        }
      }
      setLoading(false);
    })();
  }, [requestId, location.state]);

  const gapMode = fixedMembers.length > 0;

  // worker가 특정 직종에 배치 가능한지 (excluded가 아닌지)
  const canAssignTrade = (worker: Worker, trade: Trade) => {
    return !worker.excluded_trades.includes(trade);
  };

  // worker가 아무 직종에도 배치 불가한지 (모든 요청 직종이 excluded). ANY 요구가 있으면 배치 가능.
  const isFullyExcluded = (worker: Worker) => {
    if (!request) return false;
    return request.required_workers.every((rw) => rw.trade !== 'ANY' && worker.excluded_trades.includes(rw.trade));
  };

  // 이 요청에 거절한 이력이 있는지
  const isDeclined = (worker: Worker) => {
    return (request?.declined_worker_ids || []).includes(worker.worker_id);
  };

  const isSelected = (workerId: string) => selected.some((s) => s.worker_id === workerId);

  const getDefaultTrade = (worker: Worker): Trade => {
    if (!request) return ALL_TRADES[0];
    // 희망 직종 중 요청에 있는 것 우선
    for (const pt of worker.preferred_trades) {
      if (request.required_workers.some((rw) => rw.trade === pt)) return pt;
    }
    // 그 외 배치 가능한 것 (ANY 요구는 배치 가능한 실제 직종으로)
    for (const rw of request.required_workers) {
      if (rw.trade === 'ANY') return resolveAnyTrade(worker);
      if (canAssignTrade(worker, rw.trade)) return rw.trade;
    }
    return ALL_TRADES[0];
  };

  const toggleWorker = (worker: Worker) => {
    if (isSelected(worker.worker_id)) {
      setSelected(selected.filter((s) => s.worker_id !== worker.worker_id));
    } else {
      setSelected([...selected, {
        worker_id: worker.worker_id,
        assigned_trade: getDefaultTrade(worker),
        offered_wage: worker.desired_daily_wage,
      }]);
    }
  };

  const updateMember = (workerId: string, field: 'assigned_trade' | 'offered_wage', value: string | number) => {
    setSelected(selected.map((s) =>
      s.worker_id === workerId
        ? { ...s, [field]: field === 'offered_wage' ? Number(value) : value }
        : s
    ));
  };

  // 직종별 충족 현황 (gap 모드면 fixed 멤버도 포함해서 카운트).
  // 특정 직종을 먼저 소비하고, 남은 인원으로 ANY(직종 무관) 요구를 채운다.
  const getTradeStatus = (): { trade: RequiredTrade; required: number; have: number }[] => {
    if (!request) return [];
    const pool: Trade[] = [
      ...fixedMembers.map((m) => m.assigned_trade),
      ...selected.map((s) => s.assigned_trade),
    ];
    const rows = request.required_workers.map((rw: RequiredWorker) => ({
      trade: rw.trade, required: rw.count, have: 0,
    }));
    // 1) 특정 직종 소비
    rows.forEach((row) => {
      if (row.trade === 'ANY') return;
      while (row.have < row.required) {
        const idx = pool.indexOf(row.trade as Trade);
        if (idx < 0) break;
        pool.splice(idx, 1);
        row.have++;
      }
    });
    // 2) 남은 인원으로 ANY 충족
    rows.forEach((row) => {
      if (row.trade !== 'ANY') return;
      const take = Math.min(row.required, pool.length);
      pool.splice(0, take);
      row.have = take;
    });
    return rows;
  };

  const tradeStatus = getTradeStatus();
  const allFulfilled = tradeStatus.every((t) => t.have >= t.required);
  const fixedCost = fixedMembers.reduce((s, m) => s + m.offered_wage, 0);
  const totalCost = fixedCost + selected.reduce((s, m) => s + m.offered_wage, 0);
  const overBudget = request ? totalCost > request.budget && request.budget > 0 : false;

  // 후보 필터 (D-18): 직종 선택 + 아직 부족한 직종만 보기
  const neededTrades = tradeStatus.filter((t) => t.have < t.required).map((t) => t.trade);
  const canFillTrade = (w: Worker, t: string) => t === 'ANY' || !w.excluded_trades.includes(t as Trade);
  const regionOptions = [...new Set(candidates.map((worker) => worker.region))].sort((a, b) => a.localeCompare(b, 'ko'));
  const normalizedSearch = searchName.trim().toLocaleLowerCase('ko');
  const filteredCandidates = candidates
    .filter((w) => {
      if (normalizedSearch && !w.name.toLocaleLowerCase('ko').includes(normalizedSearch)) return false;
      if (filterRegion && w.region !== filterRegion) return false;
      if (minCareer && w.career_years < Number(minCareer)) return false;
      if (maxWage && w.desired_daily_wage > Number(maxWage)) return false;
      if (filterTrade === 'GENERAL' && w.excluded_trades.includes('GENERAL')) return false;
      if (filterTrade && filterTrade !== 'GENERAL' && !w.preferred_trades.includes(filterTrade)) return false;
      if (onlyNeeded && !neededTrades.some((t) => canFillTrade(w, t))) return false;
      return true;
    })
    .sort((a, b) => {
      if (sortBy === 'NAME_ASC') return a.name.localeCompare(b.name, 'ko');
      if (sortBy === 'WAGE_ASC') return a.desired_daily_wage - b.desired_daily_wage;
      if (sortBy === 'WAGE_DESC') return b.desired_daily_wage - a.desired_daily_wage;
      if (sortBy === 'COMPLETED_DESC') return b.completed_count - a.completed_count;
      return b.career_years - a.career_years;
    });

  const handleApprove = async () => {
    if (!requestId) return;
    setApproving(true);
    setError('');
    setConflictError('');

    // 빈 자리 채우기 모드: 기존 팀원 유지하고 신규만 투입
    if (gapMode && activeCrew) {
      const res = await api.post<Crew>(`/office/crews/${activeCrew.crew_id}/fill-gap`, { members: selected });
      setApproving(false);
      if (res.success) {
        navigate(`/office/requests/${requestId}`);
      } else if (res.error.code === 'STATE_CONFLICT') {
        setConflictError(res.error.message);
      } else {
        setError(res.error.message);
      }
      return;
    }

    // 일반 신규 편성
    const crewRes = await api.post<Crew>('/office/crews/manual', {
      request_id: requestId,
      members: selected,
    });

    if (!crewRes.success) {
      setApproving(false);
      setError(crewRes.error.message);
      return;
    }

    const crew = crewRes.data;
    const approveRes = await api.post<Crew>(`/office/crews/${crew.crew_id}/approve`);
    setApproving(false);

    if (approveRes.success) {
      navigate(`/office/requests/${requestId}`);
    } else if (approveRes.error.code === 'STATE_CONFLICT') {
      setConflictError(approveRes.error.message);
    } else {
      setError(approveRes.error.message);
    }
  };

  if (loading) return <p className="text-center text-gray-400 py-10">불러오는 중...</p>;
  if (!request) return <p className="text-center text-gray-500 py-10">요청을 찾을 수 없습니다.</p>;

  return (
    <div className="max-w-5xl mx-auto space-y-4">
      <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="text-xl font-semibold text-gray-800 break-words">
          {gapMode ? '빈 자리 채우기' : '수동 편성'} — {request.site_name}
        </h2>
        <button onClick={() => navigate(`/office/requests/${requestId}`)}
          className="text-sm text-gray-500 hover:text-gray-800">← 돌아가기</button>
      </div>

      {/* 기존 팀원 (gap 모드) */}
      {gapMode && (
        <div className="bg-blue-50 rounded-lg border border-blue-200 p-4">
          <h3 className="text-sm font-medium text-blue-700 mb-2">유지되는 기존 팀원 ({fixedMembers.length}명)</h3>
          <div className="space-y-1.5">
            {fixedMembers.map((m) => (
              <div key={m.worker_id} className="flex items-center justify-between text-sm bg-white rounded px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-gray-800">{m.name}</span>
                  <span className="text-xs text-gray-500">{TRADE_LABEL[m.assigned_trade]}</span>
                  <span className="text-xs bg-blue-100 text-blue-600 px-1.5 py-0.5 rounded-full">
                    {m.acceptance === 'ACCEPTED' ? '수락 완료' : '응답 대기'}
                  </span>
                </div>
                <span className="text-xs text-gray-400">{m.offered_wage.toLocaleString()}원</span>
              </div>
            ))}
          </div>
          <p className="text-xs text-blue-600 mt-2">이 인원은 그대로 유지되며, 빈 자리(거절/취소된 자리)만 새로 채웁니다.</p>
        </div>
      )}

      {/* 직종별 충족 현황 */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="text-sm font-medium text-gray-500 mb-2">
          직종별 충족 현황 {allFulfilled && <span className="ml-2 text-green-600">✓ 모두 충족</span>}
        </h3>
        <div className="flex flex-wrap gap-3">
          {tradeStatus.map((t) => (
            <div key={t.trade} className={`px-3 py-2 rounded-lg text-sm ${t.have >= t.required ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
              <span className="font-medium">{TRADE_LABEL[t.trade]}</span>
              <span className="ml-2">{t.have}/{t.required}명</span>
            </div>
          ))}
        </div>
        <div className="mt-2 text-sm text-gray-500">
          선택: {selected.length}명 / 예상 총 비용: {totalCost.toLocaleString()}원
          {request.budget > 0 && <span className="ml-2 text-gray-400">/ 총예산: {request.budget.toLocaleString()}원</span>}
          {overBudget && <span className="text-red-600 ml-2">⚠ 총예산 초과 — 승인 불가</span>}
        </div>
      </div>

      {/* 에러 */}
      {error && <div className="bg-red-50 border border-red-200 text-red-700 text-sm p-3 rounded-lg">{error}</div>}
      {conflictError && (
        <div className="bg-yellow-50 border border-yellow-200 p-4 rounded-lg">
          <p className="text-yellow-800 font-medium text-sm">⚠ 배정 충돌</p>
          <p className="text-yellow-700 text-sm mt-1">{conflictError}</p>
        </div>
      )}

      {/* 선택된 멤버 — 직종/금액 조절 */}
      {selected.length > 0 && (
        <div className="bg-purple-50 rounded-lg border border-purple-200 p-4">
          <h3 className="text-sm font-medium text-purple-700 mb-3">선택된 인원 ({selected.length}명)</h3>
          <div className="space-y-2">
            {selected.map((s) => {
              const w = candidates.find((c) => c.worker_id === s.worker_id)!;
              return (
                <div key={s.worker_id} className="flex flex-wrap items-center gap-2 bg-white rounded p-2">
                  <span className="font-medium text-sm text-gray-800 w-16">{w.name}</span>
                  <select value={s.assigned_trade}
                    onChange={(e) => updateMember(s.worker_id, 'assigned_trade', e.target.value)}
                    className="border border-gray-300 rounded px-2 py-1 text-sm">
                    {ALL_TRADES.filter((t) => canAssignTrade(w, t)).map((t) => (
                      <option key={t} value={t}>{TRADE_LABEL[t]}{w.preferred_trades.includes(t) ? ' ★' : ''}</option>
                    ))}
                  </select>
                  <input type="text" inputMode="numeric" value={commaInputValue(s.offered_wage)}
                    onChange={(e) => updateMember(s.worker_id, 'offered_wage', parseDigits(e.target.value))}
                    className="w-28 border border-gray-300 rounded px-2 py-1 text-sm" />
                  <span className="text-xs text-gray-400">원</span>
                  <button onClick={() => setSelected(selected.filter((x) => x.worker_id !== s.worker_id))}
                    className="text-gray-400 hover:text-red-500 ml-auto">×</button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 후보 필터 (D-18) */}
      <div className="bg-white rounded-lg border border-gray-200 p-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <input value={searchName} onChange={(e) => setSearchName(e.target.value)}
          placeholder="근로자 이름 검색"
          className="border border-gray-300 rounded px-2 py-1.5 text-sm" />
        <select value={filterTrade} onChange={(e) => setFilterTrade(e.target.value as Trade | '')}
          className="border border-gray-300 rounded px-2 py-1.5 text-sm">
          <option value="">전체 직종</option>
          {ALL_TRADES.map((t) => (
            <option key={t} value={t}>{TRADE_LABEL[t]} 희망</option>
          ))}
        </select>
        <select value={filterRegion} onChange={(e) => setFilterRegion(e.target.value)}
          className="border border-gray-300 rounded px-2 py-1.5 text-sm">
          <option value="">전체 지역</option>
          {regionOptions.map((region) => <option key={region} value={region}>{region}</option>)}
        </select>
        <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}
          className="border border-gray-300 rounded px-2 py-1.5 text-sm">
          <option value="CAREER_DESC">경력 높은 순</option>
          <option value="WAGE_ASC">희망 일당 낮은 순</option>
          <option value="WAGE_DESC">희망 일당 높은 순</option>
          <option value="COMPLETED_DESC">완료 작업 많은 순</option>
          <option value="NAME_ASC">이름 순</option>
        </select>
        <label className="flex items-center gap-2 text-xs text-gray-600">
          <span className="shrink-0">최소 경력</span>
          <input type="number" min="0" value={minCareer} onChange={(e) => setMinCareer(e.target.value)}
            placeholder="0" className="min-w-0 w-full border border-gray-300 rounded px-2 py-1.5 text-sm" />
          <span>년</span>
        </label>
        <label className="flex items-center gap-2 text-xs text-gray-600">
          <span className="shrink-0">최대 일당</span>
          <input type="text" inputMode="numeric" value={maxWage ? commaInputValue(Number(maxWage)) : ''}
            onChange={(e) => setMaxWage(String(parseDigits(e.target.value) || ''))}
            placeholder="제한 없음" className="min-w-0 w-full border border-gray-300 rounded px-2 py-1.5 text-sm" />
        </label>
        <label className="flex items-center gap-1.5 text-sm text-gray-600 cursor-pointer">
          <input type="checkbox" checked={onlyNeeded} onChange={(e) => setOnlyNeeded(e.target.checked)}
            className="rounded border-gray-300" />
          부족 직종만
        </label>
        <span className="text-xs text-gray-400 self-center sm:text-right">{filteredCandidates.length}명 표시</span>
      </div>

      {/* 후보 테이블 (모바일: 가로 스크롤) */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-x-auto">
        <table className="w-full text-sm min-w-[700px]">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="w-10 px-4 py-3"></th>
              <th className="text-left px-4 py-3 text-gray-500 font-medium">이름</th>
              <th className="text-left px-4 py-3 text-gray-500 font-medium">희망 직종</th>
              <th className="text-center px-4 py-3 text-gray-500 font-medium">경력</th>
              <th className="text-right px-4 py-3 text-gray-500 font-medium">희망 일당</th>
              <th className="text-left px-4 py-3 text-gray-500 font-medium">지역</th>
              <th className="text-center px-4 py-3 text-gray-500 font-medium">배치 가능</th>
              <th className="text-center px-4 py-3 text-gray-500 font-medium">지원서</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {filteredCandidates.map((w) => {
              const excluded = isFullyExcluded(w);
              const declined = isDeclined(w);
              const blocked = excluded || declined;
              const checked = isSelected(w.worker_id);
              return (
                <tr key={w.worker_id}
                  onClick={() => !blocked && toggleWorker(w)}
                  className={`transition-colors ${blocked ? 'opacity-50 cursor-not-allowed' : checked ? 'bg-purple-50 cursor-pointer' : 'hover:bg-gray-50 cursor-pointer'}`}>
                  <td className="px-4 py-3">
                    <input type="checkbox" checked={checked} disabled={blocked}
                      onClick={(event) => event.stopPropagation()}
                      onChange={() => !blocked && toggleWorker(w)}
                      className="rounded border-gray-300" />
                  </td>
                  <td className="px-4 py-3 font-medium text-gray-800">
                    <div className="flex items-center gap-2">
                      {w.name}
                      {declined && <span className="text-xs bg-red-100 text-red-600 px-1.5 py-0.5 rounded-full">거절함</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-gray-600">
                    <div className="flex flex-wrap gap-1">
                      {w.preferred_trades.map((t) => (
                        <span key={t} className={`text-xs px-1.5 py-0.5 rounded ${tradeMeta(t).badge}`}>{tradeMeta(t).emoji} {tradeMeta(t).label}</span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-center text-gray-600">{w.career_years}년차</td>
                  <td className="px-4 py-3 text-right text-gray-600">{w.desired_daily_wage.toLocaleString()}원</td>
                  <td className="px-4 py-3 text-gray-600">{w.region}</td>
                  <td className="px-4 py-3 text-center">
                    {declined ? (
                      <span className="text-xs text-red-500">거절함</span>
                    ) : excluded ? (
                      <span className="text-xs text-red-500">불가</span>
                    ) : (
                      <span className="text-xs text-green-600">가능</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-center">
                    <button type="button" onClick={(event) => {
                      event.stopPropagation();
                      setDetailWorker(w);
                    }} className="text-xs font-medium text-purple-700 hover:text-purple-900 hover:underline">
                      상세 보기
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* 승인 버튼 */}
      <div className="flex justify-end">
        <button onClick={handleApprove}
          disabled={!allFulfilled || overBudget || approving || selected.length === 0}
          className="bg-purple-600 text-white px-6 py-2.5 rounded-md text-sm font-medium hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
          {approving ? '처리 중...' : gapMode ? `빈 자리 채우기 (신규 ${selected.length}명)` : `편성 승인 (${selected.length}명)`}
        </button>
      </div>

      {detailWorker && (
        <WorkerDetailModal worker={detailWorker} onClose={() => setDetailWorker(null)} />
      )}
    </div>
  );
}

function WorkerDetailModal({ worker, onClose }: { worker: Worker; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog" aria-modal="true" aria-labelledby="manual-worker-detail-title"
      onClick={onClose}>
      <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl bg-white shadow-xl"
        onClick={(event) => event.stopPropagation()}>
        <div className="sticky top-0 flex items-center justify-between border-b border-gray-200 bg-white px-5 py-4">
          <div>
            <p className="text-xs font-medium text-purple-600">근로자 지원서</p>
            <h3 id="manual-worker-detail-title" className="text-lg font-semibold text-gray-800">{worker.name}</h3>
          </div>
          <button type="button" onClick={onClose} aria-label="상세 정보 닫기"
            className="rounded-md px-2 py-1 text-xl text-gray-400 hover:bg-gray-100 hover:text-gray-700">×</button>
        </div>
        <div className="space-y-5 p-5">
          <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-3">
            <DetailItem label="연락처" value={worker.phone} />
            <DetailItem label="나이" value={`${worker.age}세`} />
            <DetailItem label="지역" value={worker.region} />
            <DetailItem label="경력" value={`${worker.career_years}년`} />
            <DetailItem label="희망 일당" value={`${worker.desired_daily_wage.toLocaleString()}원`} />
            <DetailItem label="완료 작업" value={`${worker.completed_count}건`} />
          </dl>
          <DetailTags title="희망 직종" values={worker.preferred_trades.map((trade) => TRADE_LABEL[trade])} />
          <DetailTags title="비희망 직종" values={worker.excluded_trades.map((trade) => TRADE_LABEL[trade])} tone="red" />
          <DetailTags title="자격증" values={worker.certifications} />
          <DetailTags title="보유 작업 능력" values={worker.abilities || []} />
          <div>
            <p className="mb-2 text-sm font-medium text-gray-500">자기소개</p>
            <p className="whitespace-pre-wrap rounded-lg bg-gray-50 p-4 text-sm text-gray-700">
              {worker.introduction || '작성한 자기소개가 없습니다.'}
            </p>
          </div>
        </div>
        <div className="flex justify-end border-t border-gray-200 px-5 py-3">
          <button type="button" onClick={onClose}
            className="rounded-md bg-purple-600 px-4 py-2 text-sm font-medium text-white hover:bg-purple-700">닫기</button>
        </div>
      </div>
    </div>
  );
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-gray-500">{label}</dt><dd className="mt-1 font-medium text-gray-800">{value}</dd></div>;
}

function DetailTags({ title, values, tone = 'purple' }: { title: string; values: string[]; tone?: 'purple' | 'red' }) {
  return (
    <div>
      <p className="mb-2 text-sm font-medium text-gray-500">{title}</p>
      {values.length ? <div className="flex flex-wrap gap-1.5">{values.map((value) => (
        <span key={value} className={`rounded-full px-2.5 py-1 text-xs ${tone === 'red' ? 'bg-red-50 text-red-700' : 'bg-purple-50 text-purple-700'}`}>{value}</span>
      ))}</div> : <p className="text-sm text-gray-400">등록된 내용이 없습니다.</p>}
    </div>
  );
}
