"use client";

import Link from "next/link";
import { ReactNode, useCallback } from "react";
import { useNotify } from "@/components/feedback";
import { PLAN_REQUIRED } from "@/lib/api";

export function Card({ title, hint, action, children, className = "", id }: {
  title?: ReactNode; hint?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; id?: string;
}) {
  return (
    <section className={`card ${className}`} id={id} style={id ? { scrollMarginTop: 72 } : undefined}>
      {(title || action) && (
        <div className="card-head">
          <div>
            {title && <h2>{title}</h2>}
            {hint && <div className="hint">{hint}</div>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, foot, tone }: { label: string; value: ReactNode; foot?: ReactNode; tone?: "pos" | "neg" }) {
  return (
    <div className="card">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${tone ?? ""}`}>{value}</div>
      {foot && <div className="stat-foot">{foot}</div>}
    </div>
  );
}

export function PageHead({ title, sub, children }: { title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <div className="sub">{sub}</div>}
      </div>
      {children && <div className="row wrap">{children}</div>}
    </div>
  );
}

const SEV_LABEL: Record<string, string> = {
  high: "High", medium: "Medium", low: "Low", info: "Info", over: "Over budget", at_risk: "At risk", on_track: "On track",
};
const SEV_ICON: Record<string, string> = { high: "!", over: "!", medium: "▲", at_risk: "▲", low: "✓", on_track: "✓", info: "i" };

/** Status badge — always icon + label, never color alone. */
export function Badge({ kind, children }: { kind: string; children?: ReactNode }) {
  return (
    <span className={`badge ${kind}`}>
      {SEV_ICON[kind] && <span aria-hidden>{SEV_ICON[kind]}</span>}
      {children ?? SEV_LABEL[kind] ?? kind}
    </span>
  );
}

export function Loading({ h = 120 }: { h?: number }) {
  return <div className="skeleton" style={{ height: h }} aria-label="Loading" />;
}

export function ErrorBox({ error }: { error: string }) {
  if (error.startsWith(PLAN_REQUIRED)) return <UpgradeCard message={error.slice(PLAN_REQUIRED.length)} />;
  return <div className="error">Couldn&apos;t load data: {error}. Is the backend running on port 8000?</div>;
}

export function UpgradeCard({ message }: { message: string }) {
  return (
    <div className="card upgrade">
      <div className="upgrade-mark" aria-hidden>★</div>
      <div>
        <h2>Unlock with Pro</h2>
        <p className="muted" style={{ marginTop: 4 }}>{message}</p>
      </div>
      <Link href="/plans" className="btn primary">See plans</Link>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Progress({ value, status, marker }: { value: number; status?: string; marker?: number }) {
  return (
    <div className="bar" role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100}>
      <span className={status} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
      {marker != null && marker <= 100 && <i className="marker" style={{ left: `${marker}%` }} />}
    </div>
  );
}

/**
 * Page-level toast API, kept stable for every existing page. Toasts now render in the shared stack
 * (components/feedback.tsx), so `node` is always null — pages may still render it harmlessly.
 */
export function useToast() {
  const notify = useNotify();
  const show = useCallback((msg: string | null) => { if (msg) notify(msg); }, [notify]);
  return { show, node: null as ReactNode };
}
