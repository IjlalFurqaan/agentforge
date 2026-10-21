"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api, onAuthChange, refreshSession } from "./api";
import type { User } from "./types";

interface AuthState {
  user: User | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const unsubscribe = onAuthChange(setUser);
    void refreshSession().finally(() => setReady(true));
    return () => {
      unsubscribe();
    };
  }, []);

  const value: AuthState = {
    user,
    ready,
    login: async (email, password) => {
      await api.login(email, password);
    },
    logout: () => api.logout(),
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
