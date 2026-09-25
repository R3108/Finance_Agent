"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Tilt } from "@/components/motion";
import { api, post } from "@/lib/api";
import { inr } from "@/lib/format";

/** Starts a private demo sandbox (INR persona) and drops the visitor straight into the app. */
export function DemoButton({ className = "btn lg" }: { className?: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <button className={className} disabled={busy} onClick={async () => {
        setBusy(true);
        setError(null);
        try {
          await post("/auth/demo?currency=INR");
          router.replace("/");
        } catch (err) {
          setError((err as Error).message);
          setBusy(false);
        }
      }}>{busy ? "Preparing your demo…" : "Try the live demo"}</button>
      {error && <span className="small" role="alert">{error}</span>}
    </>
  );
}

type PublicPlan = {
  id: string; name: string; tagline: string; price_monthly: number; price_annual: number; annual_saving_pct: number;
  features: { key: string; label: string; included: boolean; tier: string }[];
};

/** What each tier adds, in the words a buyer compares on — the full list lives on the in-app Plans page. */
const HEADLINES: Record<string, string[]> = {
  free: ["sms_capture", "afford", "splits", "subscriptions", "budgets", "ai_assistant", "two_factor"],
  pro: ["sms_autocapture", "unlimited_ai", "alert_email", "digest", "debt_planner", "freelancer", "receipts"],
  family: ["household"],
};

/** Prices come from the API (plans.py) so the landing page can never quote a stale price. */
export function Pricing() {
  const [plans, setPlans] = useState<PublicPlan[] | null>(null);
  const [annual, setAnnual] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    api<{ plans: PublicPlan[] }>("/plans/public").then((r) => setPlans(r.plans)).catch(() => setFailed(true));
  }, []);

  if (failed) return <p className="muted" style={{ textAlign: "center" }}>Pricing is loading slowly — <Link className="link" href="/login?mode=signup">start free</Link> and see plans inside.</p>;
  if (!plans) return <div className="lp-plans">{[0, 1, 2].map((i) => <div key={i} className="skeleton" style={{ height: 360 }} />)}</div>;

  return (
    <>
      <div className="lp-toggle" role="group" aria-label="Billing period">
        <button className={!annual ? "on" : ""} aria-pressed={!annual} onClick={() => setAnnual(false)}>Monthly</button>
        <button className={annual ? "on" : ""} aria-pressed={annual} onClick={() => setAnnual(true)}>
          Yearly <span className="badge good">save {plans.find((p) => p.id === "pro")?.annual_saving_pct ?? 0}%</span>
        </button>
      </div>
      <div className="lp-plans">
        {plans.map((p) => {
          const label = (k: string) => p.features.find((f) => f.key === k)?.label;
          const price = annual ? p.price_annual : p.price_monthly;
          return (
            <Tilt key={p.id} max={4} className={`card lp-plan ${p.id === "pro" ? "featured" : ""}`}>
              {p.id === "pro" && <span className="badge accent lp-ribbon">Most popular</span>}
              <h3>{p.name}</h3>
              <p className="muted small">{p.tagline}</p>
              {/* keyed on the period so switching Monthly/Yearly replays the price's entrance */}
              <div className="lp-price" key={annual ? "year" : "month"}>
                {price ? inr(price) : "₹0"}
                <span className="muted small"> / {annual ? "year" : "month"}</span>
              </div>
              <Link className={`btn ${p.id === "pro" ? "primary" : ""} lp-cta`} href="/login?mode=signup">
                {p.id === "free" ? "Start free" : `Start with ${p.name}`}
              </Link>
              <ul>
                {p.id !== "free" && <li className="muted">Everything in {p.id === "pro" ? "Free" : "Pro"}, plus:</li>}
                {(HEADLINES[p.id] ?? []).map((k) => label(k) && <li key={k}><span aria-hidden>✓</span> {label(k)}</li>)}
              </ul>
            </Tilt>
          );
        })}
      </div>
      <p className="small muted" style={{ textAlign: "center", marginTop: 12 }}>
        Prices in INR, taxes as applicable. Pro includes a 14-day free trial, no card needed. Cancel any time.
      </p>
    </>
  );
}
