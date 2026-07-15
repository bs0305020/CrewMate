import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { api } from '../../api/client';
import type { CreateWorkRequestPayload, Priority, PriorityAxis, PriorityRank, RequiredTrade, RequiredWorker, WorkRequest, Office } from '../../api/types';
import { commaInputValue, parseDigits } from '../../lib/format';

const TRADE_OPTIONS: { value: RequiredTrade; label: string }[] = [
  { value: 'ANY', label: '🔀 직종 무관' },
  { value: 'FORMWORK', label: '🪵 형틀목공' },
  { value: 'REBAR', label: '🔩 철근공' },
  { value: 'MASONRY', label: '🧱 조적공' },
  { value: 'MATERIAL_CARRY', label: '📦 자재운반' },
  { value: 'GENERAL', label: '👷 보통인부' },
];

const PRIORITY_META: Record<PriorityAxis, { label: string; icon: string; hint: string }> = {
  cost: { label: '비용', icon: '💰', hint: '저렴한 조합' },
  career: { label: '경력', icon: '🛠️', hint: '경력 우선' },
  teamwork: { label: '팀워크', icon: '🤝', hint: '협업 이력' },
};

const DEFAULT_PRIORITY_ORDER: PriorityAxis[] = ['cost', 'career', 'teamwork'];

