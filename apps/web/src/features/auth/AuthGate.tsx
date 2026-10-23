"use client";
import { useEffect, useState, type ReactNode } from "react";
import { ApiError, UNAUTHORIZED_EVENT, getToken, login, setToken } from "@/lib/api";

type State = "checking" | "in" | "out";

/** Shows the workbench only after the APP_TOKEN checks out (or the server has auth off).
 * Every mount re-runs the login so the 12h /media cookie is refreshed while the token is kept. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>("checking");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    login(getToken() ?? "").then(() => setState("in"), () => { setToken(null); setState("out"); });
    const out = () => { setToken(null); setState("out"); };
    window.addEventListener(UNAUTHORIZED_EVENT, out);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, out);
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await login(value.trim());
      setToken(value.trim());
      setState("in");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "口令不对" : `无法连接后端：${(err as Error).message}`);
    }
  }

  if (state === "checking") return <p className="p-8 text-ink-soft">正在连接…</p>;
  if (state === "in") return <>{children}</>;
  return (
    <main className="mx-auto mt-24 max-w-sm px-4">
      <h1 className="mb-6 text-3xl font-black tracking-[0.3em] text-ink">长卷</h1>
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-sm text-ink-soft">访问口令（服务器的 APP_TOKEN）
          <input className="field mt-1 w-full" type="password" autoComplete="current-password" autoFocus
            value={value} onChange={(e) => setValue(e.target.value)} />
        </label>
        {error && <p className="text-sm text-seal" role="alert">{error}</p>}
        <button className="btn" disabled={!value.trim()}>进入</button>
      </form>
    </main>
  );
}
