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

// ---- ledger team: keyframes, stage routing, money (types live with the features until contracts regenerate) ----
export const keyframeApi = {
  generate: (shotId: string, force = false) => api(`/shots/${shotId}/keyframe${force ? "?force=true" : ""}`, { method: "POST" }),
  generateEpisode: (projectId: string, n: number, onlyMissing = true) =>
    api<{ jobs: unknown[]; skipped: string[]; error: string | null }>(`/projects/${projectId}/episodes/${n}/keyframes`,
      { method: "POST", json: { only_missing: onlyMissing } }),
  approve: (shotId: string) => api(`/shots/${shotId}/keyframe/approve`, { method: "POST" }),
  reject: (shotId: string) => api(`/shots/${shotId}/keyframe/reject`, { method: "POST" }),
};

export const putStage = (body: { stage: string; model: string | null; resolution?: string | null; note?: string | null }) =>
  api("/admin/stages", { method: "PUT", json: body });

export const putBudget = (projectId: string, budgetUsd: string | null, episode?: number) =>
  api(episode ? `/projects/${projectId}/episodes/${episode}/budget` : `/projects/${projectId}/budget`,
    { method: "PUT", json: { budget_usd: budgetUsd } });

/** Checks the APP_TOKEN and refreshes the HttpOnly /media cookie. Empty token works when auth is off. */
export function login(token: string) {
  return api<{ ok: boolean; expires_at: number; auth_enabled: boolean }>("/auth/login", { method: "POST", json: { token } });
}

// --- remix studio (team/remix): types live here because contracts.ts is regenerated after merge ---
export type RemixMode = "recast" | "restyle" | "replot" | "sequel" | "spinoff" | "condense";
export interface RemixSpec {
  modes: RemixMode[]; recast?: Record<string, string>; style?: string; plot_direction?: string;
  spinoff_focus?: string; condense_ratio?: number; episode_count?: number;
}
export interface RemixSource {
  episode_number: number; status: string; progress: number; sha256: string | null; cache_hit: boolean;
  shots: number | null; kept: number | null; error: string | null; video_path: string;
}
export interface RemixEstimate {
  model: string; price_verified: boolean;
  episodes: { episode_number: number; status: string; shots?: number; images: number | null; images_without_dedupe?: number; usd: number | null }[];
  total: { images: number; batches: number; usd: number | null };
}
export interface RemixProject { id: string; title: string; category: string; status: string; episodes?: number }

const rx = (pid: string) => `/remix/projects/${pid}`;
export const remixApi = {
  sources: (pid: string) => api<RemixSource[]>(`${rx(pid)}/sources`),
  register: (pid: string, episode_number: number, video_path: string) =>
    api<RemixSource>(`${rx(pid)}/sources`, { method: "POST", json: { episode_number, video_path } }),
  remove: (pid: string, ep: number) => fetch(`/api/v1${rx(pid)}/sources/${ep}`, { method: "DELETE" }),
  deconstruct: (pid: string, describe: boolean, episodes?: number[]) =>
    api<{ queued: number[] }>(`${rx(pid)}/deconstruct`, { method: "POST", json: { describe, episodes } }),
  estimate: (pid: string) => api<RemixEstimate>(`${rx(pid)}/estimate`),
  generate: (pid: string, spec: RemixSpec, title?: string) =>
    api<RemixProject>(`${rx(pid)}/generate`, { method: "POST", json: { spec, title: title || undefined } }),
  fork: (source_project_id: string, title?: string, spec?: RemixSpec) =>
    api<RemixProject>("/remix/fork", { method: "POST", json: { source_project_id, title: title || undefined, spec } }),
  markRedesigned: (pid: string, assetId: string) =>
    api<{ redesign_required: boolean }>(`${rx(pid)}/assets/${assetId}/redesigned`, { method: "POST" }),
};
