"use client";

import { useEffect, useState } from "react";
import { CashHistoryChart, DebtPayoffChart } from "@/components/charts";
import { useRemovable } from "@/components/feedback";
import { Badge, Card, ErrorBox, Loading, PageHead, Stat, useToast } from "@/components/ui";
import { del, post, useApi } from "@/lib/api";
import { useScoped } from "@/lib/session";
import { money, monthLabel, pct } from "@/lib/format";
import type { DebtPlan, DebtSummary, NetWorth } from "@/lib/types";

const ASSET_KINDS = ["investment", "retirement", "property", "vehicle", "other"];
const DEBT_KINDS: Record<string, string> = { credit_card: "Credit card", student_loan: "Student loan", auto_loan: "Auto loan", mortgage: "Mortgage", personal: "Personal", loan: "Other loan" };
const title = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export default function NetWorthPage() {
  const scoped = useScoped();
  const nw = useApi<NetWorth>(scoped("/net-worth"));
  const debts = useApi<DebtSummary>(scoped("/debts"));
  const [extra, setExtra] = useState(0);
  const [planPath, setPlanPath] = useState("/debts/plan?extra=0");
  const plan = useApi<DebtPlan>(planPath);
  const toast = useToast();
  const removeAsset = useRemovable<number>();
  const removeDebt = useRemovable<number>();
  const [asset, setAsset] = useState({ name: "", kind: "investment", value: "" });
  const [debt, setDebt] = useState({ name: "", kind: "credit_card", balance: "", apr: "", min_payment: "" });

  useEffect(() => { // debounce the slider so the plan isn't recomputed on every pixel
    const t = setTimeout(() => setPlanPath(`/debts/plan?extra=${extra}`), 250);
    return () => clearTimeout(t);
  }, [extra]);

  const reloadAll = () => { nw.reload(); debts.reload(); plan.reload(); };

  async function addAsset(e: React.FormEvent) {
    e.preventDefault();
    await post("/assets", { ...asset, value: Number(asset.value) });
    setAsset({ name: "", kind: asset.kind, value: "" });
    toast.show("Asset added");
    reloadAll();
  }

  async function addDebt(e: React.FormEvent) {
    e.preventDefault();
    await post("/debts", { ...debt, balance: Number(debt.balance), apr: Number(debt.apr), min_payment: Number(debt.min_payment) });
    setDebt({ name: "", kind: debt.kind, balance: "", apr: "", min_payment: "" });
    toast.show("Debt added");
    reloadAll();
  }

  if (nw.error) return <ErrorBox error={nw.error} />;
  const n = nw.data;
  const p = plan.data;
  const rec = p?.recommended ? p.strategies[p.recommended] : null;
  const surplus = p?.avg_monthly_surplus ?? 0;

  return (
    <>
      <PageHead title="Net worth & debt" sub="Everything you own minus everything you owe, and the fastest way to get debt-free." />

      {!n ? <Loading h={100} /> : (
        <div className="grid cols-4" style={{ marginBottom: 16 }}>
          <Stat label="Net worth" value={money(n.net_worth)} tone={n.net_worth < 0 ? "neg" : undefined} />
          <Stat label="Assets" value={money(n.total_assets)} foot={`${money(n.cash)} cash · ${money(n.manual_assets)} other`} />
          <Stat label="Liabilities" value={money(n.total_liabilities)} foot={`${money(n.debts)} loans · ${money(n.card_balances)} cards`} />
          <Stat label="Cash change, 12 months" value={money(n.cash_change_period)} tone={n.cash_change_period >= 0 ? "pos" : "neg"}
            foot={`Debt-to-asset ${pct(n.debt_to_asset_pct)}`} />
        </div>
      )}

      <div className="grid cols-3" style={{ marginBottom: 16 }}>
        <Card title="Cash across accounts" hint="Month-end balance, rebuilt from your transactions" className="span-2">
          {!n ? <Loading h={220} /> : <CashHistoryChart data={n.cash_history} />}
        </Card>
        <Card title="What you own">
          {!n ? <Loading /> : (
            <div className="stack" style={{ gap: 8 }}>
              {n.asset_mix.map((a) => (
                <div key={a.kind} className="row between small">
                  <span>{title(a.kind)}</span>
                  <span className="num"><b>{money(a.value)}</b> <span className="muted">{pct(a.share_pct, 0)}</span></span>
                </div>
              ))}
              <hr style={{ border: 0, borderTop: "1px solid var(--border)", width: "100%" }} />
              {n.assets.filter(removeAsset.visible((a: { id: number }) => a.id)).map((a) => (
                <div key={a.id} className="row between small">
                  <span>{a.name} <span className="muted">· {a.kind}</span></span>
                  <span className="row" style={{ gap: 6 }}>
                    <span className="num">{money(a.value)}</span>
                    <button className="btn sm danger" aria-label={`Remove ${a.name}`} onClick={() => removeAsset(a.id, `${a.name} removed`, () => del(`/assets/${a.id}`).then(reloadAll))}>×</button>
                  </span>
                </div>
              ))}
              <form className="row wrap" onSubmit={addAsset} style={{ marginTop: 6 }}>
                <input className="input" required placeholder="Asset name" value={asset.name} onChange={(e) => setAsset({ ...asset, name: e.target.value })} style={{ flex: "1 1 120px" }} />
                <select className="select" value={asset.kind} onChange={(e) => setAsset({ ...asset, kind: e.target.value })} aria-label="Asset type">
                  {ASSET_KINDS.map((k) => <option key={k} value={k}>{title(k)}</option>)}
                </select>
                <input className="input" required type="number" min="0" step="0.01" placeholder="Value" value={asset.value} onChange={(e) => setAsset({ ...asset, value: e.target.value })} style={{ width: 100 }} />
                <button className="btn primary">Add</button>
              </form>
            </div>
          )}
        </Card>
      </div>

      <Card title="Your debts" hint={debts.data ? `${money(debts.data.total_debt)} at a ${debts.data.weighted_apr_pct}% weighted APR · ${money(debts.data.monthly_interest_now)} interest this month` : undefined}>
        {!debts.data ? <Loading /> : (
          <div className="table-wrap">
            <table className="cards-mobile">
              <thead><tr><th>Debt</th><th>Type</th><th className="r">Balance</th><th className="r">APR</th><th className="r">Minimum</th><th className="r">Interest / mo</th><th /></tr></thead>
              <tbody>
                {debts.data.debts.filter(removeDebt.visible((d: { id: number }) => d.id)).map((d) => (
                  <tr key={d.id}>
                    <td data-label="Debt"><b>{d.name}</b></td>
                    <td data-label="Type" className="small">{DEBT_KINDS[d.kind] ?? d.kind}</td>
                    <td data-label="Balance" className="r">{money(d.balance)}</td>
                    <td data-label="APR" className="r">{d.apr_pct.toFixed(2)}%</td>
                    <td data-label="Minimum" className="r">{money(d.min_payment)}</td>
                    <td data-label="Interest / mo" className="r">{money(d.monthly_interest_now)}</td>
                    <td className="r"><button className="btn sm danger" onClick={() => removeDebt(d.id, `${d.name} removed`, () => del(`/debts/${d.id}`).then(reloadAll))} aria-label={`Remove ${d.name}`}>×</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <form className="row wrap" onSubmit={addDebt} style={{ marginTop: 14 }}>
          <input className="input" required placeholder="Debt name" value={debt.name} onChange={(e) => setDebt({ ...debt, name: e.target.value })} style={{ flex: "1 1 140px" }} />
          <select className="select" value={debt.kind} onChange={(e) => setDebt({ ...debt, kind: e.target.value })} aria-label="Debt type">
            {Object.entries(DEBT_KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <input className="input" required type="number" min="0.01" step="0.01" placeholder="Balance" value={debt.balance} onChange={(e) => setDebt({ ...debt, balance: e.target.value })} style={{ width: 110 }} />
          <input className="input" required type="number" min="0" max="100" step="0.01" placeholder="APR %" value={debt.apr} onChange={(e) => setDebt({ ...debt, apr: e.target.value })} style={{ width: 90 }} />
          <input className="input" required type="number" min="0.01" step="0.01" placeholder="Min payment" value={debt.min_payment} onChange={(e) => setDebt({ ...debt, min_payment: e.target.value })} style={{ width: 130 }} />
          <button className="btn primary">Add debt</button>
        </form>
      </Card>

      <div style={{ height: 16 }} />
      {plan.error ? <ErrorBox error={plan.error} /> : (
        <Card title="Payoff planner" hint="Avalanche vs snowball, simulated month by month to the cent"
          action={<label className="row small" style={{ gap: 8 }}>
            Extra per month
            <input type="range" min={0} max={Math.max(1000, Math.ceil(surplus / 50) * 50)} step={25} value={extra} onChange={(e) => setExtra(Number(e.target.value))} aria-label="Extra monthly payment" />
            <b className="num" style={{ width: 70 }}>{money(extra)}</b>
          </label>}>
          {!p ? <Loading h={300} /> : !rec ? <p className="muted">Add a debt above to see your payoff plan.</p> : (
            <>
              <p style={{ marginBottom: 14 }}>
                <Badge kind="good">Recommended: {title(p.recommended!)}</Badge>{" "}
                Debt-free by <b>{monthLabel(rec.debt_free_date!)}</b> ({rec.months} months), paying <b>{money(rec.total_interest)}</b> in interest —{" "}
                {rec.interest_saved_vs_minimum != null && <><b className="pos">{money(rec.interest_saved_vs_minimum)}</b> less than minimum payments</>}
                {rec.months_saved_vs_minimum != null && <> and <b>{rec.months_saved_vs_minimum} months</b> sooner</>}.
                {surplus > 0 && extra === 0 && <span className="muted"> Your average surplus is {money(surplus)}/month — try putting part of it here.</span>}
              </p>
              <div className="grid cols-3" style={{ marginBottom: 16 }}>
                {(["avalanche", "snowball", "minimum"] as const).map((k) => {
                  const s = p.strategies[k];
                  if (!s) return null;
                  return (
                    <div key={k} className="card" style={{ boxShadow: "none" }}>
                      <div className="row between"><h3>{k === "minimum" ? "Minimum only" : title(k)}</h3>{p.recommended === k && <Badge kind="good">Best</Badge>}</div>
                      <p className="small muted" style={{ margin: "4px 0 10px" }}>{s.description}</p>
                      {s.feasible ? (
                        <div className="stack small" style={{ gap: 4 }}>
                          <div className="row between"><span>Debt-free</span><b>{monthLabel(s.debt_free_date!)}</b></div>
                          <div className="row between"><span>Total interest</span><b className="num">{money(s.total_interest)}</b></div>
                          <div className="row between"><span>First debt gone</span><b>month {s.first_win_month}</b></div>
                          <div className="muted" style={{ marginTop: 4 }}>Order: {s.payoff_order.map((o) => o.name).join(" → ")}</div>
                        </div>
                      ) : <Badge kind="high">Never paid off — minimums don&apos;t cover interest</Badge>}
                    </div>
                  );
                })}
              </div>
              <DebtPayoffChart data={p.chart} />
            </>
          )}
        </Card>
      )}
      {toast.node}
    </>
  );
}
