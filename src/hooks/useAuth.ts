import { useEffect, useState, useSyncExternalStore } from "react";

import { fetchAuthState, type AuthState } from "@/lib/auth";

// ------------------------------------------------------------------ //
// 模块级 auth store —— 登录/登出后主动刷新，所有组件同步更新
// ------------------------------------------------------------------ //

const UNAUTHENTICATED: AuthState = { authenticated: false, username: null, loading: true };

let cachedState: AuthState = UNAUTHENTICATED;
let initialized = false;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) {
    listener();
  }
}

function setState(next: AuthState) {
  cachedState = next;
  emit();
}

/** 强制重新拉取登录状态（登录/登出后调用）。 */
export async function refreshAuth(): Promise<AuthState> {
  const result = await fetchAuthState();
  setState(result);
  return result;
}

/** 清除缓存（登出用）。 */
export function clearAuthCache() {
  setState(UNAUTHENTICATED);
}

function subscribe(callback: () => void) {
  listeners.add(callback);
  // 首次挂载时拉取一次
  if (!initialized) {
    initialized = true;
    fetchAuthState().then(setState);
  }
  return () => {
    listeners.delete(callback);
  };
}

function getSnapshot(): AuthState {
  return cachedState;
}

/**
 * 鉴权 hook：返回当前登录状态，登录/登出后自动同步更新。
 */
export function useAuth(): AuthState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
