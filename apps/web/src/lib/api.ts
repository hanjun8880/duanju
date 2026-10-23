"use client";
import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(`/api/v1${path}`, {
    ...rest,
    headers: json !== undefined ? { "content-type": "application/json", ...rest.headers } : rest.headers,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  const data = await res.json().catch(() => null);
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

// --- look-dev (team/lookdev) ---
export type LookdevKind = "portrait" | "turnaround" | "expressions" | "costume";
export type LookdevJob = { id: string; status: string; credit_estimate: string; cached: boolean; kind: LookdevKind };
export type LookdevEstimate = { kind: LookdevKind; images: number; usd: number; cached: boolean };

export function lookdevGenerate(id: string, kind: LookdevKind = "portrait", note?: string, force = false) {
  return api<LookdevJob>(`/lookdev/${id}/generate`, { method: "POST", json: { kind, note, force } });
}
export function lookdevEstimate(id: string, kind: LookdevKind, note?: string) {
  const q = new URLSearchParams({ kind, ...(note ? { note } : {}) });
  return api<LookdevEstimate>(`/lookdev/${id}/estimate?${q}`);
}
export function lookdevLock(id: string, urls: string[]) {
  return api<{ locked_ref_urls: string[] }>(`/lookdev/${id}/lock`, { method: "POST", json: { urls } });
}
export function lookdevUnlock(id: string, urls?: string[]) {
  return api<{ locked_ref_urls: string[] }>(`/lookdev/${id}/unlock`, { method: "POST", json: urls ? { urls } : {} });
}
