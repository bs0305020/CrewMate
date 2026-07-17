import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { api } from '../../api/client';
import type { SpecReportJobStart, SpecReportJobState, SpecReportJobSummary, SpecReportRequest, SpecReportResponse, Trade, Worker } from '../../api/types';
import MarkdownReport, { humanizeReportText } from '../../components/MarkdownReport';
import { tradeText } from '../../lib/trades';

const LAST_REPORT_JOB_KEY = 'crewmate:last-spec-report-job';

const TARGET_OPTIONS = [
  '건축목공시공', '철근콘크리트시공', '조적미장시공', '타일석공시공', '방수시공',
  '도장시공', '비계시공', '배관시공', '용접', '건설기계운전',
];

const DEFAULT_TARGET: Partial<Record<Trade, string>> = {
  FORMWORK: '철근콘크리트시공',
  REBAR: '철근콘크리트시공',
  MASONRY: '조적미장시공',
};

export default function ReportPage() {
  const navigate = useNavigate();
  const [worker, setWorker] = useState<Worker | null>(null);
  const [targetTrade, setTargetTrade] = useState(TARGET_OPTIONS[0]);
  const [result, setResult] = useState<SpecReportResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMode, setLoadingMode] = useState<'GENERATING' | 'OPENING' | null>(
    () => localStorage.getItem(LAST_REPORT_JOB_KEY) ? 'GENERATING' : null,
  );
  const [jobId, setJobId] = useState<string | null>(() => localStorage.getItem(LAST_REPORT_JOB_KEY));
  const [savedReports, setSavedReports] = useState<SpecReportJobSummary[]>([]);

  const loadSavedReports = useCallback(async () => {
    const response = await api.get<SpecReportJobSummary[]>('/reports/spec-gap/jobs');
    if (response.success) setSavedReports(response.data);
  }, []);

  useEffect(() => { loadSavedReports(); }, [loadSavedReports]);

  useEffect(() => {
    (async () => {
      const response = await api.get<Worker>('/worker/me');
      if (!response.success) {
        toast.error('지원서를 먼저 작성해주세요.');
        navigate('/worker/application', { replace: true });
        return;
      }
      setWorker(response.data);
      const preferred = response.data.preferred_trades
        .map((trade) => DEFAULT_TARGET[trade])
        .find(Boolean);
      if (preferred) setTargetTrade(preferred);
    })();
  }, [navigate]);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let timer: number | undefined;
    const stopPolling = () => {
      if (timer !== undefined) window.clearInterval(timer);
    };

    const loadJob = async () => {
      const response = await api.get<SpecReportJobState>(`/reports/spec-gap/jobs/${jobId}`);
      if (cancelled) return;
      if (!response.success) {
        if (['REPORT_NOT_FOUND', 'HTTP_404', 'HTTP_403'].includes(response.error.code)) {
          localStorage.removeItem(LAST_REPORT_JOB_KEY);
          setJobId(null);
        }
        setLoading(false);
        setLoadingMode(null);
        stopPolling();
        loadSavedReports();
        return;
      }
      if (response.data.status === 'COMPLETED' && response.data.report) {
        setResult({
          report: response.data.report,
          markdown: response.data.markdown,
          persisted: Boolean(response.data.persisted),
          status: 'COMPLETED',
        });
        setLoading(false);
        setLoadingMode(null);
        stopPolling();
      } else if (response.data.status === 'FAILED') {
        setLoading(false);
        setLoadingMode(null);
        localStorage.removeItem(LAST_REPORT_JOB_KEY);
        setJobId(null);
        stopPolling();
        toast.error(response.data.error?.message || '보고서 생성에 실패했습니다.');
      } else {
        setLoading(true);
        setLoadingMode((current) => current || 'GENERATING');
      }
    };

    loadJob();
    timer = window.setInterval(loadJob, 3000);
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [jobId, loadSavedReports]);

  const inputSummary = useMemo(() => ({
    certifications: worker?.certifications || [],
    abilities: worker?.abilities || [],
  }), [worker]);

  const generate = async () => {
    if (!worker) return;
    setLoading(true);
    setLoadingMode('GENERATING');
    setResult(null);
    const payload: SpecReportRequest = {
      targetTrade,
      targetSpecialty: worker.preferred_trades.map((trade) => tradeText(trade)).join(', '),
      certifications: inputSummary.certifications,
      abilities: inputSummary.abilities,
      persistReport: true,
    };
    const response = await api.post<SpecReportJobStart>('/reports/spec-gap/jobs', payload);
    if (response.success) {
      localStorage.setItem(LAST_REPORT_JOB_KEY, response.data.reportId);
      setJobId(response.data.reportId);
      toast.success('보고서 생성을 시작했습니다. 다른 화면을 이용해도 계속 진행됩니다.');
    } else {
      setLoading(false);
      setLoadingMode(null);
      toast.error(response.error.message);
    }
  };

  const openSavedReport = (saved: SpecReportJobSummary) => {
    if (saved.status !== 'COMPLETED') return;
    localStorage.setItem(LAST_REPORT_JOB_KEY, saved.reportId);
    setResult(null);
    setLoading(true);
    setLoadingMode('OPENING');
    setJobId(saved.reportId);
  };

  return (
    <div className="max-w-lg mx-auto space-y-4 min-w-0">
      <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs text-green-700 font-medium">자격·NCS 근거 분석</p>
          <h2 className="text-xl font-semibold text-gray-800">내 스펙 보완 보고서</h2>
        </div>
        <button onClick={() => navigate('/worker')} className="text-sm text-gray-500 hover:text-gray-800">← 돌아가기</button>
      </div>

      <section className="bg-white rounded-lg border border-gray-200 p-6 space-y-5">
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">분석할 직종</label>
            <select value={targetTrade} onChange={(event) => setTargetTrade(event.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-green-500">
              {TARGET_OPTIONS.map((option) => <option key={option}>{option}</option>)}
            </select>
          </div>
          <button onClick={generate} disabled={loading || !worker}
            className="w-full bg-green-600 text-white px-6 py-2.5 rounded-md text-sm font-medium hover:bg-green-700 disabled:opacity-50">
            {loading ? (loadingMode === 'OPENING' ? '보고서 여는 중...' : '보고서 생성 중...') : result ? '새 보고서 생성' : '보고서 생성'}
          </button>
        </div>
        <div className="mt-4 grid md:grid-cols-2 gap-3 text-xs">
          <InputChips title="지원서 자격증" values={inputSummary.certifications} />
          <InputChips title="지원서 보유 능력" values={inputSummary.abilities} />
        </div>
        <p className="text-xs text-gray-400">완료 보고서는 S3에 저장되어 다시 열 수 있으며, 31일 후 Standard-IA로 전환되고 61일 후 삭제됩니다.</p>
      </section>

      <section className="bg-white rounded-lg border border-gray-200 p-4 sm:p-5">
        <div className="flex items-center justify-between gap-3 mb-3">
          <h3 className="font-semibold text-gray-800">이전 스펙 보고서</h3>
          <span className="text-xs text-gray-400">{savedReports.length}개</span>
        </div>
        {savedReports.length === 0 ? (
          <p className="text-sm text-gray-400">저장된 보고서가 없습니다.</p>
        ) : (
          <div className="space-y-2">
            {savedReports.map((saved) => (
              <button key={saved.reportId} type="button" onClick={() => openSavedReport(saved)}
                disabled={saved.status !== 'COMPLETED'}
                className="w-full text-left border border-gray-200 rounded-lg px-3 py-3 hover:border-green-300 hover:bg-green-50 disabled:hover:bg-white disabled:opacity-60 transition-colors">
                <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                  <span className="text-sm font-medium text-gray-800 break-words">{saved.targetTrade}</span>
                  <span className={`text-xs ${saved.status === 'COMPLETED' ? 'text-green-700' : saved.status === 'FAILED' ? 'text-red-600' : 'text-indigo-600'}`}>
                    {saved.status === 'COMPLETED' ? '보기' : saved.status === 'FAILED' ? '생성 실패' : '생성 중'}
                  </span>
                </div>
                <span className="block text-xs text-gray-400 mt-1">
                  {saved.createdAt ? new Date(saved.createdAt).toLocaleString('ko-KR') : '생성 시각 확인 중'}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      {loading && (
        <ReportSkeleton mode={loadingMode || 'GENERATING'} />
      )}

      {result && (
        <>
          <section className="grid grid-cols-1 min-[380px]:grid-cols-3 gap-2">
            <Metric label="충족 자격그룹" value={`${result.report.satisfiedCertificationGroups.length}개`} />
            <Metric label="부족 핵심그룹" value={`${result.report.missingCoreCertificationGroups.length}개`} tone="red" />
            <Metric label="능력 커버리지" value={`${result.report.abilityCoverage.percentage}%`} />
          </section>

          <section className="bg-white rounded-lg border border-gray-200 p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-gray-800">우선 보완 순서</h3>
              <span className="text-xs text-gray-400">{new Date(result.report.generatedAt).toLocaleString('ko-KR')}</span>
            </div>
            {result.report.priorityActions.length ? (
              <ol className="space-y-3">
                {result.report.priorityActions.map((action) => (
                  <li key={`${action.priority}-${action.itemName}`} className="flex gap-3 text-sm">
                    <span className="shrink-0 w-6 h-6 rounded-full bg-green-100 text-green-700 flex items-center justify-center font-semibold">{action.priority}</span>
                    <div><p className="font-medium text-gray-800">{humanizeReportText(action.itemName)}</p><p className="text-gray-500">{humanizeReportText(action.reason)}</p></div>
                  </li>
                ))}
              </ol>
            ) : <p className="text-sm text-gray-400">우선 보완 항목이 없습니다.</p>}
          </section>

          {result.markdown ? (
            <section className="bg-white rounded-lg border border-gray-200 p-4 sm:p-6 min-w-0">
              <h3 className="font-semibold text-gray-800 mb-4">전체 보고서</h3>
              <MarkdownReport markdown={result.markdown} />
            </section>
          ) : (
            <section className="bg-white rounded-lg border border-gray-200 p-6">
              <h3 className="font-semibold text-gray-800 mb-3">확인이 필요한 항목</h3>
              <ul className="text-sm text-gray-600 space-y-1">
                {[...result.report.limitations, ...result.report.humanReviewItems].map((item) => <li key={item}>· {item}</li>)}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function InputChips({ title, values }: { title: string; values: string[] }) {
  return (
    <div className="bg-gray-50 rounded-md p-3">
      <p className="text-gray-500 mb-2">{title}</p>
      {values.length ? <div className="flex flex-wrap gap-1">{values.map((value) => <span key={value} className="bg-white border border-gray-200 px-2 py-0.5 rounded-full">{value}</span>)}</div>
        : <p className="text-gray-400">등록된 내용이 없습니다.</p>}
    </div>
  );
}

function Metric({ label, value, tone = 'green' }: { label: string; value: string; tone?: 'green' | 'red' }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 text-center">
      <p className={`text-2xl font-bold ${tone === 'red' ? 'text-red-600' : 'text-green-700'}`}>{value}</p>
      <p className="text-xs text-gray-500 mt-1">{label}</p>
    </div>
  );
}

function ReportSkeleton({ mode }: { mode: 'GENERATING' | 'OPENING' }) {
  const opening = mode === 'OPENING';
  return (
    <div className="space-y-3" aria-label={opening ? '보고서 여는 중' : '보고서 생성 중'}>
      <div className="bg-green-50 border border-green-200 rounded-lg p-4">
        <p className="text-sm font-medium text-green-800">
          {opening ? '저장된 보고서를 여는 중입니다' : '보고서를 생성하고 있습니다'}
        </p>
        <p className="text-xs text-green-700 mt-1">
          {opening
            ? '저장된 보고서 내용을 안전하게 불러오고 있습니다.'
            : '이 화면을 벗어나도 작업은 계속되며, 돌아오면 자동으로 결과를 불러옵니다.'}
        </p>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {[0, 1, 2].map((item) => <div key={item} className="h-20 rounded-lg bg-gray-100 animate-pulse" />)}
      </div>
      <div className="bg-white border border-gray-200 rounded-lg p-6 space-y-3">
        <div className="h-4 bg-gray-200 rounded animate-pulse w-1/3" />
        <div className="h-3 bg-gray-100 rounded animate-pulse" />
        <div className="h-3 bg-gray-100 rounded animate-pulse w-5/6" />
        <div className="h-3 bg-gray-100 rounded animate-pulse w-2/3" />
      </div>
    </div>
  );
}
