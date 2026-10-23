"use client";
import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

const TOKEN_KEY = "duanju_token";
export const UNAUTHORIZED_EVENT = "duanju:unauthorized";

export function getToken(): string | null {
  return typeof window === "undefined" ? null : window.localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string | null) {
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

/** Merges the APP_TOKEN bearer (if any) into caller headers; caller headers win except Authorization. */
export function withAuth(headers: HeadersInit | undefined, token: string | null): Record<string, string> {
  const out: Record<string, string> = {};
  new Headers(headers).forEach((v, k) => { out[k] = v; });
  if (token) out["authorization"] = `Bearer ${token}`;
  return out;
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const headers = json !== undefined ? { "content-type": "application/json", ...rest.headers } : rest.headers;
  const res = await fetch(`/api/v1${path}`, {
    ...rest,
    headers: withAuth(headers, getToken()),
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  const data = await res.json().catch(() => null);
  if (res.status === 401 && path !== "/auth/login" && typeof window !== "undefined") {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
  if (!res.ok) {
    const detail = data?.detail;
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail ?? res.statusText));
  }
  return data as T;
}

/** refreshMs: re-fetch on an interval (results of queued renders arrive asynchronously).
 * loading is true only until the first response lands, so callers can tell "still loading" from "really empty". */
export function useApi<T>(path: string | null, refreshMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const reload = useCallback(() => {
    if (!path) return;
    api<T>(path).then(
      (d) => { setData(d); setError(null); setLoading(false); },
      (e: Error) => { setError(e.message); setLoading(false); },
    );
  }, [path]);
  useEffect(reload, [reload]);
  useEffect(() => {
    if (!refreshMs) return;
    const t = setInterval(reload, refreshMs);
    return () => clearInterval(t);
  }, [reload, refreshMs]);
  return { data, error, loading, reload };
}

/** Guards an async action (a credit-spending POST, usually) against a double click.
 * The lock is a ref, checked and set synchronously before any await, so a second click fired in the
 * same tick is rejected even though React hasn't re-rendered the disabled button yet; `busy` mirrors
 * the lock into state so callers can still style/disable the button for the (slower) visual case. */
export function useBusySet<K>() {
  const lock = useRef<Set<K>>(new Set());
  const [busy, setBusy] = useState<Set<K>>(new Set());
  const run = useCallback(async <T,>(key: K, fn: () => Promise<T>): Promise<T | undefined> => {
    if (lock.current.has(key)) return undefined;
    lock.current.add(key);
    setBusy(new Set(lock.current));
    try {
      return await fn();
    } finally {
      lock.current.delete(key);
      setBusy(new Set(lock.current));
    }
  }, []);
  return { busy, run };
}

/** Checks the APP_TOKEN and refreshes the HttpOnly /media cookie. Empty token works when auth is off. */
export function login(token: string) {
  return api<{ ok: boolean; expires_at: number; auth_enabled: boolean }>("/auth/login", { method: "POST", json: { token } });
}
