import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { api } from '../../api/client';
import type { Worker, WorkerApplicationRequest, Trade, Office, WageStats } from '../../api/types';
import { commaInputValue, parseDigits } from '../../lib/format';

const TRADE_OPTIONS: { value: Trade; label: string }[] = [
  { value: 'FORMWORK', label: '🪵 형틀목공' },
  { value: 'REBAR', label: '🔩 철근공' },
  { value: 'MASONRY', label: '🧱 조적공' },
  { value: 'MATERIAL_CARRY', label: '📦 자재운반' },
  { value: 'GENERAL', label: '👷 보통인부' },
];

export default function ApplicationPage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [isEdit, setIsEdit] = useState(false);
  const [offices, setOffices] = useState<Office[]>([]);
  const [form, setForm] = useState<WorkerApplicationRequest>({
    name: '',
    phone: '',
    office_id: '',
    preferred_trades: [],
    excluded_trades: [],
    career_years: 0,
    age: 20,
    region: '',
    desired_daily_wage: 150000,
    certifications: [],
    introduction: '',
  });
  const [certInput, setCertInput] = useState('');
  const [wageStat, setWageStat] = useState<WageStats | null>(null);

  // 경력 연차별 평균 희망 일당 안내 (직종 무관)
  useEffect(() => {
    const years = form.career_years;
    if (years === null || years === undefined || Number.isNaN(years) || years < 0) {
      setWageStat(null);
      return;
    }
    let cancelled = false;
    const t = setTimeout(async () => {
      const res = await api.get<WageStats>(`/worker/wage-stats/${years}`);
      if (!cancelled && res.success) setWageStat(res.data);
    }, 400);
    return () => { cancelled = true; clearTimeout(t); };
  }, [form.career_years]);

  useEffect(() => {
    (async () => {
      // 인력사무소 목록 로드 (API 기반 → 백엔드 연결 시 실제 목록)
      const officeRes = await api.get<Office[]>('/offices');
      let officeList: Office[] = [];
      if (officeRes.success) {
        officeList = officeRes.data;
        setOffices(officeList);
      }

      const res = await api.get<Worker>('/worker/me');
      if (res.success && res.data.name) {
        const w = res.data;
        setIsEdit(true);
        setForm({
          name: w.name,
          phone: w.phone,
          office_id: w.office_id,
          preferred_trades: w.preferred_trades,
          excluded_trades: w.excluded_trades,
          career_years: w.career_years,
          age: w.age,
          region: w.region,
          desired_daily_wage: w.desired_daily_wage,
          certifications: w.certifications,
        });
      } else {
        // 신규 작성: 기본값으로 첫 활성 사무소 선택
        const firstActive = officeList.find((o) => o.active);
        if (firstActive) setForm((prev) => ({ ...prev, office_id: firstActive.office_id }));
      }
    })();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (form.preferred_trades.length === 0) {
      toast.error('희망 직종을 최소 1개 이상 선택해주세요.');
      return;
    }
    setLoading(true);
    const method = isEdit ? api.put : api.post;
    const res = await method<Worker>('/worker/application', form);
    setLoading(false);
    if (res.success) navigate('/worker');
    else toast.error(res.error.message);
  };

  const togglePreferred = (trade: Trade) => {
    if (form.preferred_trades.includes(trade)) {
      setForm({ ...form, preferred_trades: form.preferred_trades.filter((t) => t !== trade) });
    } else {
      // 비희망에서 제거하고 희망에 추가
      setForm({
        ...form,
        preferred_trades: [...form.preferred_trades, trade],
        excluded_trades: form.excluded_trades.filter((t) => t !== trade),
      });
    }
  };

  const toggleExcluded = (trade: Trade) => {
    if (form.excluded_trades.includes(trade)) {
      setForm({ ...form, excluded_trades: form.excluded_trades.filter((t) => t !== trade) });
    } else {
      // 희망에서 제거하고 비희망에 추가
      setForm({
        ...form,
        excluded_trades: [...form.excluded_trades, trade],
        preferred_trades: form.preferred_trades.filter((t) => t !== trade),
      });
    }
  };

  const addCertification = () => {
    if (certInput.trim() && !form.certifications.includes(certInput.trim())) {
      setForm({ ...form, certifications: [...form.certifications, certInput.trim()] });
      setCertInput('');
    }
  };

  const removeCertification = (cert: string) => {
    setForm({ ...form, certifications: form.certifications.filter((c) => c !== cert) });
  };

  return (
    <div className="max-w-lg mx-auto">
      <h2 className="text-xl font-semibold text-gray-800 mb-6">
        {isEdit ? '지원서 수정' : '지원서 작성'}
      </h2>

      <form onSubmit={handleSubmit} className="bg-white rounded-lg border border-gray-200 p-6 space-y-5">
        {/* 이름 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">이름 *</label>
          <input type="text" required value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-500"
            placeholder="홍길동" />
        </div>

        {/* 전화번호 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">전화번호 *</label>
          <input type="tel" required value={form.phone}
            onChange={(e) => setForm({ ...form, phone: e.target.value })}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-500"
            placeholder="010-1234-5678" />
        </div>

        {/* 인력사무소 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">인력사무소 *</label>
          <select value={form.office_id} required
            onChange={(e) => setForm({ ...form, office_id: e.target.value })}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-500">
            <option value="" disabled>인력사무소를 선택하세요</option>
            {offices.map((o) => (
              <option key={o.office_id} value={o.office_id} disabled={!o.active}>
                {o.name} ({o.region}){o.active ? '' : ' · 준비 중'}
              </option>
            ))}
          </select>
        </div>

        {/* 희망 직종 (복수 선택) */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">희망 직종 * <span className="text-gray-400 font-normal">(복수 선택 가능)</span></label>
          <div className="flex flex-wrap gap-2">
            {TRADE_OPTIONS.map((t) => {
              const isPreferred = form.preferred_trades.includes(t.value);
              return (
                <button key={t.value} type="button" onClick={() => togglePreferred(t.value)}
                  className={`px-3 py-1.5 rounded-full text-sm border transition-colors ${
                    isPreferred ? 'bg-green-600 text-white border-green-600' : 'bg-white text-gray-700 border-gray-300 hover:border-green-400'
                  }`}>
                  {isPreferred && '✓ '}{t.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* 비희망 직종 (복수 선택) */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">비희망 직종 <span className="text-gray-400 font-normal">(이 직종으로는 편성되지 않습니다)</span></label>
          <div className="flex flex-wrap gap-2">
            {TRADE_OPTIONS.map((t) => {
              const isExcluded = form.excluded_trades.includes(t.value);
              return (
                <button key={t.value} type="button" onClick={() => toggleExcluded(t.value)}
                  className={`px-3 py-1.5 rounded-full text-sm border transition-colors ${
                    isExcluded ? 'bg-red-600 text-white border-red-600' : 'bg-white text-gray-700 border-gray-300 hover:border-red-400'
                  }`}>
                  {isExcluded && '✕ '}{t.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* 경력 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">경력 (년) *</label>
          <input type="number" min={0} required value={form.career_years}
            onChange={(e) => setForm({ ...form, career_years: Number(e.target.value) })}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-500" />
        </div>

        {/* 나이 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">나이 *</label>
          <input type="number" min={18} max={70} required value={form.age}
            onChange={(e) => setForm({ ...form, age: Number(e.target.value) })}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-500" />
        </div>

        {/* 지역 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">지역 *</label>
          <input type="text" required value={form.region}
            onChange={(e) => setForm({ ...form, region: e.target.value })}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-500"
            placeholder="부산 해운대구" />
        </div>

        {/* 희망 일당 — 직접 입력 (3자리 콤마 자동) */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">희망 일당 (원) *</label>
          <input type="text" inputMode="numeric" required
            value={commaInputValue(form.desired_daily_wage)}
            onChange={(e) => setForm({ ...form, desired_daily_wage: parseDigits(e.target.value) })}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-500"
            placeholder="180,000" />
          {wageStat && wageStat.average_wage != null && wageStat.sample_count > 0 ? (
            <p className="text-xs text-green-600 mt-1">
              💡 경력 {wageStat.career_years}년차 지원자의 평균 희망 금액은 <span className="font-semibold">{wageStat.average_wage.toLocaleString()}원</span>입니다.
              <span className="text-gray-400"> (직종 무관 {wageStat.sample_count}명 기준)</span>
            </p>
          ) : (
            <p className="text-xs text-gray-400 mt-1">{form.desired_daily_wage ? form.desired_daily_wage.toLocaleString() + '원' : ''}</p>
          )}
        </div>

        {/* 자격증 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">자격증</label>
          <div className="flex gap-2">
            <input type="text" value={certInput}
              onChange={(e) => setCertInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addCertification(); } }}
              className="flex-1 border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-500"
              placeholder="자격증명 입력 후 추가" />
            <button type="button" onClick={addCertification}
              className="px-3 py-2 bg-gray-100 text-gray-700 rounded-md text-sm hover:bg-gray-200">추가</button>
          </div>
          {form.certifications.length > 0 && (
            <div className="flex flex-wrap gap-2 mt-2">
              {form.certifications.map((cert) => (
                <span key={cert} className="inline-flex items-center gap-1 bg-green-50 text-green-700 text-xs px-2 py-1 rounded-full">
                  {cert}
                  <button type="button" onClick={() => removeCertification(cert)} className="text-green-500 hover:text-green-800">×</button>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* 자기소개 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">자기소개</label>
          <textarea value={form.introduction || ''}
            onChange={(e) => setForm({ ...form, introduction: e.target.value })}
            rows={3}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-500"
            placeholder="간단한 자기소개를 입력해주세요." />
        </div>

        {/* 제출 */}
        <div className="flex gap-3 pt-2">
          <button type="submit" disabled={loading}
            className="flex-1 bg-green-600 text-white py-2.5 rounded-md text-sm font-medium hover:bg-green-700 disabled:opacity-50 transition-colors">
            {loading ? '저장 중...' : isEdit ? '수정 완료' : '지원서 제출'}
          </button>
          <button type="button" onClick={() => navigate('/worker')}
            className="px-4 py-2.5 border border-gray-300 text-gray-700 rounded-md text-sm hover:bg-gray-50 transition-colors">
            취소
          </button>
        </div>
      </form>
    </div>
  );
}
