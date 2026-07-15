import type { AttendanceMap } from '../api/types';

// GitHub 잔디 스타일 출근일 히트맵. 최근 약 4개월(17주)을 주 단위 열로 표시한다.
const WEEKS = 17;

function toKey(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function cellColor(count: number): string {
  if (count <= 0) return 'bg-gray-100';
  if (count === 1) return 'bg-green-300';
  if (count === 2) return 'bg-green-500';
  return 'bg-green-700';
}

export default function AttendanceHeatmap({ attendance }: { attendance: AttendanceMap }) {
  const end = new Date();
  end.setHours(0, 0, 0, 0);
  // 시작: 오늘 기준 WEEKS*7일 전, 그 주의 일요일로 정렬
  const start = new Date(end);
  start.setDate(end.getDate() - (WEEKS * 7 - 1));
  start.setDate(start.getDate() - start.getDay()); // 일요일로

  const weeks: { key: string; count: number; future: boolean }[][] = [];
  const cursor = new Date(start);
  while (cursor <= end || weeks.length === 0 || weeks[weeks.length - 1].length < 7) {
    const week = weeks.length === 0 || weeks[weeks.length - 1].length === 7 ? [] : weeks[weeks.length - 1];
    if (week.length === 0) weeks.push(week);
    const key = toKey(cursor);
    week.push({ key, count: attendance[key] || 0, future: cursor > end });
    cursor.setDate(cursor.getDate() + 1);
    if (cursor > end && week.length === 7) break;
  }

  const total = Object.values(attendance).reduce((s, n) => s + n, 0);

  return (
    <div>
      <div className="flex gap-1 overflow-x-auto pb-1">
        {weeks.map((week, wi) => (
          <div key={wi} className="flex flex-col gap-1">
            {week.map((cell) => (
              <div
                key={cell.key}
                title={`${cell.key}${cell.count ? ` · 출근 ${cell.count}회` : ''}`}
                className={`w-3 h-3 rounded-sm ${cell.future ? 'bg-transparent' : cellColor(cell.count)}`}
              />
            ))}
          </div>
        ))}
      </div>
      <div className="flex items-center justify-between mt-2 text-[11px] text-gray-400">
        <span>최근 약 4개월 · 총 출근 {total}일</span>
        <span className="flex items-center gap-1">
          적음
          <span className="w-2.5 h-2.5 rounded-sm bg-gray-100 inline-block" />
          <span className="w-2.5 h-2.5 rounded-sm bg-green-300 inline-block" />
          <span className="w-2.5 h-2.5 rounded-sm bg-green-500 inline-block" />
          <span className="w-2.5 h-2.5 rounded-sm bg-green-700 inline-block" />
          많음
        </span>
      </div>
    </div>
  );
}
