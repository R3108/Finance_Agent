"use client";

import { useState } from "react";
import { Badge, Card, ErrorBox, PageHead, UpgradeCard, useToast } from "@/components/ui";
import { del, post, useApi } from "@/lib/api";
import { dateLabel, money } from "@/lib/format";
import type { Account, CapturePreviewItem } from "@/lib/types";

type Preview = { items: CapturePreviewItem[]; parsed: number; skipped: number };
type ImportResult = { received: number; inserted: number; duplicates_skipped: number; skipped: { text: string; reason: string }[] };
type TokenStatus = { active: boolean; account_id?: number | null; last_used_at?: string | null; created_at?: string; endpoint: string; allowed: boolean };

const SAMPLE = `Sent Rs.250.00
From HDFC Bank A/C *1234
To SWIGGY
On 14/09/26
Ref 425712345678

Rs.649 spent on HDFC Bank Card x9876 at NETFLIX on 2026-09-14. Avl Lmt: Rs 1,50,000

123456 is your OTP for txn of Rs.500 at AMAZON. Do not share`;

function AutoForward({ accounts }: { accounts: Account[] }) {
  const status = useApi<TokenStatus>("/capture/token");
  const [token, setToken] = useState<string | null>(null);
  const [account, setAccount] = useState("");
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  if (!status.data) return null;
  const s = status.data;
  if (!s.allowed) {
    return <UpgradeCard message="Pro turns on automatic tracking: your phone forwards every bank SMS the moment it arrives, so your ledger is always up to date without importing anything." />;
  }

  async function mint() {
    setError(null);
    try {
      const r = await post<{ token: string }>("/capture/token", { account_id: account ? Number(account) : null });
      setToken(r.token);
      status.reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <Card title="Track automatically" hint="Your phone forwards each bank SMS here as it arrives. Nothing to paste, ever.">
      <div className="stack">
        {s.active ? (
          <p className="small">
            <Badge kind="good">Connected</Badge>{" "}
            {s.last_used_at ? <>Last message received {dateLabel(s.last_used_at.slice(0, 10))}.</> : "Waiting for the first message."}
          </p>
        ) : <p className="small muted">Not set up yet.</p>}

        {token ? (
          <div className="stack small">
            <b>Your forwarding key — copy it now, it won&apos;t be shown again:</b>
            <div className="secret">{token}</div>
            <div className="row wrap">
              <button className="btn sm" onClick={() => { navigator.clipboard?.writeText(token); toast.show("Key copied"); }}>Copy key</button>
              <button className="btn sm" onClick={() => { navigator.clipboard?.writeText(s.endpoint); toast.show("Address copied"); }}>Copy address</button>
            </div>
          </div>
        ) : (
          <div className="row wrap">
            <select className="select" value={account} onChange={(e) => setAccount(e.target.value)} aria-label="Default account">
              <option value="">Match account from the SMS</option>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
            <button className="btn primary" onClick={mint}>{s.active ? "Replace key" : "Create forwarding key"}</button>
            {s.active && <button className="btn danger" onClick={async () => { await del("/capture/token"); setToken(null); status.reload(); toast.show("Forwarding stopped"); }}>Stop</button>}
          </div>
        )}
        {error && <ErrorBox error={error} />}

        <details>
          <summary className="small"><b>How to set it up on your phone</b></summary>
          <div className="stack small" style={{ marginTop: 8 }}>
            <div>
              <b>Android</b> — install an SMS-forwarding app (e.g. “SMS Forwarder”), add a rule for messages from your
              bank&apos;s sender ID (like <code>HDFCBK</code> or <code>SBIUPI</code>), and forward them as a webhook:
              <ul className="steps" style={{ marginTop: 6 }}>
                <li>URL: <code>{s.endpoint}</code></li>
                <li>Method: <code>POST</code>, body: the message text, or JSON <code>{`{"text": "%message%"}`}</code></li>
                <li>Header: <code>X-Capture-Token: &lt;your key&gt;</code> (or add <code>?token=&lt;your key&gt;</code> to the URL if the app can&apos;t set headers)</li>
              </ul>
            </div>
            <div>
              <b>iPhone</b> — in Shortcuts, create a Personal Automation “When I get a message from” your bank, with
              the action <i>Get contents of URL</i>: the address above, method POST, the header above, and the message as
              the request body.
            </div>
            <p className="muted">
              The key can only add transactions to your ledger — it can&apos;t read anything. OTPs, declined payments and
              reminders are ignored, and a message that arrives twice is only recorded once.
            </p>
          </div>
        </details>
      </div>
      {toast.node}
    </Card>
  );
}

export default function Capture() {
  const overview = useApi<{ balances: { accounts: Account[] } }>("/overview");
  const [text, setText] = useState("");
  const [account, setAccount] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const accounts = overview.data?.balances.accounts ?? [];

  async function check() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setPreview(await post<Preview>("/capture/preview", { text }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      setResult(await post<ImportResult>("/capture/import", { text, account_id: account ? Number(account) : null }));
      setPreview(null);
      setText("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHead title="Quick add from bank SMS"
        sub="Paste the alerts your bank texts you — UPI, cards, salary, ATM. Ledgerly reads the amount, date and payee, and categorises them like any other transaction." />

      <div className="grid cols-3" style={{ alignItems: "start" }}>
        <Card title="Paste messages" className="span-2"
              hint="One or many. Separate messages with a blank line if they run together.">
          <div className="stack">
            <textarea className="input" value={text} onChange={(e) => { setText(e.target.value); setPreview(null); }}
                      placeholder="Sent Rs.250.00 From HDFC Bank A/C *1234 To SWIGGY On 14/09/26 Ref 425712345678" aria-label="Bank SMS text" />
            <div className="row wrap">
              <select className="select" value={account} onChange={(e) => setAccount(e.target.value)} aria-label="Account">
                <option value="">Match account from the SMS</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
              <button className="btn" disabled={busy || !text.trim()} onClick={check}>Preview</button>
              <button className="btn primary" disabled={busy || !preview?.parsed} onClick={save}>
                {preview ? `Add ${preview.parsed} transaction${preview.parsed === 1 ? "" : "s"}` : "Add transactions"}
              </button>
              {!text && <button className="btn sm" onClick={() => setText(SAMPLE)}>Try an example</button>}
            </div>
            {error && <ErrorBox error={error} />}
            {result && (
              <p className="pos" role="status">
                Added {result.inserted} transaction{result.inserted === 1 ? "" : "s"}
                {result.duplicates_skipped > 0 && ` · ${result.duplicates_skipped} already in your ledger`}
                {result.skipped.length > 0 && ` · ${result.skipped.length} message${result.skipped.length === 1 ? "" : "s"} weren't transactions`}.
              </p>
            )}
          </div>
        </Card>
        <AutoForward accounts={accounts} />
      </div>

      {preview && (
        <div style={{ marginTop: 16 }}>
        <Card title={`Preview — ${preview.parsed} to add, ${preview.skipped} skipped`}>
          <div className="table-wrap">
            <table className="cards-mobile">
              <thead><tr><th>Date</th><th>Payee</th><th>Category</th><th className="r">Amount</th><th>Notes</th></tr></thead>
              <tbody>
                {preview.items.map((p, i) => p.ok ? (
                  <tr key={i}>
                    <td data-label="Date" className="num" style={{ whiteSpace: "nowrap" }}>{p.date && dateLabel(p.date)}</td>
                    <td className="cell-main">
                      <div><b>{p.merchant}</b></div>
                      <div className="small muted">{p.mode?.toUpperCase()}{p.account_last4 && ` · ••${p.account_last4}`}{p.reference && ` · ref ${p.reference}`}</div>
                    </td>
                    <td data-label="Category">{p.is_transfer ? <span className="badge">Transfer</span> : p.category}</td>
                    <td data-label="Amount" className={`r ${p.amount! > 0 ? "pos" : ""}`}>{money(p.amount)}</td>
                    <td data-label="Notes" className="small muted">{p.warnings.join(" ") || "—"}</td>
                  </tr>
                ) : (
                  <tr key={i} className="muted">
                    <td colSpan={4} className="small cell-main"><span className="ellipsis txn-desc" style={{ display: "block" }} title={p.text}>{p.text}</span></td>
                    <td className="small"><Badge kind="info">Skipped</Badge> {p.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        </div>
      )}
    </>
  );
}
