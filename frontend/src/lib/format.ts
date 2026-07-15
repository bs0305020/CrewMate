// 금액 입력/표시 포맷 헬퍼 (D-16: 3자리 콤마).

// 숫자를 3자리 콤마 문자열로. 0/빈값은 빈 문자열.
export function formatThousands(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '';
  const n = typeof value === 'number' ? value : Number(String(value).replace(/[^0-9]/g, ''));
  if (!Number.isFinite(n) || n === 0) return n === 0 ? '0' : '';
  return n.toLocaleString('ko-KR');
}

// 입력 문자열에서 숫자만 추출 (콤마/기타 제거).
export function parseDigits(value: string): number {
  const digits = value.replace(/[^0-9]/g, '');
  return digits ? Number(digits) : 0;
}

// <input> onChange용: 표시값(콤마 포함)을 반환. 상태에는 parseDigits 결과(숫자)를 저장.
export function commaInputValue(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return '';
  return value.toLocaleString('ko-KR');
}
