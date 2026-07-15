import type { Trade } from '../api/types';

// 직종 표시 메타: 라벨 + 이모지 + 배지 색상 클래스 (Tailwind).
// 글자 수가 비슷해 구분이 어려운 문제(D-15)를 색과 이모지로 해소한다.
export interface TradeMeta {
  label: string;
  emoji: string;
  badge: string; // 배지 배경/글자 색 클래스
}

export const TRADE_META: Record<string, TradeMeta> = {
  FORMWORK: { label: '형틀목공', emoji: '🪵', badge: 'bg-amber-100 text-amber-800' },
  REBAR: { label: '철근공', emoji: '🔩', badge: 'bg-slate-200 text-slate-800' },
  MASONRY: { label: '조적공', emoji: '🧱', badge: 'bg-orange-100 text-orange-800' },
  MATERIAL_CARRY: { label: '자재운반', emoji: '📦', badge: 'bg-lime-100 text-lime-800' },
  GENERAL: { label: '보통인부', emoji: '👷', badge: 'bg-sky-100 text-sky-800' },
  ANY: { label: '직종 무관', emoji: '🔀', badge: 'bg-violet-100 text-violet-800' },
};

const FALLBACK: TradeMeta = { label: '', emoji: '🏗️', badge: 'bg-gray-100 text-gray-700' };

export function tradeMeta(trade: string | undefined | null): TradeMeta {
  if (!trade) return FALLBACK;
  return TRADE_META[trade] ?? { ...FALLBACK, label: trade };
}

// "🪵 형틀목공" 형태의 라벨
export function tradeLabel(trade: string | undefined | null): string {
  const m = tradeMeta(trade);
  return m.label ? `${m.emoji} ${m.label}` : m.emoji;
}

// 순수 라벨(이모지 없이)
export function tradeText(trade: string | undefined | null): string {
  return tradeMeta(trade).label || String(trade ?? '');
}

// 직종 옵션 목록 (실제 직종). 요청 폼 등에서 사용.
export const TRADE_VALUES: Trade[] = ['FORMWORK', 'REBAR', 'MASONRY', 'MATERIAL_CARRY', 'GENERAL'];
