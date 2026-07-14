import type { ApiResponse } from './types';

const API_MODE = import.meta.env.VITE_API_MODE || 'mock';
// API Gateway base URL (스테이지 경로 포함, 예: https://xxx.execute-api.ap-northeast-2.amazonaws.com/dev)
// VITE_API_BASE_URL 우선, 하위호환으로 VITE_API_URL 도 허용.
const API_URL = import.meta.env.VITE_API_BASE_URL || import.meta.env.VITE_API_URL || '';

// mock 핸들러를 동적으로 임포트
type MockHandler = (body?: unknown, pathParam?: string) => Promise<ApiResponse<unknown>>;
let mockHandlers: Record<string, MockHandler> = {};

async function getMockHandlers() {
  if (Object.keys(mockHandlers).length === 0) {
    const mod = await import('./mock');
    mockHandlers = mod.handlers as Record<string, MockHandler>;
  }
  return mockHandlers;
}

// 인증 토큰 (메모리에만 보관)
let authToken: string | null = null;

export function setAuthToken(token: string | null) {
  authToken = token;
}

export function getAuthToken(): string | null {
  return authToken;
}

// API 요청 공통 함수
export async function apiRequest<T>(
  method: 'GET' | 'POST' | 'PUT' | 'DELETE',
  path: string,
  body?: unknown
): Promise<ApiResponse<T>> {
  if (API_MODE === 'mock') {
    const handlers = await getMockHandlers();
    const key = `${method} ${path}`;

    // 정확한 매칭 먼저 시도
    if (handlers[key]) {
      return handlers[key](body) as Promise<ApiResponse<T>>;
    }

    // 패턴 매칭 시도 (경로 파라미터 추출)
    for (const [pattern, handler] of Object.entries(handlers)) {
      const match = matchPattern(pattern, key);
      if (match) {
        return handler(body, match.param) as Promise<ApiResponse<T>>;
      }
    }

    return {
      success: false,
      error: { code: 'NOT_IMPLEMENTED', message: `Mock not implemented: ${key}` },
    } as ApiResponse<T>;
  }

  // 실 API 호출
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`;
  }

  // 15초 타임아웃 (무한 대기 방지)
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);

  try {
    const response = await fetch(`${API_URL}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    clearTimeout(timeout);

    // JSON 파싱 시도
    let data: unknown;
    try {
      data = await response.json();
    } catch {
      return {
        success: false,
        error: { code: 'INVALID_RESPONSE', message: `서버 응답을 해석할 수 없습니다. (HTTP ${response.status})` },
      } as ApiResponse<T>;
    }

    // 백엔드가 이미 { success, ... } 봉투로 주면 그대로 반환
    if (data && typeof data === 'object' && 'success' in data) {
      return data as ApiResponse<T>;
    }

    // 봉투가 아니면 HTTP 상태로 성공/실패 판단
    if (response.ok) {
      return { success: true, data: data as T };
    }
    return {
      success: false,
      error: { code: `HTTP_${response.status}`, message: `요청 실패 (HTTP ${response.status})` },
    } as ApiResponse<T>;
  } catch (e) {
    clearTimeout(timeout);
    const message = e instanceof DOMException && e.name === 'AbortError'
      ? '요청 시간이 초과되었습니다. 네트워크를 확인해주세요.'
      : '서버에 연결할 수 없습니다. 네트워크 또는 서버 상태를 확인해주세요.';
    return { success: false, error: { code: 'NETWORK_ERROR', message } } as ApiResponse<T>;
  }
}

// 경로 패턴 매칭 + 파라미터 추출
function matchPattern(pattern: string, actual: string): { param?: string } | null {
  // pattern: "GET /company/requests/{id}"
  // actual:  "GET /company/requests/REQ001"
  const paramMatch = pattern.match(/\{([^}]+)\}/);
  if (!paramMatch) return null;

  const regex = new RegExp(
    '^' + pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\\{[^}]+\\}/g, '([^/]+)') + '$'
  );
  const result = actual.match(regex);
  if (result) {
    return { param: result[1] };
  }
  return null;
}

// 편의 함수
export const api = {
  get: <T>(path: string) => apiRequest<T>('GET', path),
  post: <T>(path: string, body?: unknown) => apiRequest<T>('POST', path, body),
  put: <T>(path: string, body?: unknown) => apiRequest<T>('PUT', path, body),
  delete: <T>(path: string) => apiRequest<T>('DELETE', path),
};
