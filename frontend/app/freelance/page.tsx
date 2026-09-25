"use client";

import { useState } from "react";
import { Badge, Card, Empty, ErrorBox, Loading, PageHead, Stat, useToast } from "@/components/ui";
import { del, post, put, useApi } from "@/lib/api";
import { dateLabel, money, pct } from "@/lib/format";
import type { TaxOverview, TaxSuggestion, TaxTxn } from "@/lib/types";

const KIND_LABEL: Record<string, string> = { business: "Business", personal: "Personal" };

/** Suggestions the user confirms one at a time — nothing is classified without a decision. */
function Suggestions({ items, deductions, onDone }: {
  items: TaxSuggestion[];
  deductions: TaxOverview["deductions"];
  onDone: () => void;
}) {
  const [busy, setBusy] = useState<number | null>(null);

  async function decide(s: TaxSuggestion, business: boolean) {
    setBusy(s.transaction_id);
    try {
      await put(`/tax/tags/${s.transaction_id}`, business
        ? { kind: "business", deduction: s.deduction, share_pct: s.suggested_share }
        : { kind: "personal" });
      onDone();
    } finally {
      setBusy(null);
    }
  }

  if (!items.length) return <Empty>Nothing left to review — every likely business expense is classified.</Empty>;

  return (
    <div className="stack">
      {items.slice(0, 10).map((s) => (
        <div key={s.transaction_id} className="row between" style={{ alignItems: "flex-start", gap: 12 }}>
          <div>
            <b>{s.merchant}</b> <span className="muted">{s.amount_text}</span>
            <div className="small muted">
              {dateLabel(s.date)} · {s.why} Suggested: {deductions.find((d) => d.key === s.deduction)?.label}
              {s.suggested_share < 100 && ` at ${s.suggested_share}%`}
            </div>
          </div>
          <div className="row nowrap" style={{ gap: 6 }}>
            <button className="btn sm primary" disabled={busy === s.transaction_id}
                    onClick={() => decide(s, true)}>Business</button>
            <button className="btn sm" disabled={busy === s.transaction_id}
                    onClick={() => decide(s, false)}>Personal</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function TaggingTable({ fy, deductions, onChange }: {
  fy: string; deductions: TaxOverview["deductions"]; onChange: () => void;
}) {
  const [kind, setKind] = useState<"untagged" | "business" | "personal">("untagged");
  const rows = useApi<{ transactions: TaxTxn[]; total_count: number }>(
    `/tax/transactions?fy=${fy}&kind=${kind}&limit=100`);

  async function update(t: TaxTxn, patch: Partial<TaxTxn>) {
    const next = { ...t, ...patch };
    await put(`/tax/tags/${t.id}`, {
      kind: next.kind ?? "business", deduction: next.deduction, share_pct: next.share_pct,
      client: next.client,
    });
    rows.reload();
    onChange();
  }

  return (
    <Card title="Classify transactions"
          hint={rows.data ? `${rows.data.total_count} ${kind === "untagged" ? "still unclassified" : kind}` : undefined}
          action={
            <select className="select" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}
                    aria-label="Which transactions">
              <option value="untagged">Unclassified</option>
              <option value="business">Business</option>
              <option value="personal">Personal</option>
            </select>
          }
          className="span-2">
      {rows.loading ? <Loading h={200} /> : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Date</th><th>Merchant</th><th className="r">Amount</th><th>Classification</th>
                  <th>Bucket</th><th className="r">Business %</th><th /></tr>
            </thead>
            <tbody>
              {rows.data?.transactions.map((t) => (
                <tr key={t.id}>
                  <td className="small muted">{dateLabel(t.date)}</td>
                  <td>{t.merchant}{t.has_receipt && <> <Badge kind="good">receipt</Badge></>}
                    <div className="small muted">{t.category}</div>
                  </td>
                  <td className="r">{money(t.amount)}</td>
                  <td>
                    <select className="select" value={t.kind ?? ""} aria-label={`Classify ${t.merchant}`}
                            onChange={(e) => e.target.value
                              ? update(t, { kind: e.target.value as TaxTxn["kind"] })
                              : del(`/tax/tags/${t.id}`).then(() => { rows.reload(); onChange(); })}>
                      <option value="">—</option>
                      <option value="business">Business</option>
                      <option value="personal">Personal</option>
                    </select>
                  </td>
                  <td>
                    {t.kind === "business" && t.amount < 0 && (
                      <select className="select" value={t.deduction ?? ""} aria-label={`Bucket for ${t.merchant}`}
                              onChange={(e) => update(t, { deduction: e.target.value })}>
                        <option value="">Choose…</option>
                        {deductions.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
                      </select>
                    )}
                    {t.kind === "business" && t.amount > 0 && (
                      <input className="input" placeholder="Client" defaultValue={t.client ?? ""}
                             onBlur={(e) => e.target.value !== (t.client ?? "") && update(t, { client: e.target.value })} />
                    )}
                  </td>
                  <td className="r">
                    {t.kind === "business" && t.amount < 0 && (
                      <input className="input" type="number" min={1} max={100} style={{ width: 70 }}
                             defaultValue={t.share_pct} aria-label={`Business share for ${t.merchant}`}
                             onBlur={(e) => Number(e.target.value) !== t.share_pct
                               && update(t, { share_pct: Number(e.target.value) })} />
                    )}
                  </td>
                  <td className="small muted">{t.source === "rule" ? "auto" : t.source === "user" ? "you" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.data?.transactions.length && <Empty>Nothing here.</Empty>}
        </div>
      )}
    </Card>
  );
}

export default function Freelance() {
  const [fy, setFy] = useState<string | null>(null);
  const { data, error, loading, reload } = useApi<TaxOverview>(`/tax${fy ? `?fy=${fy}` : ""}`);
  const toast = useToast();
  const [payment, setPayment] = useState({ paid_on: "", amount: "", kind: "advance" });

  async function addPayment(e: React.FormEvent) {
    e.preventDefault();
    try {
      await post("/tax/payments", { ...payment, amount: Number(payment.amount) });
      setPayment({ paid_on: "", amount: "", kind: "advance" });
      toast.show("Payment recorded");
      reload();
    } catch (err) {
      toast.show((err as Error).message);
    }
  }

  if (loading) return <Loading h={320} />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const { summary, estimate, advance_tax: advance } = data;

  return (
    <>
      <PageHead
        title="Business & tax"
        sub="Split your ledger into business and personal, see what you'll owe, and hand your accountant a clean file."
      >
        <select className="select" value={data.financial_year} onChange={(e) => setFy(e.target.value)}
                aria-label="Financial year">
          {data.available_years.map((y) => <option key={y} value={y}>FY {y}</option>)}
        </select>
        <a className="btn" href={`/api/tax/export.csv?fy=${data.financial_year}`}>Export for accountant</a>
      </PageHead>

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <Stat label="Gross receipts" value={money(summary.gross_receipts)}
              foot={`${summary.clients.length} client${summary.clients.length === 1 ? "" : "s"}`} />
        <Stat label="Deductible expenses" value={money(summary.total_expenses)}
              foot={`${summary.tagged_transactions} classified`} />
        <Stat label="Net profit" value={money(summary.net_profit)}
              foot={summary.margin_pct != null ? `${pct(summary.margin_pct)} margin` : undefined} />
        <Stat label="Estimated tax" value={estimate.applicable ? money(estimate.total_tax) : "—"}
              foot={estimate.applicable && estimate.effective_rate_pct != null
                ? `${pct(estimate.effective_rate_pct)} effective` : estimate.reason} />
      </div>

      {estimate.rates_stale && (
        <div className="error" role="status" style={{ marginBottom: 16 }}>
          This estimate uses the published rates for FY {estimate.rates_from}, the most recent in the app.
          Rates for FY {data.financial_year} haven&apos;t been added yet, so treat the figure as indicative.
        </div>
      )}

      <div className="grid cols-2">
        <Card title="Needs a decision" hint="Likely business expenses, largest first. Nothing is classified until you say so.">
          <Suggestions items={data.suggestions} deductions={data.deductions} onDone={reload} />
          {data.suggestions.length > 0 && (
            <div className="row" style={{ marginTop: 14 }}>
              <button className="btn sm" onClick={async () => {
                const r = await post<{ tagged: number }>("/tax/tags/apply-rules");
                toast.show(r.tagged ? `${r.tagged} transactions classified from your past decisions` : "No consistent patterns yet");
                reload();
              }}>Apply my past decisions</button>
            </div>
          )}
        </Card>

        <Card title="Where the deductions are">
          {summary.buckets.length ? (
            <div className="stack">
              {summary.buckets.map((b) => (
                <div key={b.key} className="row between">
                  <span>{b.label} <span className="small muted">· {b.transactions} items</span></span>
                  <span>{b.amount_text} <span className="small muted">{b.share_pct != null && `${pct(b.share_pct, 0)}`}</span></span>
                </div>
              ))}
              <div className="row between" style={{ borderTop: "1px solid var(--border)", paddingTop: 8, fontWeight: 600 }}>
                <span>Total</span><span>{money(summary.total_expenses)}</span>
              </div>
              {summary.untagged_spend > 0 && (
                <p className="small muted">
                  {money(summary.untagged_spend)} of spending this year is still unclassified — some of it may be deductible.
                </p>
              )}
            </div>
          ) : <Empty>Classify some expenses and they&apos;ll be grouped here.</Empty>}
        </Card>

        {data.comparison.length > 1 && (
          <Card title="Which regime costs least" hint="The same year, costed under each option.">
            <div className="stack">
              {data.comparison.map((r) => (
                <div key={r.regime} className="row between">
                  <span>{r.label} {r.best && <Badge kind="good">cheapest</Badge>}</span>
                  <b>{r.total_tax_text}</b>
                </div>
              ))}
              <label className="stack small" style={{ gap: 6, marginTop: 8 }}>
                <span>Your regime</span>
                <select className="select" value={data.profile.regime}
                        onChange={async (e) => { await put("/tax/profile", { regime: e.target.value }); reload(); }}>
                  {data.regimes.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
                </select>
                <span className="muted">{data.regimes.find((r) => r.key === data.profile.regime)?.description}</span>
              </label>
              {estimate.eligibility_note && <p className="small muted">{estimate.eligibility_note}</p>}
            </div>
          </Card>
        )}

        <Card title="Advance tax" hint={advance.required ? "Four instalments across the year" : undefined}>
          {advance.required ? (
            <div className="stack">
              {advance.instalments.map((i) => (
                <div key={i.due_date} className="row between">
                  <span>{dateLabel(i.due_date)} <span className="small muted">· {i.share_pct}% cumulative</span></span>
                  <span>{i.instalment_text} <Badge kind={i.status === "overdue" ? "high" : i.status === "paid" ? "good" : "info"}>{i.status}</Badge></span>
                </div>
              ))}
              {advance.shortfall != null && advance.shortfall > 0 && (
                <p className="small">Short by <b>{advance.shortfall_text}</b> against what&apos;s due so far.</p>
              )}
              {advance.note && <p className="small muted">{advance.note}</p>}
            </div>
          ) : <Empty>{advance.reason ?? "No advance tax due."}</Empty>}

          <form className="row wrap" onSubmit={addPayment} style={{ marginTop: 14, gap: 8 }}>
            <input className="input" type="date" required value={payment.paid_on}
                   onChange={(e) => setPayment({ ...payment, paid_on: e.target.value })} aria-label="Payment date" />
            <input className="input" type="number" min="0.01" step="0.01" required placeholder="Amount"
                   style={{ width: 120 }} value={payment.amount}
                   onChange={(e) => setPayment({ ...payment, amount: e.target.value })} aria-label="Amount paid" />
            <select className="select" value={payment.kind}
                    onChange={(e) => setPayment({ ...payment, kind: e.target.value })} aria-label="Payment kind">
              <option value="advance">Advance tax</option>
              <option value="self_assessment">Self assessment</option>
              <option value="tds">TDS credit</option>
            </select>
            <button className="btn">Record</button>
          </form>
          {data.payments.length > 0 && (
            <div className="stack small" style={{ marginTop: 10 }}>
              {data.payments.map((p) => (
                <div key={p.id} className="row between">
                  <span>{dateLabel(p.paid_on)} · {p.kind.replace("_", " ")}</span>
                  <span>{p.amount_text}{" "}
                    <button className="btn sm danger" onClick={async () => {
                      await del(`/tax/payments/${p.id}`); reload();
                    }}>Remove</button>
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>

        {summary.clients.length > 0 && (
          <Card title="Income by client">
            <div className="stack">
              {summary.clients.map((c) => (
                <div key={c.client} className="row between">
                  <span>{c.client} <span className="small muted">· {c.invoices} payments</span></span>
                  <span>{c.amount_text} <span className="small muted">{c.share_pct != null && pct(c.share_pct, 0)}</span></span>
                </div>
              ))}
            </div>
          </Card>
        )}

        <Card title="Receipts" hint="Proof for the expenses you've claimed">
          <div className="stack">
            <Stat label="Business expenses with a receipt"
                  value={`${data.receipts.with_receipt} / ${data.receipts.business_transactions}`}
                  foot={data.receipts.coverage_pct != null ? `${pct(data.receipts.coverage_pct, 0)} covered` : undefined} />
            <a className="btn" href="/receipts">Manage receipts</a>
          </div>
        </Card>

        <TaggingTable fy={data.financial_year} deductions={data.deductions} onChange={reload} />
      </div>

      <p className="small muted" style={{ marginTop: 16 }}>{data.disclaimer}</p>
      {toast.node}
    </>
  );
}