export default function CreateRequestPage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [offices, setOffices] = useState<Office[]>([]);
  const [officeId, setOfficeId] = useState('');

  // 인력사무소 목록 로드 (API 기반 → 백엔드 연결 시 자동으로 실제 목록)
  useEffect(() => {
    (async () => {
      const res = await api.get<Office[]>('/offices');
      if (res.success) {
        setOffices(res.data);
        const firstActive = res.data.find((o) => o.active);
        if (firstActive) setOfficeId(firstActive.office_id);
      }
    })();
  }, []);

  const [form, setForm] = useState({
    site_name: '',
    work_date: '',
    start_time: '07:00',
    location_text: '',
    budget: 500000,
    notes: '',
  });

  // 우선순위: 축의 순서 자체가 순위(왼쪽=1순위). 드래그/버튼으로 순서를 바꾼다.
  const [priorityOrder, setPriorityOrder] = useState<PriorityAxis[]>(DEFAULT_PRIORITY_ORDER);
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  const movePriority = (from: number, to: number) => {
    setPriorityOrder((prev) => {
      if (to < 0 || to >= prev.length || from === to) return prev;
      const next = [...prev];
      const [item] = next.splice(from, 1);
      next.splice(to, 0, item);
      return next;
    });
  };

  const handleDrop = (idx: number) => {
    if (dragIndex !== null) movePriority(dragIndex, idx);
    setDragIndex(null);
  };

  const [requiredWorkers, setRequiredWorkers] = useState<RequiredWorker[]>([
    { trade: 'FORMWORK', count: 1 },
  ]);

  const addWorkerRow = () => {
    setRequiredWorkers([...requiredWorkers, { trade: 'GENERAL', count: 1 }]);
  };

  const removeWorkerRow = (idx: number) => {
    setRequiredWorkers(requiredWorkers.filter((_, i) => i !== idx));
  };

  const updateWorkerRow = (idx: number, field: keyof RequiredWorker, value: string | number) => {
    const updated = [...requiredWorkers];
    if (field === 'trade') {
      updated[idx] = { ...updated[idx], trade: value as RequiredTrade };
    } else {
      updated[idx] = { ...updated[idx], count: Number(value) };
    }
    setRequiredWorkers(updated);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!officeId) {
      toast.error('인력사무소를 선택해주세요.');
      return;
    }

    if (requiredWorkers.length === 0) {
      toast.error('필요 인원을 최소 1개 이상 추가해주세요.');
      return;
    }

    setLoading(true);

    // 순서 → 순위(1=최우선). 왼쪽부터 1·2·3.
    const priority = priorityOrder.reduce((acc, axis, i) => {
      acc[axis] = (i + 1) as PriorityRank;
      return acc;
    }, {} as Priority);

    const payload: CreateWorkRequestPayload = {
      office_id: officeId,
      site_name: form.site_name,
      work_date: form.work_date,
      start_time: form.start_time,
      location_text: form.location_text,
      required_workers: requiredWorkers,
      budget: form.budget,
      priority,
      notes: form.notes,
    };

    const res = await api.post<WorkRequest>('/company/requests', payload);
    setLoading(false);

    if (res.success) {
      navigate('/company');
    } else {
      toast.error(res.error.message);
    }
  };

  const totalRequired = requiredWorkers.reduce((sum, w) => sum + w.count, 0);

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-semibold text-gray-800">인력 요청 생성</h2>
        <button
          onClick={() => navigate('/company')}
          className="text-sm text-gray-500 hover:text-gray-800 transition-colors"
        >
          ← 목록으로
        </button>
      </div>

      <form onSubmit={handleSubmit} className="bg-white rounded-lg border border-gray-200 p-6 space-y-5">
        {/* 인력사무소 선택 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">인력사무소 *</label>
          <select
            value={officeId}
            onChange={(e) => setOfficeId(e.target.value)}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
          >
            {offices.map((o) => (
              <option key={o.office_id} value={o.office_id} disabled={!o.active}>
                {o.name} ({o.region}){o.active ? ` · 근로자 ${o.worker_count}명` : ' · 준비 중'}
              </option>
            ))}
          </select>
          <p className="text-xs text-gray-400 mt-1">요청을 접수할 인력사무소를 선택하세요.</p>
        </div>

        {/* 현장명 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">현장명 *</label>
          <input
            type="text"
            required
            value={form.site_name}
            onChange={(e) => setForm({ ...form, site_name: e.target.value })}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
            placeholder="해운대 B현장"
          />
        </div>

        {/* 작업일 + 시작 시간 */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">작업일 *</label>
            <input
              type="date"
              required
              value={form.work_date}
              onChange={(e) => setForm({ ...form, work_date: e.target.value })}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">시작 시간 *</label>
            <input
              type="time"
              required
              value={form.start_time}
              onChange={(e) => setForm({ ...form, start_time: e.target.value })}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
            />
          </div>
        </div>

        {/* 위치 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">위치 *</label>
          <input
            type="text"
            required
            value={form.location_text}
            onChange={(e) => setForm({ ...form, location_text: e.target.value })}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
            placeholder="부산 해운대구 우동 456-7"
          />
        </div>

        {/* 직종별 필요 인원 */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="block text-sm font-medium text-gray-700">
              직종별 필요 인원 * <span className="text-gray-400 font-normal">(총 {totalRequired}명)</span>
            </label>
            <button
              type="button"
              onClick={addWorkerRow}
              className="text-xs bg-orange-50 text-orange-600 px-2 py-1 rounded hover:bg-orange-100 transition-colors"
            >
              + 직종 추가
            </button>
          </div>
          <div className="space-y-2">
            {requiredWorkers.map((row, idx) => (
              <div key={idx} className="flex gap-2 items-center">
                <select
                  value={row.trade}
                  onChange={(e) => updateWorkerRow(idx, 'trade', e.target.value)}
                  className="flex-1 border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
                >
                  {TRADE_OPTIONS.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={row.count}
                  onChange={(e) => updateWorkerRow(idx, 'count', e.target.value)}
                  className="w-20 border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
                />
                <span className="text-xs text-gray-500">명</span>
                {requiredWorkers.length > 1 && (
                  <button
                    type="button"
                    onClick={() => removeWorkerRow(idx)}
                    className="text-gray-400 hover:text-red-500 text-lg transition-colors"
                  >
                    ×
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* 총예산 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">총예산 (원) *</label>
          <input
            type="text"
            inputMode="numeric"
            required
            value={commaInputValue(form.budget)}
            onChange={(e) => setForm({ ...form, budget: parseDigits(e.target.value) })}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
            placeholder="500,000"
          />
          <p className="text-xs text-gray-400 mt-1">{form.budget ? form.budget.toLocaleString() + '원' : ''}</p>
        </div>

        {/* 우선순위 (드래그로 순서 변경: 왼쪽일수록 우선) */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">우선순위</label>
          <p className="text-xs text-gray-400 mb-2">카드를 드래그하거나 화살표로 순서를 바꾸세요. 왼쪽일수록 AI 편성에서 더 크게 반영됩니다.</p>
          <div className="flex gap-2">
            {priorityOrder.map((axis, idx) => {
              const meta = PRIORITY_META[axis];
              return (
                <div
                  key={axis}
                  draggable
                  onDragStart={() => setDragIndex(idx)}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={() => handleDrop(idx)}
                  onDragEnd={() => setDragIndex(null)}
                  className={`flex-1 cursor-move select-none rounded-lg border p-3 text-center transition-colors ${
                    dragIndex === idx
                      ? 'border-orange-400 bg-orange-50 opacity-60'
                      : 'border-gray-300 bg-white hover:border-orange-300'
                  }`}
                >
                  <div className="text-xs font-semibold text-orange-600">{idx + 1}순위</div>
                  <div className="text-2xl leading-none mt-1">{meta.icon}</div>
                  <div className="text-sm font-medium text-gray-800 mt-1">{meta.label}</div>
                  <div className="text-[11px] text-gray-400">{meta.hint}</div>
                  <div className="mt-2 flex justify-center gap-1">
                    <button
                      type="button"
                      onClick={() => movePriority(idx, idx - 1)}
                      disabled={idx === 0}
                      aria-label="앞으로"
                      className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 hover:bg-gray-200 disabled:opacity-30"
                    >
                      ←
                    </button>
                    <button
                      type="button"
                      onClick={() => movePriority(idx, idx + 1)}
                      disabled={idx === priorityOrder.length - 1}
                      aria-label="뒤로"
                      className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 hover:bg-gray-200 disabled:opacity-30"
                    >
                      →
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* 비고 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">비고</label>
          <textarea
            value={form.notes}
            onChange={(e) => setForm({ ...form, notes: e.target.value })}
            rows={3}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
            placeholder="고층 작업 경험자 우대, 안전교육 이수 필수 등"
          />
        </div>

        {/* 제출 */}
        <div className="flex gap-3 pt-2">
          <button
            type="submit"
            disabled={loading}
            className="flex-1 bg-orange-600 text-white py-2.5 rounded-md text-sm font-medium hover:bg-orange-700 disabled:opacity-50 transition-colors"
          >
            {loading ? '생성 중...' : '요청 생성'}
          </button>
          <button
            type="button"
            onClick={() => navigate('/company')}
            className="px-4 py-2.5 border border-gray-300 text-gray-700 rounded-md text-sm hover:bg-gray-50 transition-colors"
          >
            취소
          </button>
        </div>
      </form>
    </div>
  );
}
