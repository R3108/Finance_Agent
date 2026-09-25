"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useRemovable } from "@/components/feedback";
import { Badge, Card, Empty, ErrorBox, Loading, PageHead, Stat, useToast } from "@/components/ui";
import { del, patch, post, useApi } from "@/lib/api";
import { currencyCode, dateLabel, money } from "@/lib/format";
import { useMe } from "@/lib/session";
import type { SplitBalance, SplitsOverview } from "@/lib/types";

type Person = { name: string; vpa: string; share: string };
const blank = (): Person => ({ name: "", vpa: "", share: "" });

function BalanceRow({ b, onSettle }: { b: SplitBalance; onSettle: () => void }) {
  const toast = useToast();
  return (
    <div className="person-row">
      <div>
        <div><b>{b.person}</b>{b.vpa && <span className="small muted"> · {b.vpa}</span>}</div>
        <div className={`small ${b.direction === "owes_you" ? "pos" : b.direction === "you_owe" ? "neg" : "muted"}`}>
          {b.direction === "owes_you" ? `Owes you ${b.net_text}` : b.direction === "you_owe" ? `You owe ${b.net_text}` : "All square"}
          <span className="muted"> · {b.items} item{b.items === 1 ? "" : "s"}</span>
        </div>
      </div>
      <div className="row wrap" style={{ justifyContent: "flex-end" }}>
        {b.reminder && (
          <>
            <a className="btn sm" href={`https://wa.me/?text=${encodeURIComponent(b.reminder)}`} target="_blank" rel="noreferrer">Remind on WhatsApp</a>
            <button className="btn sm" onClick={() => { navigator.clipboard?.writeText(b.reminder!); toast.show("Reminder copied"); }}>Copy</button>
          </>
        )}
        {b.pay_link && <a className="btn sm primary" href={b.pay_link}>Pay via UPI</a>}
        <button className="btn sm" onClick={onSettle}>Mark settled</button>
      </div>
      {toast.node}
    </div>
  );
}

