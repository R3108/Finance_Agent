"use client";

import { useState } from "react";
import { AffordChart } from "@/components/charts";
import { Card, ErrorBox, PageHead, Stat } from "@/components/ui";
import { post } from "@/lib/api";
import { currencyCode, dateLabel, money } from "@/lib/format";
import type { AffordResult } from "@/lib/types";

const VERDICT = {
  comfortable: { mark: "✓", title: "Yes — go for it" },
  tight: { mark: "▲", title: "You can, but it's tight" },
  not_now: { mark: "!", title: "Not right now" },
} as const;

/** Quick picks sized to the user's currency, so a rupee user isn't offered a "$50" example. */
const EXAMPLES: Record<string, { label: string; amount: number; recurring?: boolean }[]> = {
  INR: [{ label: "New phone", amount: 60000 }, { label: "Weekend trip", amount: 18000 },
        { label: "Gym membership", amount: 2500, recurring: true }, { label: "Bike EMI", amount: 6500, recurring: true }],
  default: [{ label: "New laptop", amount: 1500 }, { label: "Weekend trip", amount: 600 },
            { label: "Gym membership", amount: 60, recurring: true }, { label: "Car payment", amount: 450, recurring: true }],
};

export default function Afford() {
  const [form, setForm] = useState({ label: "", amount: "", when: "", recurring: false });
  const [result, setResult] = useState<AffordResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(next = form) {
    if (!Number(next.amount)) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await post<AffordResult>("/afford", {
        amount: Number(next.amount), recurring: next.recurring, label: next.label || null, when: next.when || null,
      }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const examples = EXAMPLES[currencyCode()] ?? EXAMPLES.default;
  const v = result ? VERDICT[result.verdict] : null;

  return (
    <>
      <PageHead title="Can I afford it?"
        sub="Check a purchase against your real cash flow before you buy. Every figure comes from your own transactions." />

      <div className="grid cols-3" style={{ marginBottom: 16, alignItems: "start" }}>
        <Card title="What are you thinking of buying?" className="span-2">
          <form className="stack" onSubmit={(e) => { e.preventDefault(); run(); }}>
            <div className="row wrap">
              <input className="input" placeholder="What is it? (optional)" value={form.label} maxLength={80}
                     onChange={(e) => setForm({ ...form, label: e.target.value })} style={{ flex: "2 1 200px" }} aria-label="What it is" />
              <input className="input" type="number" min="1" step="any" required placeholder="Price" value={form.amount}
                     onChange={(e) => setForm({ ...form, amount: e.target.value })} style={{ flex: "1 1 120px" }} aria-label="Price" />
              <input className="input" type="date" value={form.when} onChange={(e) => setForm({ ...form, when: e.target.value })}
                     aria-label="When you'd buy it (default today)" title="When you'd buy it — leave empty for today" />
            </div>
            <label className="row small">
              <input type="checkbox" checked={form.recurring} onChange={(e) => setForm({ ...form, recurring: e.target.checked })} />
              It&apos;s a monthly cost (subscription, EMI, membership)
            </label>
            <div className="row wrap">
              <button className="btn primary" disabled={busy || !Number(form.amount)}>{busy ? "Checking…" : "Check it"}</button>
              <span className="small muted">Try:</span>
              {examples.map((x) => (
                <button key={x.label} type="button" className="btn sm" onClick={() => {
                  const next = { label: x.label, amount: String(x.amount), when: "", recurring: !!x.recurring };
                  setForm(next);
                  run(next);
                }}>{x.label}</button>
              ))}
            </div>
          </form>
        </Card>
        <Card title="How it decides">
          <ul className="steps small muted">
            <li>Replays your next 90 days — paychecks, bills and your usual day-to-day spending — with the purchase in it.</li>
            <li><b>Comfortable</b>: your balance stays above a month of everyday spending.</li>
            <li><b>Tight</b>: it dips below that, overruns this month&apos;s safe-to-spend, or puts a goal behind.</li>
            <li><b>Not now</b>: your cash would go negative, or a monthly cost exceeds what you usually save.</li>
          </ul>
        </Card>
      </div>

      {error && <ErrorBox error={error} />}

      {result && v && (
        <>
          <div className={`card verdict ${result.verdict}`} style={{ marginBottom: 16 }} role="status">
            <div className="mark" aria-hidden>{v.mark}</div>
            <div>
              <h2>{v.title}</h2>
              <p className="muted small" style={{ marginTop: 2 }}>
                {result.label ? `${result.label} · ` : ""}{money(result.amount)}{result.recurring ? " a month" : ""} on {dateLabel(result.date)}
                {result.annual_cost != null && ` · ${money(result.annual_cost)} a year`}
              </p>
              <ul>{result.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
              {result.wait_until && (
                <p style={{ marginTop: 8 }}>
                  <b>It fits comfortably from {dateLabel(result.wait_until)}.</b>{" "}
                  <span className="muted">Waiting until then keeps your balance above the cushion the whole way.</span>
                </p>
              )}
            </div>
          </div>

          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <Stat label="Lowest balance, next 90 days" value={money(result.lowest_balance_after)}
                  tone={result.lowest_balance_after < 0 ? "neg" : undefined}
                  foot={`${money(result.lowest_balance_before)} without it · ${dateLabel(result.lowest_balance_after_date)}`} />
            <Stat label="Safe to spend this month" value={money(result.safe_to_spend_after)}
                  tone={result.safe_to_spend_after < 0 ? "neg" : undefined}
                  foot={`${money(result.safe_to_spend_before)} without it`} />
            <Stat label="Monthly surplus" value={money(result.monthly_surplus_after)}
                  foot={result.recurring ? `${money(result.monthly_surplus_before)} without it` : "Unchanged — it's a one-off"} />
            <Stat label="Emergency fund" value={result.emergency_fund_months_after != null ? `${result.emergency_fund_months_after} mo` : "—"}
                  foot={result.emergency_fund_months_before != null ? `${result.emergency_fund_months_before} months of spending without it` : undefined} />
          </div>

          <div className="grid cols-3">
            <Card title="Your balance with and without it" hint={`Cushion = ${result.cushion_days} days of your everyday spending (${money(result.cushion)})`} className="span-2">
              <AffordChart r={result} />
            </Card>
            <Card title="Effect on your goals">
              {result.goals.length ? (
                <div className="stack small">
                  {result.goals.map((g) => (
                    <div key={g.goal} className="row between">
                      <span>{g.goal}</span>
                      <span className="num">
                        {g.delay_months ? <b>{g.delay_months} mo later</b> : g.months_after == null ? <b className="neg">out of reach</b> : "no change"}
                        {g.on_track_before && !g.on_track_after && <span className="neg"> · falls behind</span>}
                      </span>
                    </div>
                  ))}
                </div>
              ) : <p className="muted small">No open savings goals. Add one on Goals &amp; What-if to see the trade-off.</p>}
            </Card>
          </div>
        </>
      )}
    </>
  );
}
