import type { AuthUser } from '../api/types';

export const AUTH_SESSION_KEY = 'crewmate:auth-user';

function isAuthUser(value: unknown): value is AuthUser {
  if (!value || typeof value !== 'object') return false;
  const user = value as Partial<AuthUser>;
  return typeof user.userId === 'string'
    && typeof user.name === 'string'
    && typeof user.token === 'string'
    && ['WORKER', 'OFFICE', 'COMPANY'].includes(user.role || '');
}

function tokenExpired(token: string): boolean {
  const parts = token.split('.');
  if (parts.length !== 3) return false; // mock token
  try {
    const normalized = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    const payload = JSON.parse(atob(padded)) as { exp?: number };
    return typeof payload.exp === 'number' && payload.exp * 1000 <= Date.now();
  } catch {
    return true;
  }
}

export function loadAuthSession(): AuthUser | null {
  if (typeof window === 'undefined') return null;
  try {
    const stored = window.sessionStorage.getItem(AUTH_SESSION_KEY);
    if (!stored) return null;
    const user: unknown = JSON.parse(stored);
    if (!isAuthUser(user) || tokenExpired(user.token)) {
      window.sessionStorage.removeItem(AUTH_SESSION_KEY);
      return null;
    }
    return user;
  } catch {
    window.sessionStorage.removeItem(AUTH_SESSION_KEY);
    return null;
  }
}

export function saveAuthSession(user: AuthUser | null): void {
  if (typeof window === 'undefined') return;
  if (user) window.sessionStorage.setItem(AUTH_SESSION_KEY, JSON.stringify(user));
  else window.sessionStorage.removeItem(AUTH_SESSION_KEY);
}
