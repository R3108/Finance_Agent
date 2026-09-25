"use client";

import { useState } from "react";
import { MonthlySpendChart } from "@/components/charts";
import { CountUp } from "@/components/motion";
import { Card, ErrorBox, Loading, PageHead, useToast } from "@/components/ui";
import { useApi } from "@/lib/api";
import { dateLabel, money, monthLabel, pct } from "@/lib/format";
import type { Wrapped } from "@/lib/types";

function Tile({ label, big, foot }: { label: string; big: React.ReactNode; foot?: React.ReactNode }) {
  return (
    <div className="card wrap-tile">
      <div className="stat-label">{label}</div>
      <div className="big">{big}</div>
      {foot && <div className="stat-foot">{foot}</div>}
    </div>
  );
}

export default function WrappedPage() {
  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState<number | null>(null);
  const w = useApi<Wrapped>(`/wrapped${year ? `?year=${year}` : ""}`);
  const toast = useToast();

  async function share() {
    if (!w.data) return;
    try {
      await navigator.clipboard.writeText(w.data.share_text);
      toast.show("Summary copied — no amounts included");
    } catch {
      toast.show("Couldn't access the clipboard");
    }
  }

  if (w.error) return <ErrorBox error={w.error} />;
  const d = w.data;

  return (
    <>
      <PageHead title="Money Wrapped" sub="Your year in money: habits, highlights and the persona behind the numbers.">
        <select className="select" value={year ?? ""} onChange={(e) => setYear(e.target.value ? Number(e.target.value) : null)} aria-label="Period">
          <option value="">Last 12 months</option>
          {[thisYear, thisYear - 1].map((y) => <option key={y} value={y}>{y}</option>)}
        </select>
        <button className="btn" onClick={share} disabled={!d}>Copy share text</button>
        <button className="btn no-print" onClick={() => window.print()}>Print</button>
      </PageHead>

      {!d ? <Loading h={400} /> : (
        <>
          <div className="wrap-hero">
            <div className="eyebrow">{d.label} · {dateLabel(d.start)} – {dateLabel(d.end)}</div>
            <h2>You&apos;re {d.persona}</h2>
            <p style={{ opacity: 0.92, maxWidth: 620 }}>{d.persona_tagline}</p>
          </div>

          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <Tile label="Earned" big={<CountUp value={d.income} format={money} />} />
            <Tile label="Spent" big={<CountUp value={d.spending} format={money} />}
              foot={d.spending_change_pct != null ? `${d.spending_change_pct > 0 ? "▲" : "▼"} ${Math.abs(d.spending_change_pct)}% vs the period before` : undefined} />
            <Tile label="Saved" big={<span className={d.saved >= 0 ? "pos" : "neg"}><CountUp value={d.saved} format={money} /></span>} foot={`Savings rate ${pct(d.savings_rate_pct)}`} />
            <Tile label="No-spend days" big={<CountUp value={d.no_spend_days} />} foot={`of ${d.days} · longest streak ${d.longest_no_spend_streak} days`} />
          </div>

          <div className="grid cols-3" style={{ marginBottom: 16 }}>
            {d.most_visited && <Tile label="Your regular spot" big={d.most_visited.name} foot={`${d.most_visited.visits} visits · ${money(d.most_visited.amount)}`} />}
            {d.top_merchant && <Tile label="Where the most money went" big={d.top_merchant.name} foot={`${money(d.top_merchant.amount)} across ${d.top_merchant.visits} purchases`} />}
            {d.biggest_purchase && <Tile label="Biggest single purchase" big={money(d.biggest_purchase.amount)} foot={`${d.biggest_purchase.merchant} · ${dateLabel(d.biggest_purchase.date)}`} />}
            <Tile label="Coffee runs" big={<CountUp value={d.coffee.visits} />} foot={`${money(d.coffee.amount)} in total`} />
            <Tile label="Delivery orders" big={<CountUp value={d.delivery.orders} />} foot={`${money(d.delivery.amount)} in total`} />
            <Tile label="Subscriptions" big={money(d.subscriptions_total)} foot={`${d.unique_merchants} different merchants overall`} />
          </div>

          <div className="grid cols-3">
            <Card title="Month by month" className="span-2"
              hint={d.priciest_month && d.leanest_month ? `Priciest: ${monthLabel(d.priciest_month.month)} (${money(d.priciest_month.amount)}) · Leanest: ${monthLabel(d.leanest_month.month)} (${money(d.leanest_month.amount)})` : undefined}>
              <MonthlySpendChart data={d.monthly} />
            </Card>
            <Card title="Fun facts">
              <div className="stack small">
                {d.top_category && <p>Your biggest category was <b>{d.top_category.name}</b> at {pct(d.top_category.share_pct)} of spending.</p>}
                {d.top_discretionary_category && <p>Your top indulgence: <b>{d.top_discretionary_category.name}</b> ({money(d.top_discretionary_category.amount)}).</p>}
                {d.busiest_weekday && <p>You spend the most on <b>{d.busiest_weekday}s</b>.</p>}
              </div>
            </Card>
          </div>
        </>
      )}
      {toast.node}
    </>
  );
}
