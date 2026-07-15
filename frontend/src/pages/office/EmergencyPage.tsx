import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { api } from '../../api/client';
import type { GapEvent, Crew, WorkRequest, Recommendation, CrewMember, Worker, Trade } from '../../api/types';
import { tradeMeta } from '../../lib/trades';
import { commaInputValue, parseDigits } from '../../lib/format';

const TRADE_LABEL: Record<string, string> = {
  FORMWORK: '🪵 형틀목공', REBAR: '🔩 철근공', MASONRY: '🧱 조적공',
  MATERIAL_CARRY: '📦 자재운반', GENERAL: '👷 보통인부', ANY: '🔀 직종 무관',
};

const ALL_TRADES: Trade[] = ['FORMWORK', 'REBAR', 'MASONRY', 'MATERIAL_CARRY', 'GENERAL'];

// 직종 무관(ANY) 슬롯에 배정할 실제 직종 (excluded 회피).
function resolveAnyTrade(worker: Worker): Trade {
  const pref = worker.preferred_trades.find((t) => !worker.excluded_trades.includes(t));
  if (pref) return pref;
  return ALL_TRADES.find((t) => !worker.excluded_trades.includes(t)) || ALL_TRADES[0];
}

interface RequestDetail extends WorkRequest { crew: (Crew & { members: CrewMember[] }) | null; }

interface SelectedMember {
  worker_id: string;
  assigned_trade: Trade;
  offered_wage: number;
}

type Mode = 'choose' | 'ai' | 'manual';

