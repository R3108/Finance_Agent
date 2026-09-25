"use client";

/**
 * First-run experience. Two pieces:
 *
 * - `EmptyDashboard` replaces the dashboard for an account with no transactions yet. A brand-new
 *   user sees three ways to get data in, not a wall of ₹0 charts.
 * - `GettingStarted` is a checklist that sits on the dashboard until it's done or dismissed. Every
 *   tick comes from real data (`/api/onboarding`), so it can't be "completed" by clicking around.
 */
import Link from "next/link";
import { useEffect, useState } from "react";
import { useConfirm, useNotify } from "@/components/feedback";
import Icon from "@/components/Icon";
import { Badge, Progress } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import { useMe } from "@/lib/session";

type Onboarding = {
  steps: Record<"transactions" | "auto_capture" | "budgets" | "goals" | "email_verified" | "two_factor", boolean>;
  done: number; total: number; is_demo: boolean; auto_capture_allowed: boolean;
};

const STEPS: { key: keyof Onboarding["steps"]; title: string; body: string; href?: string; cta?: string; pro?: boolean }[] = [
  { key: "transactions", title: "Add your transactions", body: "Paste a few bank SMS alerts, or import a statement CSV.", href: "/capture", cta: "Add now" },
  { key: "auto_capture", title: "Track automatically", body: "Let your phone forward bank SMS so you never import again.", href: "/capture", cta: "Set up", pro: true },
  { key: "budgets", title: "Set your budgets", body: "One click applies limits suggested from your own spending.", href: "/budgets", cta: "Set budgets" },
  { key: "goals", title: "Add a savings goal", body: "See if you're on track, and what cutting back would change.", href: "/goals", cta: "Add a goal" },
  { key: "email_verified", title: "Confirm your email", body: "Use the link we emailed you — it protects account recovery." },
  { key: "two_factor", title: "Turn on two-factor sign-in", body: "A code from your phone keeps your finances private.", href: "/settings", cta: "Turn on" },
];

const dismissKey = (id: number) => `ledgerly.onboarding.dismissed.${id}`;

export function GettingStarted() {
  const { me } = useMe();
  const { data } = useApi<Onboarding>("/onboarding");
  const [dismissed, setDismissed] = useState(true);   // assume hidden until storage is read: no flash

  useEffect(() => {
    if (!me) return;
    try { setDismissed(localStorage.getItem(dismissKey(me.id)) === "1"); } catch { setDismissed(false); }
  }, [me]);

  if (!me || !data || data.is_demo || dismissed || data.done === data.total) return null;
  const next = STEPS.find((s) => !data.steps[s.key]);

  return (
    <section className="card onboarding" aria-labelledby="gs-title" style={{ marginBottom: 16 }}>
      <div className="card-head">
        <div>
          <h2 id="gs-title">Get set up <span className="muted small">· {data.done} of {data.total} done</span></h2>
          <div className="hint">A few minutes now and Ledgerly runs itself.</div>
        </div>
        <button className="btn sm" onClick={() => {
          try { localStorage.setItem(dismissKey(me.id), "1"); } catch {}
          setDismissed(true);
        }}>Hide</button>
      </div>
      <Progress value={(data.done / data.total) * 100} status="on_track" />
      <ol className="checklist">
        {STEPS.map((s) => {
          const done = data.steps[s.key];
          return (
            <li key={s.key} className={`${done ? "done" : ""} ${next?.key === s.key ? "next" : ""}`}>
              <span className="check" aria-hidden>{done ? "✓" : ""}</span>
              <div>
                <b>{s.title}</b> {s.pro && !data.auto_capture_allowed && !done && <Badge kind="accent">Pro</Badge>}
                <div className="small muted">{s.body}</div>
              </div>
              {!done && s.href && (
                <Link href={s.pro && !data.auto_capture_allowed ? "/plans" : s.href}
                      className={`btn sm ${next?.key === s.key ? "primary" : ""}`}>
                  {s.pro && !data.auto_capture_allowed ? "See Pro" : s.cta}
                </Link>
              )}
              <span className="sr-only">{done ? "Done" : "Not done yet"}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function EmptyDashboard() {
  const { me, refresh } = useMe();
  const confirm = useConfirm();
  const notify = useNotify();
  const [busy, setBusy] = useState(false);

  async function sample() {
    if (!(await confirm({
      title: "Fill your account with sample data?",
      body: "You'll get 24 months of realistic transactions to explore. Replace it with your own any time from Data & settings.",
      confirmLabel: "Load sample data",
    }))) return;
    setBusy(true);
    try {
      await post("/demo/reset");
      notify("Sample data loaded — have a look around", { tone: "success" });
      refresh();
    } catch (err) {
      notify((err as Error).message, { tone: "error" });
      setBusy(false);
    }
  }

  const options = [
    { icon: "M4 5h16v11H8l-4 4zM8 9h8M8 12h5", title: "Paste bank SMS", body: "Copy a few UPI or card alerts from your messages. Fastest way to start.", href: "/capture", cta: "Paste SMS" },
    { icon: "M12 3v12M7 10l5 5 5-5M4 21h16", title: "Import a statement", body: "Download a CSV from your bank's website and drop it in.", href: "/settings#import", cta: "Import CSV" },
  ];

  return (
    <>
      <div className="welcome-hero">
        <h1>Welcome{me?.name ? `, ${me.name.split(" ")[0]}` : ""} 👋</h1>
        <p>Let&apos;s get your money in one place. Pick whichever is easiest — you can add the others later.</p>
      </div>
      <div className="grid cols-3" style={{ marginBottom: 16 }}>
        {options.map((o) => (
          <Link key={o.title} href={o.href} className="card start-option">
            <div className="lp-icon"><Icon d={o.icon} size={20} /></div>
            <h2>{o.title}</h2>
            <p className="muted small">{o.body}</p>
            <span className="btn primary sm" style={{ alignSelf: "flex-start" }}>{o.cta}</span>
          </Link>
        ))}
        <button className="card start-option" onClick={sample} disabled={busy} style={{ textAlign: "left", cursor: "pointer" }}>
          <div className="lp-icon"><Icon d="M12 3l2.5 5.5L20 9l-4 4 1 6-5-3-5 3 1-6-4-4 5.5-.5z" size={20} /></div>
          <h2>Explore with sample data</h2>
          <p className="muted small">See every feature working on 24 months of realistic data first.</p>
          <span className="btn sm" style={{ alignSelf: "flex-start" }}>{busy ? "Loading…" : "Load sample data"}</span>
        </button>
      </div>
      <GettingStarted />
    </>
  );
}
