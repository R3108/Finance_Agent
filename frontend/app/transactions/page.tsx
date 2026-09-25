"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card, Empty, ErrorBox, Loading, PageHead, useToast } from "@/components/ui";
import { patch, useApi } from "@/lib/api";
import { useScoped } from "@/lib/session";
import { dateLabel, money } from "@/lib/format";
import type { Txn } from "@/lib/types";

const PAGE = 50;

export default function Transactions() {
  const scoped = useScoped();
  const cats = useApi<string[]>("/categories");
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [category, setCategory] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [offset, setOffset] = useState(0);
  const [applyAll, setApplyAll] = useState(true);
  const toast = useToast();

  useEffect(() => {
    const t = setTimeout(() => { setDebounced(q); setOffset(0); }, 250);
    return () => clearTimeout(t);
  }, [q]);

  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (debounced) params.set("q", debounced);
  if (category) params.set("category", category);
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  const { data, error, reload } = useApi<{ total_count: number; net_spend_of_matches: number; transactions: Txn[] }>(scoped(`/transactions?${params}`));

  async function recategorize(t: Txn, cat: string) {
    const r = await patch<{ also_updated: number }>(`/transactions/${t.id}`, { category: cat, apply_to_merchant: applyAll });
    toast.show(applyAll ? `${t.merchant} → ${cat} (rule saved, ${r.also_updated} other transactions updated)` : `Updated to ${cat}`);
    reload();
  }

  return (
    <>
      <PageHead title="Transactions" sub="Auto-categorised by rules. Fix a category once and Ledgerly learns it for that merchant.">
        <Link className="btn" href="/capture">Add from bank SMS</Link>
        <a className="btn" href="/api/export.csv">Export CSV</a>
      </PageHead>
      <Card>
        <div className="row wrap" style={{ marginBottom: 14 }}>
          <input className="input" placeholder="Search merchant or description" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: "1 1 220px" }} aria-label="Search" />
          <select className="select" value={category} onChange={(e) => { setCategory(e.target.value); setOffset(0); }} aria-label="Category">
            <option value="">All categories</option>
            {cats.data?.map((c) => <option key={c}>{c}</option>)}
          </select>
          <input className="input" type="date" value={start} onChange={(e) => { setStart(e.target.value); setOffset(0); }} aria-label="From" />
          <input className="input" type="date" value={end} onChange={(e) => { setEnd(e.target.value); setOffset(0); }} aria-label="To" />
          <label className="row small muted" style={{ gap: 6 }}>
            <input type="checkbox" checked={applyAll} onChange={(e) => setApplyAll(e.target.checked)} /> Learn from my edits
          </label>
        </div>
        {error && <ErrorBox error={error} />}
        {!data ? <Loading h={400} /> : data.transactions.length === 0 ? <Empty>No transactions match.</Empty> : (
          <>
            <div className="row between small muted" style={{ marginBottom: 8 }}>
              <span>{data.total_count.toLocaleString()} transactions</span>
              <span>Net spend of matches: <b className="num" style={{ color: "var(--ink)" }}>{money(data.net_spend_of_matches)}</b></span>
            </div>
            <div className="table-wrap">
              <table className="cards-mobile">
                <thead>
                  <tr><th>Date</th><th>Merchant</th><th>Account</th><th>Category</th><th className="r">Amount</th><th /></tr>
                </thead>
                <tbody>
                  {data.transactions.map((t) => (
                    <tr key={t.id}>
                      <td data-label="Date" className="num" style={{ whiteSpace: "nowrap" }}>{dateLabel(t.date)}</td>
                      <td className="cell-main">
                        <div><b>{t.merchant}</b></div>
                        <div className="small muted ellipsis txn-desc" title={t.description}>{t.description}</div>
                      </td>
                      <td data-label="Account" className="small muted">{t.account ?? "—"}</td>
                      <td data-label="Category">
                        {t.is_transfer ? <span className="badge">Transfer</span> : (
                          <select className="select" value={t.category} onChange={(e) => recategorize(t, e.target.value)} aria-label={`Category for ${t.merchant}`}
                            style={{ padding: "3px 6px", fontSize: 12.5 }}>
                            {cats.data?.map((c) => <option key={c}>{c}</option>)}
                          </select>
                        )}
                        {t.category_source === "llm" && <span className="badge accent" style={{ marginLeft: 6 }}>AI</span>}
                      </td>
                      <td data-label="Amount" className={`r num ${t.amount > 0 ? "pos" : ""}`}><b>{money(t.amount)}</b></td>
                      <td className="r">
                        {t.amount < 0 && !t.is_transfer && (
                          <Link className="btn sm" title="Split this with friends"
                                href={`/splits?${new URLSearchParams({ txn: String(t.id), amount: String(-t.amount), merchant: t.merchant })}`}>
                            Split
                          </Link>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="row between" style={{ marginTop: 12 }}>
              <button className="btn sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← Newer</button>
              <span className="small muted">{offset + 1}–{Math.min(offset + PAGE, data.total_count)} of {data.total_count}</span>
              <button className="btn sm" disabled={offset + PAGE >= data.total_count} onClick={() => setOffset(offset + PAGE)}>Older →</button>
            </div>
          </>
        )}
      </Card>
      {toast.node}
    </>
  );
}
