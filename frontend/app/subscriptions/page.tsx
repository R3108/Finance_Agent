"use client";

import { useMemo, useState } from "react";
import { Badge, Card, ErrorBox, Loading, PageHead, Stat, useToast } from "@/components/ui";
import { put, useApi } from "@/lib/api";
import { useScoped } from "@/lib/session";
import { dateLabel, money } from "@/lib/format";
import type { SubFlag, Subscription } from "@/lib/types";

const STATUSES = [
  { v: "keep", l: "Keep" },
  { v: "cancel_planned", l: "Plan to cancel" },
  { v: "cancelled", l: "Cancelled" },
  { v: "ignored", l: "Not a subscription" },
];
const CADENCE: Record<string, string> = { weekly: "Weekly", biweekly: "Every 2 wks", four_weekly: "Every 4 wks", monthly: "Monthly", quarterly: "Quarterly", semiannual: "Every 6 mo", annual: "Yearly" };

export default function Subscriptions() {
  const scoped = useScoped();
  const subs = useApi<{ items: Subscription[]; active_subscriptions: number; subscriptions_monthly_total: number; subscriptions_annual_total: number; bills_monthly_total: number }>(scoped("/subscriptions"));
  const flags = useApi<SubFlag[]>(scoped("/subscriptions/unusual"));
  const [showInactive, setShowInactive] = useState(false);
  const toast = useToast();

  const planned = useMemo(() => (subs.data?.items ?? []).filter((s) => s.active && s.status === "cancel_planned"), [subs.data]);
  const plannedSavings = planned.reduce((a, s) => a + Math.round(s.monthly_cost * 100), 0) / 100;

  async function setStatus(s: Subscription, status: string) {
    await put("/subscriptions/status", { key: s.key, status });
    toast.show(`${s.merchant}: ${STATUSES.find((x) => x.v === status)?.l}`);
    subs.reload();
  }

  if (subs.error) return <ErrorBox error={subs.error} />;
  const d = subs.data;
  const rows = (d?.items ?? []).filter((s) => showInactive || s.active);
  const flaggedKeys = new Set((flags.data ?? []).flatMap((f) => (f.key ? [f.key] : [])));
  const flaggedMerchants = new Set((flags.data ?? []).flatMap((f) => f.merchants));
  const totalSavings = (flags.data ?? []).reduce((a, f) => a + Math.round(f.potential_monthly_savings * 100), 0) / 100;

  return (
    <>
      <PageHead title="Subscriptions & bills" sub="Detected automatically from charge cadence and amount stability — no manual setup." />
      {!d ? <Loading h={100} /> : (
        <div className="grid cols-4" style={{ marginBottom: 16 }}>
          <Stat label="Active subscriptions" value={d.active_subscriptions} foot={`${money(d.subscriptions_annual_total)} per year`} />
          <Stat label="Subscriptions / month" value={money(d.subscriptions_monthly_total)} />
          <Stat label="Recurring bills / month" value={money(d.bills_monthly_total)} foot="Rent, utilities, insurance" />
          <Stat label="Potential savings found" value={money(totalSavings)} tone={totalSavings > 0 ? "pos" : undefined}
            foot={planned.length ? `${money(plannedSavings)}/mo from ${planned.length} planned cancellations` : "per month"} />
        </div>
      )}

      <Card title="Unusual subscriptions" hint="Price hikes, duplicates, overlaps, new sign-ups, renewals" className="" >
        {!flags.data ? <Loading /> : flags.data.length === 0 ? <p className="muted">Nothing unusual. 🎉</p> : flags.data.map((f, i) => (
          <div className="insight" key={i}>
            <span className={`dot ${f.severity}`} aria-hidden />
            <div>
              <div className="title">{f.title}</div>
              <div className="detail">{f.detail}</div>
            </div>
            <div className="stack" style={{ alignItems: "flex-end", gap: 4 }}>
              <Badge kind={f.severity} />
              {f.potential_monthly_savings > 0 && <span className="small pos num">save {money(f.potential_monthly_savings)}/mo</span>}
            </div>
          </div>
        ))}
      </Card>

      <div style={{ height: 16 }} />
      <Card title="All recurring charges" action={
        <label className="row small muted" style={{ gap: 6 }}>
          <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> Show stopped
        </label>}>
        {!d ? <Loading h={300} /> : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Merchant</th><th>Type</th><th>Cadence</th><th className="r">Last charge</th><th className="r">Monthly</th><th className="r">Yearly</th><th>Next</th><th>Status</th></tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.key} style={{ opacity: s.active ? 1 : 0.55 }}>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        {s.merchant}
                        {(flaggedKeys.has(s.key) || flaggedMerchants.has(s.merchant)) && s.active && <Badge kind="medium">Review</Badge>}
                      </div>
                      <div className="small muted">{s.account} · since {dateLabel(s.first_charge)}</div>
                    </td>
                    <td><span className="badge">{s.kind === "bill" ? "Bill" : s.kind === "recurring" ? "Recurring" : "Subscription"}</span></td>
                    <td className="small">{CADENCE[s.cadence] ?? s.cadence}</td>
                    <td className="r">
                      {money(s.last_amount)}
                      {s.price_changed_on && s.previous_price !== s.last_amount && (
                        <div className="small muted">was {money(s.previous_price)}</div>
                      )}
                    </td>
                    <td className="r">{money(s.monthly_cost)}</td>
                    <td className="r">{money(s.annual_cost)}</td>
                    <td className="small">{s.next_expected ? dateLabel(s.next_expected) : <span className="muted">Stopped</span>}</td>
                    <td>
                      <select className="select" value={s.status} onChange={(e) => setStatus(s, e.target.value)} style={{ padding: "3px 6px", fontSize: 12.5 }} aria-label={`Status for ${s.merchant}`}>
                        {STATUSES.map((x) => <option key={x.v} value={x.v}>{x.l}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {toast.node}
    </>
  );
}
