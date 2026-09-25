"use client";

import { useState } from "react";
import { useRemovable } from "@/components/feedback";
import { Badge, Card, Empty, ErrorBox, Loading, PageHead, Progress, useToast } from "@/components/ui";
import { del, post, put, useApi } from "@/lib/api";
import { useScoped } from "@/lib/session";
import { money, monthLabel, pct } from "@/lib/format";
import type { BudgetStatus, Suggestions } from "@/lib/types";

export default function Budgets() {
  const scoped = useScoped();
  const status = useApi<BudgetStatus>(scoped("/budgets"));
  const sugg = useApi<Suggestions>(scoped("/budgets/suggestions"));
  const cats = useApi<string[]>("/categories");
  const [newCat, setNewCat] = useState("");
  const [newLimit, setNewLimit] = useState("");
  const toast = useToast();
  const removeBudget = useRemovable<string>();

  const reload = () => { status.reload(); sugg.reload(); };
  async function setBudget(category: string, limit: number) {
    await put("/budgets", { category, limit });
    toast.show(`${category} budget set to ${money(limit)}`);
    reload();
  }
  async function applyAll() {
    const r = await post<{ applied: number }>("/budgets/apply-suggestions");
    toast.show(`Applied ${r.applied} suggested budgets`);
    reload();
  }

  if (status.error) return <ErrorBox error={status.error} />;
  const s = status.data;
  const monthPct = s ? (s.days_elapsed / s.days_in_month) * 100 : 0;

  return (
    <>
      <PageHead title="Budgets" sub={s ? `${monthLabel(s.month)} · day ${s.days_elapsed} of ${s.days_in_month}` : undefined} />

      <div className="grid cols-2">
        <Card title="This month" hint="The line marks how far through the month we are — bars past it are running hot.">
          {!s ? <Loading h={260} /> : s.items.length === 0 ? <Empty>No budgets yet — apply the suggestions to get started.</Empty> : (
            <div className="stack" style={{ gap: 16 }}>
              {s.items.filter(removeBudget.visible((b: { category: string }) => b.category)).map((b) => (
                <div key={b.category}>
                  <div className="row between" style={{ marginBottom: 6 }}>
                    <div className="row" style={{ gap: 8 }}>
                      <b>{b.category}</b>
                      <Badge kind={b.status} />
                    </div>
                    <span className="num small"><b>{money(b.spent)}</b> <span className="muted">of {money(b.limit)}</span></span>
                  </div>
                  <Progress value={b.used_pct ?? 0} status={b.status} marker={monthPct} />
                  <div className="row between small muted" style={{ marginTop: 4 }}>
                    <span>Projected {money(b.projected_month_end)}</span>
                    <span>
                      {b.remaining >= 0 ? `${money(b.remaining)} left${b.daily_allowance > 0 ? ` · ${money(b.daily_allowance)}/day` : ""}` : `${money(-b.remaining)} over`}
                      <button className="btn sm danger" style={{ marginLeft: 8, padding: "0 6px" }} onClick={() => removeBudget(b.category, `${b.category} budget removed`,
                        () => del(`/budgets/${encodeURIComponent(b.category)}`).then(reload))} aria-label={`Remove ${b.category} budget`}>×</button>
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
          <form className="row wrap" style={{ marginTop: 18 }} onSubmit={(e) => { e.preventDefault(); if (newCat && Number(newLimit) > 0) { setBudget(newCat, Number(newLimit)); setNewLimit(""); } }}>
            <select className="select" value={newCat} onChange={(e) => setNewCat(e.target.value)} aria-label="Category">
              <option value="">Add a budget…</option>
              {cats.data?.filter((c) => !["Income", "Transfers"].includes(c)).map((c) => <option key={c}>{c}</option>)}
            </select>
            <input className="input" type="number" min="1" step="1" placeholder="Monthly limit" value={newLimit} onChange={(e) => setNewLimit(e.target.value)} style={{ width: 140 }} aria-label="Monthly limit" />
            <button className="btn">Save</button>
          </form>
        </Card>

        <Card title="50 / 30 / 20 check" hint={sugg.data ? `Based on ${money(sugg.data.avg_monthly_income)} average monthly income` : undefined}>
          {!sugg.data ? <Loading h={200} /> : (() => {
            const r = sugg.data.rule_50_30_20;
            const rows = [
              { l: "Needs", v: r.needs, p: r.needs_pct, t: 50, good: r.needs_pct <= 50 },
              { l: "Wants", v: r.wants, p: r.wants_pct, t: 30, good: r.wants_pct <= 30 },
              { l: "Savings", v: r.savings, p: r.savings_pct, t: 20, good: r.savings_pct >= 20 },
            ];
            return (
              <div className="stack" style={{ gap: 16 }}>
                {rows.map((x) => (
                  <div key={x.l}>
                    <div className="row between" style={{ marginBottom: 6 }}>
                      <b>{x.l}</b>
                      <span className="small"><b className="num">{money(x.v)}</b> · {pct(x.p)} <span className="muted">(target {x.l === "Savings" ? "≥" : "≤"}{x.t}%)</span></span>
                    </div>
                    <Progress value={x.p} status={x.good ? "on_track" : "at_risk"} marker={x.t} />
                  </div>
                ))}
                <p className="small muted">Computed from the suggested budgets below. Needs = housing, utilities, insurance, groceries, health, transport.</p>
              </div>
            );
          })()}
        </Card>
      </div>

      <div style={{ height: 16 }} />
      <Card title="Suggested budgets" hint={sugg.data ? `From your spending ${monthLabel(sugg.data.based_on_months[0])} – ${monthLabel(sugg.data.based_on_months.at(-1)!)}` : undefined}
        action={<button className="btn primary" onClick={applyAll}>Apply all</button>}>
        {!sugg.data ? <Loading h={300} /> : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Category</th><th className="r">Avg / mo</th><th className="r">Suggested</th><th className="r">Current</th><th>How</th><th /></tr>
                </thead>
                <tbody>
                  {sugg.data.suggestions.map((x) => (
                    <tr key={x.category}>
                      <td><b>{x.category}</b></td>
                      <td className="r">{money(x.avg_monthly)}</td>
                      <td className="r"><b>{money(x.suggested)}</b></td>
                      <td className="r muted">{x.current_budget != null ? money(x.current_budget) : "—"}</td>
                      <td className="small muted" style={{ maxWidth: 380 }}>{x.rationale}</td>
                      <td className="r">
                        {x.current_budget !== x.suggested && <button className="btn sm" onClick={() => setBudget(x.category, x.suggested)}>Use</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="row between" style={{ marginTop: 12 }}>
              <span className="muted">Total suggested: <b className="num" style={{ color: "var(--ink)" }}>{money(sugg.data.total_suggested)}</b>/mo</span>
              <span className="muted">Leaves <b className="num pos">{money(sugg.data.projected_monthly_savings)}</b>/mo to save</span>
            </div>
          </>
        )}
      </Card>
      {toast.node}
    </>
  );
}
