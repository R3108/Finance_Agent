"use client";

import { useState } from "react";
import { CategoryTrendChart, ForecastChart, WeekdayChart } from "@/components/charts";
import { Badge, Card, ErrorBox, Loading, PageHead, Progress } from "@/components/ui";
import { useApi } from "@/lib/api";
import { useScoped } from "@/lib/session";
import { dateLabel, money } from "@/lib/format";
import type { Anomaly, Forecast, Health, Insight } from "@/lib/types";

export default function Insights() {
  const scoped = useScoped();
  const [days, setDays] = useState(60);
  const health = useApi<Health>(scoped("/health-score"));
  const forecast = useApi<Forecast>(scoped(`/forecast?days=${days}`));
  const anomalies = useApi<Anomaly[]>(scoped("/anomalies?days=180"));
  const trend = useApi<Record<string, number | string>[]>("/spending/category-trend?months=12");
  const weekday = useApi<{ weekday: string; avg_spend: number }[]>("/spending/weekday");
  const merchants = useApi<{ merchant: string; category: string; total: number; transactions: number; average: number }[]>("/spending/merchants?limit=8");
  const insights = useApi<Insight[]>(scoped("/insights"));

  if (health.error) return <ErrorBox error={health.error} />;
  const f = forecast.data;

  return (
    <>
      <PageHead title="Insights" sub="Every figure on this page is computed deterministically from your transactions." />

      <div className="grid cols-3" style={{ marginBottom: 16 }}>
        <Card title="Financial health score">
          {!health.data ? <Loading h={220} /> : (
            <>
              <div className="row" style={{ gap: 18, marginBottom: 14 }}>
                <div className="ring" style={{ ["--v" as string]: health.data.score }}>
                  <div><div><b>{health.data.score}</b><span className="small muted">Grade {health.data.grade}</span></div></div>
                </div>
                <p className="small muted">Weighted blend of savings rate, emergency fund, budget adherence, subscription load and spending stability.</p>
              </div>
              <div className="stack">
                {health.data.components.map((c) => (
                  <div key={c.name} title={c.detail}>
                    <div className="row between small"><span>{c.name} <span className="muted">· {c.weight}%</span></span><b className="num">{c.score}</b></div>
                    <Progress value={c.score} status={c.score >= 70 ? "on_track" : c.score >= 40 ? "at_risk" : "over"} />
                    <div className="small muted" style={{ marginTop: 2 }}>{c.detail}</div>
                  </div>
                ))}
              </div>
            </>
          )}
        </Card>

        <Card title="Cash-flow forecast" className="span-2" hint="Scheduled paychecks and recurring charges plus your average variable spending"
          action={
            <div className="row" role="group" aria-label="Forecast horizon">
              {[30, 60, 90].map((d) => <button key={d} className={`chip ${days === d ? "on" : ""}`} onClick={() => setDays(d)}>{d}d</button>)}
            </div>}>
          {!f ? <Loading h={260} /> : (
            <>
              <div className="grid cols-4" style={{ gap: 10, marginBottom: 10 }}>
                <div><div className="stat-label">Today</div><b className="num">{money(f.starting_balance)}</b></div>
                <div><div className="stat-label">In {f.days} days</div><b className={`num ${f.ending_balance < f.starting_balance ? "neg" : "pos"}`}>{money(f.ending_balance)}</b></div>
                <div><div className="stat-label">Lowest point</div><b className="num">{money(f.lowest_balance)}</b> <span className="small muted">{dateLabel(f.lowest_balance_date)}</span></div>
                <div><div className="stat-label">Variable spend / day</div><b className="num">{money(f.avg_daily_variable_spend)}</b></div>
              </div>
              <ForecastChart f={f} />
            </>
          )}
        </Card>
      </div>

      <div className="grid cols-2" style={{ marginBottom: 16 }}>
        <Card title="Unusual transactions" hint="Outliers, duplicates, unknown merchants and fees · last 180 days">
          {!anomalies.data ? <Loading h={240} /> : anomalies.data.length === 0 ? <p className="muted">Nothing unusual.</p> : anomalies.data.map((a) => (
            <div className="insight" key={a.id}>
              <span className={`dot ${a.severity}`} aria-hidden />
              <div>
                <div className="title">{a.merchant} <span className="muted small">· {dateLabel(a.date)} · {a.category}</span></div>
                <div className="detail">{a.reasons.join(" · ")}</div>
              </div>
              <div className="stack" style={{ alignItems: "flex-end", gap: 4 }}>
                <b className="num">{money(a.amount)}</b>
                <Badge kind={a.severity} />
              </div>
            </div>
          ))}
        </Card>
        <Card title="All insights">
          {!insights.data ? <Loading h={240} /> : insights.data.map((i, idx) => (
            <div className="insight" key={idx}>
              <span className={`dot ${i.severity}`} aria-hidden />
              <div><div className="title">{i.title}</div><div className="detail">{i.detail}</div></div>
              <Badge kind={i.severity} />
            </div>
          ))}
        </Card>
      </div>

      <div className="grid cols-3">
        <Card title="Category mix" hint="Last 12 months" className="span-2">
          {!trend.data ? <Loading h={280} /> : <CategoryTrendChart data={trend.data} />}
        </Card>
        <div className="stack" style={{ gap: 16 }}>
          <Card title="Spending by weekday" hint="Avg per week, excluding bills & subscriptions · 6 months">
            {!weekday.data ? <Loading h={200} /> : <WeekdayChart data={weekday.data} />}
          </Card>
          <Card title="Top merchants" hint="All time">
            {!merchants.data ? <Loading h={200} /> : (
              <table>
                <tbody>
                  {merchants.data.map((m) => (
                    <tr key={m.merchant}>
                      <td>{m.merchant}<div className="small muted">{m.transactions} × avg {money(m.average)}</div></td>
                      <td className="r"><b>{money(m.total)}</b></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

