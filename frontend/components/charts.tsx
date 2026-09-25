"use client";

import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { useReducedMotion } from "@/components/motion";
import { money, money0, moneyCompact, monthLabel, shortDate } from "@/lib/format";
import type { AffordResult, CategoryRow, DebtPlan, Forecast, MonthRow, NetWorth } from "@/lib/types";

const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)", "var(--s7)", "var(--s8)"];
const axis = { stroke: "var(--axis)", tickLine: false, tick: { fill: "var(--muted)", fontSize: 11 } };
// the user's currency, not a hard-coded "$": a rupee ledger's axis reads ₹12K
const kfmt = (v: number) => moneyCompact(v);

/** Bars grow and lines draw in on first render and on data change; off entirely under reduced motion. */
function useChartMotion() {
  const reduced = useReducedMotion();
  return { isAnimationActive: !reduced, animationDuration: 800, animationEasing: "ease-out" as const };
}

type TipProps = { active?: boolean; payload?: { name: string; value: number; color?: string; fill?: string; payload?: Record<string, unknown> }[]; label?: string };

function Tip({ active, payload, label, labelFmt }: TipProps & { labelFmt?: (l: string) => string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tooltip">
      <div className="t">{labelFmt ? labelFmt(String(label)) : label}</div>
      {payload.map((p) => (
        <div key={p.name} className="row between" style={{ gap: 16 }}>
          <span><i style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: p.color ?? p.fill, marginRight: 6 }} />{p.name}</span>
          <b className="num">{money(p.value)}</b>
        </div>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <div className="legend">
      {items.map((i) => (
        <span key={i.label}><i style={{ background: i.color }} />{i.label}</span>
      ))}
    </div>
  );
}

/** Income vs spending per month — grouped bars, two series. */
export function IncomeSpendChart({ data }: { data: MonthRow[] }) {
  const motion = useChartMotion();
  return (
    <>
      <Legend items={[{ label: "Income", color: SERIES[0] }, { label: "Spending", color: SERIES[1] }]} />
      <div style={{ height: 260, marginTop: 8 }}>
        <ResponsiveContainer>
          <BarChart data={data} barGap={2} barCategoryGap="22%">
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="month" {...axis} tickFormatter={monthLabel} />
            <YAxis {...axis} axisLine={false} tickFormatter={kfmt} width={48} />
            <Tooltip content={<Tip labelFmt={(l) => monthLabel(l) + (data.find((d) => d.month === l)?.partial ? " (so far)" : "")} />} cursor={{ fill: "var(--surface-2)" }} />
            <Bar dataKey="income" name="Income" fill={SERIES[0]} radius={[4, 4, 0, 0]} maxBarSize={18} {...motion} />
            <Bar dataKey="spending" name="Spending" fill={SERIES[1]} radius={[4, 4, 0, 0]} maxBarSize={18} {...motion} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}

/** Ranked category magnitudes — single hue horizontal bars built in HTML, with direct value labels. */
export function CategoryBars({ rows, max = 8 }: { rows: CategoryRow[]; max?: number }) {
  const top = rows.slice(0, max);
  const peak = Math.max(1, ...top.map((r) => r.amount));
  return (
    <div className="stack" style={{ gap: 12 }}>
      {top.map((r, i) => {
        const delta = r.vs_six_month_avg_pct;
        return (
          <div key={r.category} title={`${r.category}: ${money(r.amount)} · typical by now ${money(r.expected_to_date)} · 6-mo avg ${money(r.six_month_avg)}/mo`}>
            <div className="row between small" style={{ marginBottom: 4 }}>
              <span style={{ color: "var(--ink)" }}>{r.category}</span>
              <span className="num">
                <b>{money(r.amount)}</b>
                {delta != null && Math.abs(delta) >= 10 && (
                  <span className="muted" style={{ marginLeft: 6 }}>{delta > 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(0)}% vs typical</span>
                )}
              </span>
            </div>
            <div className="bar">
              <span style={{ width: `${(r.amount / peak) * 100}%`, background: SERIES[0], animationDelay: `${i * 60}ms` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Cash-flow projection — single series area with a zero line. */
export function ForecastChart({ f }: { f: Forecast }) {
  const data = [{ date: f.as_of, balance: f.starting_balance, events: [] }, ...f.series];
  const motion = useChartMotion();
  return (
    <div style={{ height: 260 }}>
      <ResponsiveContainer>
        <AreaChart data={data}>
          <defs>
            <linearGradient id="fc" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--s1)" stopOpacity={0.22} />
              <stop offset="100%" stopColor="var(--s1)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="date" {...axis} tickFormatter={shortDate} minTickGap={40} />
          <YAxis {...axis} axisLine={false} tickFormatter={kfmt} width={52} domain={["auto", "auto"]} />
          <ReferenceLine y={0} stroke="var(--axis)" />
          <Tooltip
            cursor={{ stroke: "var(--axis)" }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              const p = payload[0].payload as Forecast["series"][number];
              return (
                <div className="tooltip">
                  <div className="t">{shortDate(String(label))}</div>
                  <div>Projected balance <b className="num">{money(p.balance)}</b></div>
                  {p.events?.map((e, i) => (
                    <div key={i} className="small muted">{e.name}: {money(e.amount)}</div>
                  ))}
                </div>
              );
            }}
          />
          <Area type="monotone" dataKey="balance" name="Projected balance" stroke="var(--s1)" strokeWidth={2} fill="url(#fc)" {...motion} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Category mix over time — stacked bars, top 5 categories + Other (color follows category, fixed order). */
export function CategoryTrendChart({ data }: { data: Record<string, number | string>[] }) {
  const totals: Record<string, number> = {};
  data.forEach((row) => Object.entries(row).forEach(([k, v]) => k !== "month" && (totals[k] = (totals[k] ?? 0) + Number(v))));
  const top = Object.entries(totals).filter(([k]) => k !== "Housing").sort((a, b) => b[1] - a[1]).slice(0, 5).map(([k]) => k);
  const rows = data.map((row) => {
    const out: Record<string, number | string> = { month: row.month };
    let other = 0;
    Object.entries(row).forEach(([k, v]) => {
      if (k === "month" || k === "Housing") return;
      if (top.includes(k)) out[k] = Number(v);
      else other += Number(v);
    });
    out.Other = Math.round(other * 100) / 100;
    return out;
  });
  const keys = [...top, "Other"];
  const color = (k: string, i: number) => (k === "Other" ? "var(--axis)" : SERIES[i]);
  const motion = useChartMotion();
  return (
    <>
      <Legend items={keys.map((k, i) => ({ label: k, color: color(k, i) }))} />
      <div style={{ height: 260, marginTop: 8 }}>
        <ResponsiveContainer>
          <BarChart data={rows} barCategoryGap="28%">
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="month" {...axis} tickFormatter={monthLabel} />
            <YAxis {...axis} axisLine={false} tickFormatter={kfmt} width={48} />
            <Tooltip content={<Tip labelFmt={monthLabel} />} cursor={{ fill: "var(--surface-2)" }} />
            {keys.map((k, i) => (
              <Bar key={k} dataKey={k} name={k} stackId="a" fill={color(k, i)} stroke="var(--surface)" strokeWidth={1}
                radius={i === keys.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]} maxBarSize={34} {...motion} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="small muted" style={{ marginTop: 6 }}>Housing excluded so variable categories stay readable.</p>
    </>
  );
}

/** Remaining debt under each payoff strategy — up to three lines, fixed color per strategy, one axis. */
export function DebtPayoffChart({ data }: { data: DebtPlan["chart"] }) {
  const lines = [
    { key: "avalanche", label: "Avalanche", color: SERIES[0] },
    { key: "snowball", label: "Snowball", color: SERIES[1] },
    { key: "minimum", label: "Minimum only", color: SERIES[2] },
  ] as const;
  const motion = useChartMotion();
  return (
    <>
      <Legend items={lines.map((l) => ({ label: l.label, color: l.color }))} />
      <div style={{ height: 260, marginTop: 8 }}>
        <ResponsiveContainer>
          <LineChart data={data}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="month" {...axis} tickFormatter={monthLabel} minTickGap={40} />
            <YAxis {...axis} axisLine={false} tickFormatter={kfmt} width={52} />
            <Tooltip content={<Tip labelFmt={monthLabel} />} cursor={{ stroke: "var(--axis)" }} />
            {lines.map((l) => (
              <Line key={l.key} type="monotone" dataKey={l.key} name={l.label} stroke={l.color} strokeWidth={2} dot={false}
                {...motion} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}

/** Month-end cash across accounts — single series area. */
export function CashHistoryChart({ data }: { data: NetWorth["cash_history"] }) {
  const motion = useChartMotion();
  return (
    <div style={{ height: 220 }}>
      <ResponsiveContainer>
        <AreaChart data={data}>
          <defs>
            <linearGradient id="nw" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--s1)" stopOpacity={0.22} />
              <stop offset="100%" stopColor="var(--s1)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="month" {...axis} tickFormatter={monthLabel} />
          <YAxis {...axis} axisLine={false} tickFormatter={kfmt} width={52} domain={["auto", "auto"]} />
          <Tooltip content={<Tip labelFmt={(l) => monthLabel(l) + (data.find((d) => d.month === l)?.partial ? " (so far)" : "")} />} cursor={{ stroke: "var(--axis)" }} />
          <Area type="monotone" dataKey="net_cash" name="Net cash" stroke="var(--s1)" strokeWidth={2} fill="url(#nw)" {...motion} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Projected balance without vs with a purchase — two lines, the cushion as a reference line. */
export function AffordChart({ r }: { r: AffordResult }) {
  const motion = useChartMotion();
  return (
    <>
      <Legend items={[{ label: "Without it", color: "var(--axis)" }, { label: "With it", color: SERIES[0] }]} />
      <div style={{ height: 260, marginTop: 8 }}>
        <ResponsiveContainer>
          <LineChart data={r.series}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="date" {...axis} tickFormatter={shortDate} minTickGap={40} />
            <YAxis {...axis} axisLine={false} tickFormatter={kfmt} width={56} domain={["auto", "auto"]} />
            <ReferenceLine y={r.cushion} stroke="var(--warning)" strokeDasharray="4 4"
              label={{ value: "Cushion", position: "insideTopLeft", fill: "var(--muted)", fontSize: 11 }} />
            <ReferenceLine y={0} stroke="var(--axis)" />
            <Tooltip content={<Tip labelFmt={shortDate} />} cursor={{ stroke: "var(--axis)" }} />
            <Line type="monotone" dataKey="before" name="Without it" stroke="var(--axis)" strokeDasharray="5 4" strokeWidth={1.5} dot={false} {...motion} />
            <Line type="monotone" dataKey="after" name="With it" stroke={SERIES[0]} strokeWidth={2} dot={false} {...motion} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}

/** Monthly spending across a period — single series bars. */
export function MonthlySpendChart({ data }: { data: { month: string; spending: number }[] }) {
  const motion = useChartMotion();
  return (
    <div style={{ height: 200 }}>
      <ResponsiveContainer>
        <BarChart data={data}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="month" {...axis} tickFormatter={monthLabel} />
          <YAxis {...axis} axisLine={false} tickFormatter={kfmt} width={48} />
          <Tooltip content={<Tip labelFmt={monthLabel} />} cursor={{ fill: "var(--surface-2)" }} />
          <Bar dataKey="spending" name="Spending" fill="var(--s1)" radius={[4, 4, 0, 0]} maxBarSize={28} {...motion} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Average discretionary spend by weekday — single series. */
export function WeekdayChart({ data }: { data: { weekday: string; avg_spend: number }[] }) {
  const motion = useChartMotion();
  return (
    <div style={{ height: 220 }}>
      <ResponsiveContainer>
        <BarChart data={data}>
          <CartesianGrid vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="weekday" {...axis} tickFormatter={(d: string) => d.slice(0, 3)} />
          <YAxis {...axis} axisLine={false} tickFormatter={(v: number) => money0(v)} width={48} />
          <Tooltip content={<Tip />} cursor={{ fill: "var(--surface-2)" }} />
          <Bar dataKey="avg_spend" name="Avg spend" fill="var(--s1)" radius={[4, 4, 0, 0]} maxBarSize={36} {...motion} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
