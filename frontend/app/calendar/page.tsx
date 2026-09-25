"use client";

import { useState } from "react";
import { Card, ErrorBox, Loading, PageHead, Stat } from "@/components/ui";
import { useApi } from "@/lib/api";
import { useScoped } from "@/lib/session";
import { dateLabel, money, shortDate } from "@/lib/format";
import type { BillCalendar } from "@/lib/types";

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function shift(month: string, delta: number) {
  const [y, m] = month.split("-").map(Number);
  const idx = y * 12 + (m - 1) + delta;
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, "0")}`;
}

const longMonth = (m: string) => {
  const [y, mo] = m.split("-").map(Number);
  return new Date(y, mo - 1, 1).toLocaleDateString("en-US", { month: "long", year: "numeric" });
};

export default function Calendar() {
  const scoped = useScoped();
  const [month, setMonth] = useState<string | null>(null);
  const cal = useApi<BillCalendar>(scoped(`/calendar${month ? `?month=${month}` : ""}`));
  if (cal.error) return <ErrorBox error={cal.error} />;
  const d = cal.data;
  const current = d?.month ?? month ?? "";
  const byDate = new Map((d?.days ?? []).map((x) => [x.date, x.events]));
  const upcoming = (d?.days ?? []).flatMap((x) => x.events.filter((e) => e.status === "upcoming").map((e) => ({ ...e, date: x.date })));

  return (
    <>
      <PageHead title="Bill calendar" sub="Every recurring bill, subscription and paycheck: posted ones from your statements, upcoming ones projected.">
        <button className="btn" disabled={!d || current <= d.earliest_month} onClick={() => setMonth(shift(current, -1))} aria-label="Previous month">←</button>
        <b style={{ minWidth: 130, textAlign: "center" }}>{current ? longMonth(current) : "…"}</b>
        <button className="btn" disabled={!d || current >= d.latest_month} onClick={() => setMonth(shift(current, 1))} aria-label="Next month">→</button>
      </PageHead>

      {!d ? <Loading h={100} /> : (
        <div className="grid cols-4" style={{ marginBottom: 16 }}>
          <Stat label="Recurring out" value={money(d.recurring_out)} foot={`${d.event_count} scheduled items`} />
          <Stat label="Still due" value={money(d.still_due)} foot={d.still_due > 0 ? "not yet charged" : "all paid"} />
          <Stat label="Paychecks & recurring income" value={money(d.recurring_in)} />
          <Stat label="Net recurring" value={money(d.net_recurring)} tone={d.net_recurring < 0 ? "neg" : "pos"}
            foot={d.heaviest_day ? `Heaviest day: ${shortDate(d.heaviest_day)}` : undefined} />
        </div>
      )}

      <div className="grid cols-3">
        <Card title={current ? longMonth(current) : "Month"} className="span-2"
          hint={<span className="row wrap" style={{ gap: 10 }}>
            <span className="cal-ev" style={{ display: "inline-flex" }}>Posted</span>
            <span className="cal-ev upcoming" style={{ display: "inline-flex" }}>Upcoming</span>
            <span className="cal-ev income" style={{ display: "inline-flex" }}>Income</span>
          </span>}>
          {!d ? <Loading h={420} /> : (
            <div className="cal" role="grid" aria-label={`Bills for ${longMonth(d.month)}`}>
              {DOW.map((w) => <div key={w} className="cal-dow">{w}</div>)}
              {Array.from({ length: d.first_weekday }, (_, i) => <div key={`b${i}`} className="cal-day blank" />)}
              {Array.from({ length: d.days_in_month }, (_, i) => {
                const iso = `${d.month}-${String(i + 1).padStart(2, "0")}`;
                const evs = byDate.get(iso) ?? [];
                const cls = [iso < d.as_of ? "past" : "", iso === d.as_of ? "today" : "", evs.length ? "" : "empty-day"].join(" ");
                return (
                  <div key={iso} className={`cal-day ${cls}`} role="gridcell" aria-label={dateLabel(iso)}>
                    <span className="cal-num">{i + 1}</span>
                    {evs.map((e, j) => (
                      <div key={j} className={`cal-ev ${e.kind === "income" ? "income" : ""} ${e.status}`} title={`${e.name} · ${money(e.amount)} · ${e.status}`}>
                        <span>{e.name}</span><b className="num">{money(Math.abs(e.amount)).replace(".00", "")}</b>
                      </div>
                    ))}
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <Card title="Coming up" hint="Projected from each series' cadence and last amount">
          {!d ? <Loading /> : upcoming.length === 0 ? <p className="muted">Nothing left to charge this month.</p> : (
            <div className="stack" style={{ gap: 8 }}>
              {upcoming.map((e, i) => (
                <div key={i} className="row between">
                  <span><span className="muted small" style={{ display: "inline-block", width: 52 }}>{shortDate(e.date)}</span>{e.name}</span>
                  <b className={`num ${e.amount > 0 ? "pos" : ""}`}>{money(e.amount)}</b>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
