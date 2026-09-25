"use client";

import { useState } from "react";
import { CategoryBars } from "@/components/charts";
import { Badge, Card, ErrorBox, Loading, PageHead, Stat } from "@/components/ui";
import { useApi } from "@/lib/api";
import { dateLabel, money, monthLabel, pct } from "@/lib/format";
import type { Anomaly, Breakdown, BudgetStatus, MonthRow } from "@/lib/types";

type Report = {
  month: string; summary: MonthRow; categories: Breakdown;
  top_merchants: { merchant: string; category: string; total: number; transactions: number }[];
  budgets: BudgetStatus | null;
  subscriptions: { active_subscriptions: number; subscriptions_monthly_total: number };
  anomalies: Anomaly[];
};

export default function ReportPage() {
  const months = useApi<MonthRow[]>("/spending/monthly?months=13");
  const [month, setMonth] = useState<string>("");
  const { data, error } = useApi<Report>(`/report${month ? `?month=${month}` : ""}`);

  if (error) return <ErrorBox error={error} />;
  return (
    <>
      <PageHead title={data ? `Monthly report · ${monthLabel(data.month)}` : "Monthly report"} sub="A printable one-page summary — share it or save as PDF.">
        <select className="select no-print" value={month || data?.month || ""} onChange={(e) => setMonth(e.target.value)} aria-label="Month">
          {months.data?.filter((m) => !m.partial).reverse().map((m) => <option key={m.month} value={m.month}>{monthLabel(m.month)}</option>)}
        </select>
        <button className="btn no-print" onClick={() => window.print()}>Print / PDF</button>
      </PageHead>
      {!data ? <Loading h={400} /> : (
        <>
          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <Stat label="Income" value={money(data.summary.income)} />
            <Stat label="Spending" value={money(data.summary.spending)} />
            <Stat label="Net" value={money(data.summary.net)} tone={data.summary.net < 0 ? "neg" : "pos"} />
            <Stat label="Savings rate" value={pct(data.summary.savings_rate_pct)} foot="Target 20%+" />
          </div>
          <div className="grid cols-2" style={{ marginBottom: 16 }}>
            <Card title="Spending by category"><CategoryBars rows={data.categories.categories} max={10} /></Card>
            <div className="stack" style={{ gap: 16 }}>
              <Card title="Top merchants">
                <table><tbody>
                  {data.top_merchants.map((m) => <tr key={m.merchant}><td>{m.merchant} <span className="small muted">· {m.transactions}×</span></td><td className="r"><b>{money(m.total)}</b></td></tr>)}
                </tbody></table>
              </Card>
              <Card title="Subscriptions">
                <b className="num">{money(data.subscriptions.subscriptions_monthly_total)}</b>/mo across {data.subscriptions.active_subscriptions} active subscriptions.
              </Card>
            </div>
          </div>
          <div className="grid cols-2">
            <Card title="Budgets">
              {!data.budgets || data.budgets.items.length === 0 ? <p className="muted">No budgets set.</p> : (
                <table>
                  <thead><tr><th>Category</th><th className="r">Spent</th><th className="r">Limit</th><th>Status</th></tr></thead>
                  <tbody>{data.budgets.items.map((b) => <tr key={b.category}><td>{b.category}</td><td className="r">{money(b.spent)}</td><td className="r">{money(b.limit)}</td><td><Badge kind={b.status} /></td></tr>)}</tbody>
                </table>
              )}
            </Card>
            <Card title="Flagged transactions">
              {data.anomalies.length === 0 ? <p className="muted">Nothing unusual this month.</p> : (
                <table><tbody>
                  {data.anomalies.map((a) => <tr key={a.id}><td>{a.merchant}<div className="small muted">{dateLabel(a.date)} · {a.reasons[0]}</div></td><td className="r">{money(a.amount)}</td></tr>)}
                </tbody></table>
              )}
            </Card>
          </div>
        </>
      )}
    </>
  );
}
