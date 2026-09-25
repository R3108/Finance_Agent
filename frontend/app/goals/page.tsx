"use client";

import { useState } from "react";
import { useRemovable } from "@/components/feedback";
import { Badge, Card, ErrorBox, Loading, PageHead, Progress, useToast } from "@/components/ui";
import { del, post, useApi } from "@/lib/api";
import { dateLabel, money } from "@/lib/format";
import type { Goal, Simulation, Subscription, Suggestions } from "@/lib/types";

export default function Goals() {
  const goals = useApi<Goal[]>("/goals");
  const subs = useApi<{ items: Subscription[] }>("/subscriptions");
  const sugg = useApi<Suggestions>("/budgets/suggestions");
  const toast = useToast();
  const removeGoal = useRemovable<number>();

  const [form, setForm] = useState({ name: "", target: "", saved: "", target_date: "" });
  const [contrib, setContrib] = useState<Record<number, string>>({});
  const [cancel, setCancel] = useState<string[]>([]);
  const [cuts, setCuts] = useState<Record<string, number>>({});
  const [sim, setSim] = useState<Simulation | null>(null);
  const [simBusy, setSimBusy] = useState(false);
  const [simError, setSimError] = useState<string | null>(null);

  async function addGoal(e: React.FormEvent) {
    e.preventDefault();
    await post("/goals", { name: form.name, target: Number(form.target), saved: Number(form.saved || 0), target_date: form.target_date });
    setForm({ name: "", target: "", saved: "", target_date: "" });
    toast.show("Goal added");
    goals.reload();
  }

  async function contribute(g: Goal) {
    const amt = Number(contrib[g.id]);
    if (!amt) return;
    await post(`/goals/${g.id}/contribute`, { amount: amt });
    setContrib({ ...contrib, [g.id]: "" });
    toast.show(`Added ${money(amt)} to ${g.name}`);
    goals.reload();
  }

  async function runSim() {
    setSimBusy(true);
    try {
      setSim(await post<Simulation>("/simulate", { cancel, category_cuts: Object.fromEntries(Object.entries(cuts).filter(([, v]) => v > 0)) }));
      setSimError(null);
    } catch (e) {
      setSimError((e as Error).message);
    } finally {
      setSimBusy(false);
    }
  }

  if (goals.error) return <ErrorBox error={goals.error} />;
  const activeSubs = (subs.data?.items ?? []).filter((s) => s.active && s.kind === "subscription");
  const cuttable = (sugg.data?.suggestions ?? []).filter((s) => !["Housing", "Utilities", "Insurance", "Subscriptions"].includes(s.category));

  return (
    <>
      <PageHead title="Goals & what-if" sub="Plan savings goals and see exactly how cutting costs changes the timeline." />

      <div className="grid cols-2" style={{ marginBottom: 16 }}>
        <Card title="Savings goals">
          {!goals.data ? <Loading h={200} /> : (
            <div className="stack" style={{ gap: 18 }}>
              {goals.data.filter(removeGoal.visible((g: Goal) => g.id)).map((g) => (
                <div key={g.id}>
                  <div className="row between" style={{ marginBottom: 6 }}>
                    <div className="row" style={{ gap: 8 }}><b>{g.name}</b><Badge kind={g.on_track ? "on_track" : "at_risk"}>{g.on_track ? "On track" : "Behind"}</Badge></div>
                    <span className="small num"><b>{money(g.saved)}</b> <span className="muted">of {money(g.target)}</span></span>
                  </div>
                  <Progress value={g.progress_pct ?? 0} status="on_track" />
                  <div className="row between wrap small muted" style={{ marginTop: 6 }}>
                    <span>Needs {money(g.required_monthly)}/mo for {g.months_left} months · by {dateLabel(g.target_date)}</span>
                    <span className="row" style={{ gap: 6 }}>
                      <input className="input" type="number" placeholder="Amount" value={contrib[g.id] ?? ""} onChange={(e) => setContrib({ ...contrib, [g.id]: e.target.value })} style={{ width: 90, padding: "3px 8px" }} aria-label={`Contribution to ${g.name}`} />
                      <button className="btn sm" onClick={() => contribute(g)}>Add</button>
                      <button className="btn sm danger" onClick={() => removeGoal(g.id, `Goal “${g.name}” deleted`, () => del(`/goals/${g.id}`).then(goals.reload))} aria-label={`Delete ${g.name}`}>×</button>
                    </span>
                  </div>
                </div>
              ))}
              {goals.data[0] && <p className="small muted">Your average monthly surplus is {money(goals.data[0].available_monthly_surplus)} (last 6 full months).</p>}
            </div>
          )}
          <form className="row wrap" style={{ marginTop: 18 }} onSubmit={addGoal}>
            <input className="input" required placeholder="Goal name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} style={{ flex: "1 1 140px" }} />
            <input className="input" required type="number" min="1" placeholder="Target" value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })} style={{ width: 110 }} />
            <input className="input" type="number" min="0" placeholder="Saved so far" value={form.saved} onChange={(e) => setForm({ ...form, saved: e.target.value })} style={{ width: 100 }} />
            <input className="input" required type="date" value={form.target_date} onChange={(e) => setForm({ ...form, target_date: e.target.value })} aria-label="Target date" />
            <button className="btn primary">Add goal</button>
          </form>
        </Card>

        <Card title="What-if simulator" hint="Pick subscriptions to cancel and categories to trim"
          action={<button className="btn primary" onClick={runSim} disabled={simBusy}>{simBusy ? "Calculating…" : "Simulate"}</button>}>
          <h3 style={{ marginBottom: 8 }}>Cancel subscriptions</h3>
          <div className="row wrap" style={{ gap: 6, marginBottom: 16 }}>
            {activeSubs.map((s) => {
              const on = cancel.includes(s.key);
              return (
                <button key={s.key} className={`chip ${on ? "on" : ""}`} aria-pressed={on}
                  onClick={() => setCancel(on ? cancel.filter((k) => k !== s.key) : [...cancel, s.key])}>
                  {s.merchant} · {money(s.monthly_cost)}
                </button>
              );
            })}
          </div>
          <h3 style={{ marginBottom: 8 }}>Trim spending</h3>
          <div className="stack">
            {cuttable.slice(0, 6).map((c) => (
              <label key={c.category} className="row between small">
                <span style={{ width: 120 }}>{c.category}</span>
                <input type="range" min={0} max={50} step={5} value={cuts[c.category] ?? 0} onChange={(e) => setCuts({ ...cuts, [c.category]: Number(e.target.value) })} style={{ flex: 1 }} />
                <span className="num" style={{ width: 150, textAlign: "right" }}>−{cuts[c.category] ?? 0}% of {money(c.avg_monthly)}</span>
              </label>
            ))}
          </div>
        </Card>
      </div>

      {simError && <ErrorBox error={simError} />}
      {sim && (
        <Card title="Simulation result">
          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <div><div className="stat-label">Monthly savings</div><div className="stat-value pos">{money(sim.monthly_savings)}</div></div>
            <div><div className="stat-label">Per year</div><div className="stat-value">{money(sim.annual_savings)}</div></div>
            <div><div className="stat-label">In 5 years at 4% APY</div><div className="stat-value">{money(sim.five_year_savings_at_4pct)}</div></div>
            <div><div className="stat-label">Monthly surplus</div><div className="stat-value" style={{ fontSize: 20 }}>{money(sim.monthly_surplus_before)} → {money(sim.monthly_surplus_after)}</div></div>
          </div>
          <div className="grid cols-2">
            <table>
              <thead><tr><th>Change</th><th className="r">Saves / mo</th></tr></thead>
              <tbody>
                {sim.cancelled_subscriptions.map((c) => <tr key={c.key}><td>Cancel {c.merchant}</td><td className="r">{money(c.monthly_cost)}</td></tr>)}
                {sim.category_cuts.map((c) => <tr key={c.category}><td>Cut {c.category} by {c.cut_pct}%</td><td className="r">{money(c.monthly_savings)}</td></tr>)}
              </tbody>
            </table>
            <table>
              <thead><tr><th>Goal</th><th className="r">Months to goal (before → after)</th></tr></thead>
              <tbody>
                {sim.goal_impact.map((g) => <tr key={g.goal}><td>{g.goal}</td><td className="r">{g.months_before ?? "—"} → <b>{g.months_after ?? "—"}</b></td></tr>)}
              </tbody>
            </table>
          </div>
          <p className="small muted" style={{ marginTop: 10 }}>Months to goal assume your full monthly surplus goes to that goal.</p>
        </Card>
      )}
      {toast.node}
    </>
  );
}
