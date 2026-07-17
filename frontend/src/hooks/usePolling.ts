import { useEffect, useRef, useCallback, useState } from 'react';

interface UsePollingOptions<T> {
  fetchFn: () => Promise<T>;
  interval?: number; // ms, 기본 5000
  enabled?: boolean;
}

export function usePolling<T>({ fetchFn, interval = 5000, enabled = true }: UsePollingOptions<T>) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);
  const requestSequenceRef = useRef(0);
  const fetchRef = useRef(fetchFn);
  fetchRef.current = fetchFn;

  const execute = useCallback(async () => {
    const sequence = ++requestSequenceRef.current;
    setLoading(true);
    try {
      const result = await fetchRef.current();
      if (sequence === requestSequenceRef.current) {
        setData(result);
        setError(null);
      }
    } catch (e) {
      if (sequence === requestSequenceRef.current) {
        setError(e instanceof Error ? e.message : '데이터를 불러올 수 없습니다.');
      }
    } finally {
      if (sequence === requestSequenceRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;

    // 즉시 1회 실행
    execute();

    // 인터벌 설정
    timerRef.current = window.setInterval(execute, interval);

    // 탭 비활성 시 중단
    const handleVisibility = () => {
      if (document.hidden) {
        if (timerRef.current) {
          clearInterval(timerRef.current);
          timerRef.current = null;
        }
      } else {
        execute();
        timerRef.current = window.setInterval(execute, interval);
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [enabled, interval, execute]);

  return { data, loading, error, refetch: execute };
}
