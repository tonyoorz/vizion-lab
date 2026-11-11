/**
 * Vizion Lab 前端鉴权工具。
 * - useAuth: 检查当前登录状态
 * - login / logout: 调后端 /api/auth/*
 * - apiFetch: 带 credentials 的 fetch 包装
 */

export interface AuthState {
  authenticated: boolean;
  username: string | null;
  loading: boolean;
}

interface LoginOptions {
  strongAuth?: boolean;
}

export async function fetchAuthState(): Promise<AuthState> {
  try {
    const resp = await fetch("/api/auth/me", { credentials: "include" });
    if (!resp.ok) {
      return { authenticated: false, username: null, loading: false };
    }
    const data = await resp.json();
    return {
      authenticated: Boolean(data.authenticated),
      username: data.username ?? null,
      loading: false,
    };
  } catch {
    return { authenticated: false, username: null, loading: false };
  }
}

export async function login(
  username: string,
  password: string,
  options: LoginOptions = {},
): Promise<{ ok: boolean; error?: string }> {
  try {
    const strongAuth = options.strongAuth ?? true;
    const resp = await fetch(strongAuth ? "/api/auth/login" : "/api/auth/browser-login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(
        strongAuth
          ? { username, password, strong_auth: true, strong_auth_type: "mobile" }
          : { username, password },
      ),
    });
    if (resp.ok) {
      return { ok: true };
    }
    if (resp.status === 401) {
      const data = await resp.json().catch(() => ({ detail: "登录失败" }));
      return { ok: false, error: data.detail ?? "SSO 登录失败：凭证无效" };
    }
    return { ok: false, error: `登录失败 (${resp.status})` };
  } catch (err) {
    return { ok: false, error: "无法连接服务器" };
  }
}

export async function logout(): Promise<void> {
  await fetch("/api/auth/logout", {
    method: "POST",
    credentials: "include",
  });
}

/** 带 credentials 的 fetch 包装，所有需要鉴权的 API 调用走这里。 */
export function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  return fetch(input, {
    ...init,
    credentials: "include",
  });
}