export default function EmergencyPage() {
  const { eventId } = useParams<{ eventId: string }>();
  const navigate = useNavigate();

  const [gapEvent, setGapEvent] = useState<GapEvent | null>(null);
  const [detail, setDetail] = useState<RequestDetail | null>(null);
  const [candidates, setCandidates] = useState<Worker[]>([]);
  const [loading, setLoading] = useState(true);
  const [aiLoading, setAiLoading] = useState(false);
  const [approving, setApproving] = useState(false);
  const [selectedRank, setSelectedRank] = useState(0);
  const [mode, setMode] = useState<Mode>('choose');
  const [manualSelected, setManualSelected] = useState<SelectedMember[]>([]);

  const load = useCallback(async () => {
    if (!eventId) return;
    const evRes = await api.get<GapEvent>(`/office/gap-events/${eventId}`);
    if (evRes.success) {
      setGapEvent(evRes.data);
      const [reqRes, workersRes] = await Promise.all([
        api.get<RequestDetail>(`/office/requests/${evRes.data.request_id}`),
        api.get<Worker[]>('/office/workers'),
      ]);
      if (reqRes.success) setDetail(reqRes.data);
      if (workersRes.success) setCandidates(workersRes.data.filter((w) => w.state === 'READY'));
      // 이미 추천이 있으면 AI 모드로
      if (evRes.data.recommendations && evRes.data.recommendations.length > 0) setMode('ai');
    }
    setLoading(false);
  }, [eventId]);

  useEffect(() => { load(); }, [load]);

  const handleAiRecompose = async () => {
    setAiLoading(true);
    const res = await api.post<GapEvent>(`/office/gap-events/${eventId}/agent-recompose`);
    setAiLoading(false);
    if (res.success) {
      setGapEvent(res.data);
      setSelectedRank(0);
    } else {
      toast.error(res.error.message);
      load();
    }
  };

  const submitApprove = async (members: { worker_id: string; assigned_trade: Trade; offered_wage: number }[]) => {
    setApproving(true);
    const res = await api.post<GapEvent>(`/office/emergency/${eventId}/approve`, { members });
    setApproving(false);
    if (res.success) {
      toast.success('긴급 대체 인력에게 제안을 발송했습니다.');
      navigate(`/office/requests/${gapEvent?.request_id}`);
    } else {
      toast.error(res.error.message);
    }
  };

  const handleApproveAi = (rec: Recommendation) => {
    submitApprove(rec.members.map((m) => ({ worker_id: m.worker_id, assigned_trade: m.assigned_trade, offered_wage: m.offered_wage })));
  };

  if (loading) return <p className="text-center text-gray-400 py-10">불러오는 중...</p>;
  if (!gapEvent || !detail) return <p className="text-center text-gray-500 py-10">결원 이벤트를 찾을 수 없습니다.</p>;

  const fixedMembers = (detail.crew?.members || []).filter((m) => m.acceptance !== 'DECLINED');
  const fixedCost = fixedMembers.reduce((s, m) => s + m.offered_wage, 0);
  const recommendations = gapEvent.recommendations || [];
  const declinedIds = detail.declined_worker_ids || [];
  const fixedIds = fixedMembers.map((m) => m.worker_id);

  // 직종별 결원 현황 (요구 - 고정). 특정 직종을 먼저 소비하고 남은 인원으로 ANY 충족.
  const _fixedPool: Trade[] = fixedMembers.map((m) => m.assigned_trade);
  const _selPool: Trade[] = manualSelected.map((s) => s.assigned_trade);
  const tradeStatus = (() => {
    const fpool = [..._fixedPool];
    const pool = [..._fixedPool, ..._selPool];
    const rows = detail.required_workers.map((rw) => ({ trade: rw.trade, required: rw.count, fixedHave: 0, have: 0 }));
    rows.forEach((row) => {
      if (row.trade === 'ANY') return;
      while (row.fixedHave < row.required) {
        const i = fpool.indexOf(row.trade as Trade);
        if (i < 0) break;
        fpool.splice(i, 1);
        row.fixedHave++;
      }
      while (row.have < row.required) {
        const i = pool.indexOf(row.trade as Trade);
        if (i < 0) break;
        pool.splice(i, 1);
        row.have++;
      }
    });
    rows.forEach((row) => {
      if (row.trade !== 'ANY') return;
      row.fixedHave = Math.min(row.required, fpool.length);
      fpool.splice(0, row.fixedHave);
      row.have = Math.min(row.required, pool.length);
      pool.splice(0, row.have);
    });
    return rows;
  })();
  const allFulfilled = tradeStatus.every((t) => t.have >= t.required);
  const manualCost = manualSelected.reduce((s, m) => s + m.offered_wage, 0);
  const remainingBudget = detail.budget > 0 ? detail.budget - fixedCost : 0;
  const overBudget = remainingBudget > 0 && manualCost > remainingBudget;

  const isSelected = (id: string) => manualSelected.some((s) => s.worker_id === id);
  const canAssign = (w: Worker, t: Trade) => !w.excluded_trades.includes(t);
  const getDefaultTrade = (w: Worker): Trade => {
    // 결원 직종 중 이 worker가 가능한 것 우선 (ANY는 배치 가능한 실제 직종으로)
    for (const t of tradeStatus.filter((ts) => ts.have < ts.required).map((ts) => ts.trade)) {
      if (t === 'ANY') return resolveAnyTrade(w);
      if (canAssign(w, t)) return t;
    }
    for (const rw of detail.required_workers) {
      if (rw.trade === 'ANY') return resolveAnyTrade(w);
      if (canAssign(w, rw.trade)) return rw.trade;
    }
    return ALL_TRADES[0];
  };
  const toggleWorker = (w: Worker) => {
    if (isSelected(w.worker_id)) setManualSelected(manualSelected.filter((s) => s.worker_id !== w.worker_id));
    else setManualSelected([...manualSelected, { worker_id: w.worker_id, assigned_trade: getDefaultTrade(w), offered_wage: w.desired_daily_wage }]);
  };
  const updateMember = (id: string, field: 'assigned_trade' | 'offered_wage', value: string | number) => {
    setManualSelected(manualSelected.map((s) => s.worker_id === id ? { ...s, [field]: field === 'offered_wage' ? Number(value) : value } : s));
  };
  const isBlocked = (w: Worker) => declinedIds.includes(w.worker_id) || fixedIds.includes(w.worker_id)
    || detail.required_workers.every((rw) => rw.trade !== 'ANY' && w.excluded_trades.includes(rw.trade));

  return (
    <div className="max-w-3xl mx-auto space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-800">🚨 긴급 재편성 — {detail.site_name}</h2>
        <button onClick={() => navigate(`/office/requests/${gapEvent.request_id}`)}
          className="text-sm text-gray-500 hover:text-gray-800">← 돌아가기</button>
      </div>

      {/* 결원 정보 */}
      <div className="bg-red-50 border border-red-200 rounded-lg p-4">
        <p className="text-red-700 font-medium text-sm">
          결원 발생: {gapEvent.affected_worker_name || gapEvent.affected_worker_id}님 ({gapEvent.type})
        </p>
        <p className="text-red-600 text-xs mt-1">잔여 팀원은 그대로 유지하고, 빈 자리에 투입할 대체 인력을 찾습니다.</p>
      </div>

      {/* 잔여 팀원 (고정) */}
      <div className="bg-blue-50 rounded-lg border border-blue-200 p-4">
        <h3 className="text-sm font-medium text-blue-700 mb-2">유지되는 잔여 팀원 ({fixedMembers.length}명)</h3>
        <div className="space-y-1.5">
          {fixedMembers.map((m) => (
            <div key={m.worker_id} className="flex items-center justify-between text-sm bg-white rounded px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="font-medium text-gray-800">{m.name}</span>
                <span className="text-xs text-gray-500">{TRADE_LABEL[m.assigned_trade]}</span>
              </div>
              <span className="text-xs text-gray-400">{m.offered_wage.toLocaleString()}원</span>
            </div>
          ))}
        </div>
      </div>

      {/* 방식 선택 */}
      {mode === 'choose' && recommendations.length === 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <p className="text-gray-600 text-sm mb-4 text-center">빈 자리를 채울 대체 인력을 어떻게 찾을까요?</p>
          <div className="flex gap-3">
            <button onClick={() => { setMode('ai'); handleAiRecompose(); }} disabled={aiLoading}
              className="flex-1 bg-indigo-600 text-white py-3 rounded-md text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors">
              🤖 AI 긴급 추천
            </button>
            <button onClick={() => setMode('manual')}
              className="flex-1 bg-purple-600 text-white py-3 rounded-md text-sm font-medium hover:bg-purple-700 transition-colors">
              ✋ 수동 선택
            </button>
          </div>
        </div>
      )}

      {/* AI 모드: 로딩 */}
      {mode === 'ai' && recommendations.length === 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6 text-center">
          <p className="text-gray-600 text-sm mb-3">AI가 잔여 팀원과 가장 잘 맞는 대체 인력을 분석 중입니다.</p>
          <p className="text-indigo-600 text-sm font-medium">{aiLoading ? '🤖 분석 중...' : ''}</p>
          {!aiLoading && (
            <button onClick={handleAiRecompose}
              className="mt-2 bg-indigo-600 text-white px-5 py-2 rounded-md text-sm font-medium hover:bg-indigo-700">
              AI 추천 실행
            </button>
          )}
          <button onClick={() => setMode('manual')} className="block mx-auto mt-3 text-sm text-purple-600 hover:underline">
            수동으로 직접 선택하기 →
          </button>
        </div>
      )}

      {/* AI 추천 결과 */}
      {mode === 'ai' && recommendations.length > 0 && (
        <div className="space-y-4">
          <h3 className="text-sm font-medium text-gray-700">🤖 긴급 대체 추천 ({recommendations.length}안)</h3>
          {recommendations.map((rec, idx) => (
            <div key={rec.rank} onClick={() => setSelectedRank(idx)}
              className={`bg-white rounded-lg border-2 p-5 cursor-pointer transition-all ${selectedRank === idx ? 'border-indigo-500 shadow-md' : 'border-gray-200 hover:border-indigo-300'}`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold text-indigo-700">대체 {rec.rank}안</span>
                  {typeof rec.fitness === 'number' && (
                    <span className="text-xs font-medium bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded-full">
                      적합도 {rec.fitness}%
                    </span>
                  )}
                </div>
                <span className="text-sm font-medium text-gray-800">+{rec.total_cost.toLocaleString()}원</span>
              </div>
              <div className="space-y-1.5 mb-3">
                {rec.members.map((m) => (
                  <div key={m.worker_id} className="flex items-center justify-between text-sm py-1 px-2 bg-green-50 rounded">
                    <div className="flex items-center gap-2">
                      <span className="text-xs bg-green-600 text-white px-1.5 py-0.5 rounded-full">신규</span>
                      <span className="font-medium text-gray-800">{m.name}</span>
                      <span className="text-xs text-gray-500">{TRADE_LABEL[m.assigned_trade]}</span>
                    </div>
                    <span className="text-xs text-gray-500">{m.offered_wage.toLocaleString()}원</span>
                  </div>
                ))}
              </div>
              <p className="text-sm text-gray-600 bg-gray-50 rounded p-2">{rec.reason}</p>
              {selectedRank === idx && (
                <button onClick={() => handleApproveAi(rec)} disabled={approving}
                  className="mt-3 w-full bg-indigo-600 text-white py-2 rounded-md text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors">
                  {approving ? '승인 처리 중...' : `${rec.rank}안 승인 (긴급 배차)`}
                </button>
              )}
            </div>
          ))}
          <div className="flex gap-3">
            <button onClick={handleAiRecompose} disabled={aiLoading} className="text-sm text-gray-500 hover:text-gray-800">다시 추천받기 →</button>
            <button onClick={() => setMode('manual')} className="text-sm text-purple-600 hover:underline ml-auto">수동으로 직접 선택 →</button>
          </div>
        </div>
      )}

      {/* 수동 선택 모드 */}
      {mode === 'manual' && (
        <>
          {/* 결원 직종 현황 */}
          <div className="bg-white rounded-lg border border-gray-200 p-4">
            <h3 className="text-sm font-medium text-gray-500 mb-2">
              결원 충원 현황 {allFulfilled && <span className="ml-2 text-green-600">✓ 충족</span>}
            </h3>
            <div className="flex flex-wrap gap-3">
              {tradeStatus.map((t) => (
                <div key={t.trade} className={`px-3 py-2 rounded-lg text-sm ${t.have >= t.required ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                  <span className="font-medium">{TRADE_LABEL[t.trade]}</span>
                  <span className="ml-2">{t.have}/{t.required}명</span>
                  <span className="ml-1 text-xs text-gray-400">(기존 {t.fixedHave})</span>
                </div>
              ))}
            </div>
            <div className="mt-2 text-sm text-gray-500">
              신규 선택: {manualSelected.length}명 / 추가 비용: {manualCost.toLocaleString()}원
              {remainingBudget > 0 && <span className="ml-2 text-gray-400">/ 잔여 예산: {remainingBudget.toLocaleString()}원</span>}
              {overBudget && <span className="text-red-600 ml-2">⚠ 잔여 예산 초과</span>}
            </div>
          </div>

          {/* 선택된 신규 인원 */}
          {manualSelected.length > 0 && (
            <div className="bg-green-50 rounded-lg border border-green-200 p-4">
              <h3 className="text-sm font-medium text-green-700 mb-3">신규 투입 인원 ({manualSelected.length}명)</h3>
              <div className="space-y-2">
                {manualSelected.map((s) => {
                  const w = candidates.find((c) => c.worker_id === s.worker_id)!;
                  return (
                    <div key={s.worker_id} className="flex flex-wrap items-center gap-2 bg-white rounded p-2">
                      <span className="font-medium text-sm text-gray-800 w-16">{w.name}</span>
                      <select value={s.assigned_trade} onChange={(e) => updateMember(s.worker_id, 'assigned_trade', e.target.value)}
                        className="border border-gray-300 rounded px-2 py-1 text-sm">
                        {ALL_TRADES.filter((t) => canAssign(w, t)).map((t) => (
                          <option key={t} value={t}>{TRADE_LABEL[t]}{w.preferred_trades.includes(t) ? ' ★' : ''}</option>
                        ))}
                      </select>
                      <input type="text" inputMode="numeric" value={commaInputValue(s.offered_wage)}
                        onChange={(e) => updateMember(s.worker_id, 'offered_wage', parseDigits(e.target.value))}
                        className="w-28 border border-gray-300 rounded px-2 py-1 text-sm" />
                      <span className="text-xs text-gray-400">원</span>
                      <button onClick={() => setManualSelected(manualSelected.filter((x) => x.worker_id !== s.worker_id))}
                        className="text-gray-400 hover:text-red-500 ml-auto">×</button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* 후보 테이블 (모바일: 가로 스크롤) */}
          <div className="bg-white rounded-lg border border-gray-200 overflow-x-auto">
            <table className="w-full text-sm min-w-[480px]">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="w-10 px-4 py-3"></th>
                  <th className="text-left px-4 py-3 text-gray-500 font-medium">이름</th>
                  <th className="text-left px-4 py-3 text-gray-500 font-medium">희망 직종</th>
                  <th className="text-center px-4 py-3 text-gray-500 font-medium">경력</th>
                  <th className="text-right px-4 py-3 text-gray-500 font-medium">희망 일당</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {candidates.map((w) => {
                  const blocked = isBlocked(w);
                  const checked = isSelected(w.worker_id);
                  return (
                    <tr key={w.worker_id}
                      onClick={() => !blocked && toggleWorker(w)}
                      className={`transition-colors ${blocked ? 'opacity-40 cursor-not-allowed' : checked ? 'bg-green-50 cursor-pointer' : 'hover:bg-gray-50 cursor-pointer'}`}>
                      <td className="px-4 py-3">
                        <input type="checkbox" checked={checked} disabled={blocked}
                          onChange={() => !blocked && toggleWorker(w)} className="rounded border-gray-300" />
                      </td>
                      <td className="px-4 py-3 font-medium text-gray-800">{w.name}</td>
                      <td className="px-4 py-3 text-gray-600">
                        <div className="flex flex-wrap gap-1">
                          {w.preferred_trades.map((t) => (
                            <span key={t} className={`text-xs px-1.5 py-0.5 rounded ${tradeMeta(t).badge}`}>{tradeMeta(t).emoji} {tradeMeta(t).label}</span>
                          ))}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-center text-gray-600">{w.career_years}년차</td>
                      <td className="px-4 py-3 text-right text-gray-600">{w.desired_daily_wage.toLocaleString()}원</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* 승인 버튼 */}
          <div className="flex items-center justify-between">
            <button onClick={() => setMode('choose')} className="text-sm text-gray-500 hover:text-gray-800">← 방식 다시 선택</button>
            <button
              onClick={() => submitApprove(manualSelected)}
              disabled={!allFulfilled || overBudget || approving || manualSelected.length === 0}
              className="bg-purple-600 text-white px-6 py-2.5 rounded-md text-sm font-medium hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
              {approving ? '승인 처리 중...' : `긴급 배차 승인 (신규 ${manualSelected.length}명)`}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
