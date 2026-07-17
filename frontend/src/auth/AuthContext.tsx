import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';
import type { AuthUser, LoginRequest, SignupRequest } from '../api/types';
import { api, setAuthToken } from '../api/client';
import { loadAuthSession, saveAuthSession } from './session';

interface AuthContextValue {
  user: AuthUser | null;
  isAuthenticated: boolean;
  login: (credentials: LoginRequest) => Promise<{ success: boolean; error?: string; role?: string }>;
  signup: (payload: SignupRequest) => Promise<{ success: boolean; error?: string; role?: string }>;
  logout: () => void;
  updateName: (name: string) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => {
    const restored = loadAuthSession();
    setAuthToken(restored?.token ?? null);
    return restored;
  });

  const login = useCallback(async (credentials: LoginRequest) => {
    const res = await api.post<{ user: AuthUser }>('/auth/login', credentials);

    if (res.success) {
      const authUser = res.data.user;
      setUser(authUser);
      setAuthToken(authUser.token);
      saveAuthSession(authUser);
      return { success: true, role: authUser.role };
    } else {
      return { success: false, error: res.error.message };
    }
  }, []);

  const signup = useCallback(async (payload: SignupRequest) => {
    const res = await api.post<{ user: AuthUser }>('/auth/signup', payload);
    if (res.success) {
      const authUser = res.data.user;
      setUser(authUser);
      setAuthToken(authUser.token);
      saveAuthSession(authUser);
      return { success: true, role: authUser.role };
    } else {
      return { success: false, error: res.error.message };
    }
  }, []);

  const logout = useCallback(() => {
    setUser(null);
    setAuthToken(null);
    saveAuthSession(null);
  }, []);

  const updateName = useCallback((name: string) => {
    setUser((prev) => {
      if (!prev) return null;
      const updated = { ...prev, name };
      saveAuthSession(updated);
      return updated;
    });
  }, []);

  return (
    <AuthContext.Provider value={{ user, isAuthenticated: !!user, login, signup, logout, updateName }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