function Splits() {
  const params = useSearchParams();
  const txnId = params.get("txn");
  const { data, error, loading, reload } = useApi<SplitsOverview>("/splits");
  const { me, refresh } = useMe();
  const toast = useToast();
  const removeItem = useRemovable<number>();
  const [people, setPeople] = useState<Person[]>([blank()]);
  const [amount, setAmount] = useState(params.get("amount") ?? "");
  const [note, setNote] = useState(params.get("merchant") ?? "");
  const [direction, setDirection] = useState<"owed_to_me" | "i_owe">("owed_to_me");
  const [includeMe, setIncludeMe] = useState(true);
  const [formError, setFormError] = useState<string | null>(null);
  const [vpa, setVpa] = useState("");
  const inr = currencyCode() === "INR";

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    const named = people.filter((p) => p.name.trim());
    const custom = named.some((p) => p.share);
    try {
      const r = await post<{ your_share: number; shares: { person: string; amount: number }[] }>("/splits", {
        transaction_id: txnId ? Number(txnId) : null,
        amount: txnId ? null : Number(amount) || null,
        note: note || null, direction, include_me: includeMe,
        people: named.map((p) => ({ name: p.name.trim(), vpa: p.vpa.trim() || null, share: custom ? Number(p.share) || null : null })),
      });
      toast.show(`Split saved · ${r.shares.map((s) => `${s.person} ${money(s.amount)}`).join(", ")}`);
      setPeople([blank()]);
      setAmount("");
      setNote("");
      reload();
    } catch (err) {
      setFormError((err as Error).message);
    }
  }

  async function settle(person: string) {
    await post("/splits/settle", { person });
    toast.show(`Settled with ${person}`);
    reload();
  }

  if (loading && !data) return <Loading h={320} />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  return (
    <>
      <PageHead title="Split & settle"
        sub="Shared dinners, cabs and trips — who owes whom, with one-tap UPI links to settle up. Ledgerly never moves money itself." />

      <div className="grid cols-3" style={{ marginBottom: 16 }}>
        <Stat label="Owed to you" value={money(data.owed_to_you)} tone={data.owed_to_you ? "pos" : undefined}
              foot={`${data.balances.filter((b) => b.direction === "owes_you").length} people`} />
        <Stat label="You owe" value={money(data.you_owe)} tone={data.you_owe ? "neg" : undefined}
              foot={`${data.balances.filter((b) => b.direction === "you_owe").length} people`} />
        <Stat label="Your real share this month" value={money(data.this_month.your_share)}
              foot={data.this_month.fronted_for_others
                ? `${money(data.this_month.spending)} spent, ${money(data.this_month.fronted_for_others)} of it for friends`
                : "Split a charge to see what was really yours"} />
      </div>

      <div className="grid cols-2" style={{ alignItems: "start" }}>
        <Card title="Balances" hint="Netted per person: what they owe you minus what you owe them.">
          {data.balances.length
            ? data.balances.map((b) => <BalanceRow key={b.person} b={b} onSettle={() => settle(b.person)} />)
            : <Empty>Nothing open. Split a bill to start tracking it.</Empty>}
          {inr && !data.your_upi_vpa && data.balances.some((b) => b.direction === "owes_you") && (
            <form className="stack small" style={{ marginTop: 12 }} onSubmit={async (e) => {
              e.preventDefault();
              try {
                await patch("/me", { upi_vpa: vpa });
                toast.show("UPI id saved — reminders now include a pay link");
                refresh();
                reload();
              } catch (err) {
                toast.show((err as Error).message);
              }
            }}>
              <span>Add your UPI id so reminders carry a one-tap pay link:</span>
              <div className="row">
                <input className="input" placeholder="yourname@okicici" value={vpa} onChange={(e) => setVpa(e.target.value)} aria-label="Your UPI id" />
                <button className="btn">Save</button>
              </div>
            </form>
          )}
        </Card>

        <Card title={txnId ? `Split ${note || "this transaction"}` : "Split a bill"}
              hint={txnId ? `${money(Number(amount))} from your transactions` : "Or pick a transaction from the Transactions page."}>
          <form className="stack" onSubmit={create}>
            {!txnId && (
              <div className="row wrap">
                <input className="input" type="number" min="1" step="any" required placeholder="Bill total" value={amount}
                       onChange={(e) => setAmount(e.target.value)} style={{ flex: "1 1 120px" }} aria-label="Bill total" />
                <input className="input" placeholder="What for? e.g. Dinner at Toit" value={note} maxLength={140}
                       onChange={(e) => setNote(e.target.value)} style={{ flex: "2 1 200px" }} aria-label="What for" />
              </div>
            )}
            <div className="row wrap small">
              <label className="row"><input type="radio" checked={direction === "owed_to_me"} onChange={() => setDirection("owed_to_me")} /> I paid</label>
              <label className="row"><input type="radio" checked={direction === "i_owe"} onChange={() => setDirection("i_owe")} /> They paid</label>
              <label className="row"><input type="checkbox" checked={includeMe} onChange={(e) => setIncludeMe(e.target.checked)} /> Include my share when splitting equally</label>
            </div>
            {people.map((p, i) => (
              <div key={i} className="row wrap">
                <input className="input" placeholder={`Person ${i + 1}`} value={p.name} required={i === 0} maxLength={60}
                       onChange={(e) => setPeople(people.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
                       style={{ flex: "2 1 140px" }} aria-label={`Person ${i + 1} name`} />
                {inr && <input className="input" placeholder="UPI id (optional)" value={p.vpa}
                       onChange={(e) => setPeople(people.map((x, j) => (j === i ? { ...x, vpa: e.target.value } : x)))}
                       style={{ flex: "2 1 140px" }} aria-label={`Person ${i + 1} UPI id`} />}
                <input className="input" type="number" min="0" step="any" placeholder="Share (optional)" value={p.share}
                       onChange={(e) => setPeople(people.map((x, j) => (j === i ? { ...x, share: e.target.value } : x)))}
                       style={{ flex: "1 1 100px" }} aria-label={`Person ${i + 1} share`} />
                {people.length > 1 && <button type="button" className="btn sm" onClick={() => setPeople(people.filter((_, j) => j !== i))} aria-label="Remove person">✕</button>}
              </div>
            ))}
            <div className="row wrap">
              <button type="button" className="btn sm" onClick={() => setPeople([...people, blank()])} disabled={people.length >= 20}>+ Add person</button>
              <span className="small muted">Leave shares empty to split equally — the paise always add up exactly.</span>
            </div>
            {formError && <div className="error" role="alert">{formError}</div>}
            <div><button className="btn primary">Save split</button></div>
          </form>
        </Card>
      </div>

      <div className="grid cols-2" style={{ marginTop: 16, alignItems: "start" }}>
        <Card title="Open items">
          {data.open.length ? (
            <div className="table-wrap">
              <table className="cards-mobile">
                <thead><tr><th>Person</th><th>For</th><th className="r">Amount</th><th /></tr></thead>
                <tbody>
                  {data.open.filter(removeItem.visible((o: { id: number }) => o.id)).map((o) => (
                    <tr key={o.id}>
                      <td data-label="Person"><b>{o.person}</b></td>
                      <td data-label="For" className="small">{o.note ?? o.merchant ?? "—"}<div className="muted">{dateLabel(o.date)}</div></td>
                      <td data-label="Amount" className={`r ${o.direction === "owes_you" ? "pos" : "neg"}`}>{o.direction === "owes_you" ? "+" : "−"}{o.amount_text}</td>
                      <td className="r">
                        <button className="btn sm" onClick={async () => { await post("/splits/settle", { split_id: o.id }); reload(); }}>Settled</button>{" "}
                        <button className="btn sm danger" aria-label="Delete"
                                onClick={() => removeItem(o.id, `Split with ${o.person} deleted`, () => del(`/splits/${o.id}`).then(reload))}>✕</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <Empty>No open items.</Empty>}
        </Card>
        <Card title="Recently settled">
          {data.settled.length ? (
            <div className="stack small">
              {data.settled.map((s, i) => (
                <div key={i} className="row between">
                  <span><Badge kind="good">settled</Badge> {s.person} <span className="muted">· {s.note ?? ""}</span></span>
                  <span className="num">{money(Math.abs(s.amount))}</span>
                </div>
              ))}
            </div>
          ) : <Empty>Nothing settled yet.</Empty>}
          {me && !inr && <p className="small muted" style={{ marginTop: 10 }}>UPI pay links appear when your currency is INR.</p>}
        </Card>
      </div>
      {toast.node}
    </>
  );
}

export default function SplitsPage() {
  return <Suspense fallback={<Loading h={320} />}><Splits /></Suspense>;
}
