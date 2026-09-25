"use client";

import { useCallback, useEffect, useState } from "react";

export const PLAN_REQUIRED = "PLAN_REQUIRED:";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body instanceof FormData ? init?.headers : { "content-type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  const here = typeof window !== "undefined" ? window.location.pathname : "";
  if (res.status === 401 && !path.startsWith("/auth/") && here && here !== "/login" && here !== "/welcome") {
    // A signed-out visitor arriving at the root has never seen the product: show them the landing
    // page. Anywhere deeper, the session expired — sign in, then come back to the same page.
    if (here === "/") window.location.assign("/welcome");
    else window.location.assign(`/login?next=${encodeURIComponent(here + window.location.search)}`);
    return new Promise<T>(() => {}); // never resolves; the page is navigating away
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {}
    const msg = typeof detail === "string" ? detail : JSON.stringify(detail);
    // 402 = the current plan doesn't include this feature; ErrorBox turns it into an upgrade prompt
    throw new Error(res.status === 402 ? `${PLAN_REQUIRED}${msg}` : msg);
  }
  return res.json() as Promise<T>;
}

export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T,>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const patch = <T,>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });

/** Fetch a JSON endpoint; `reload()` refetches. `path = null` skips fetching. */
export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!path) return;
    let alive = true;
    setLoading(true);
    api<T>(path)
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [path, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}
