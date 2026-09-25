"use client";

import { useState } from "react";
import { useConfirm, useRemovable } from "@/components/feedback";
import SecurityCard from "@/components/SecurityCard";
import { Badge, Card, ErrorBox, PageHead, useToast } from "@/components/ui";
import { api, del, patch, post, useApi } from "@/lib/api";
import { setDisplayCurrency } from "@/lib/format";
import { type CurrencyOption, useMe } from "@/lib/session";
import type { Account } from "@/lib/types";

type Rule = { id: number; pattern: string; category: string; priority: number };

export default function Settings() {
  const rules = useApi<Rule[]>("/rules");
  const cats = useApi<string[]>("/categories");
  const overview = useApi<{ balances: { accounts: Account[] } }>("/overview");
  const health = useApi<{ llm_enabled: boolean; model: string }>("/health");
  const currencies = useApi<{ currencies: CurrencyOption[] }>("/currencies");
  const toast = useToast();
  const confirmDialog = useConfirm();
  const removeRule = useRemovable<number>();

  const [pattern, setPattern] = useState("");
  const [category, setCategory] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [account, setAccount] = useState("");
  const [expensesPositive, setExpensesPositive] = useState(false);
  const [useAi, setUseAi] = useState(true);
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const [importErr, setImportErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { me, refresh } = useMe();
  const [pw, setPw] = useState({ current: "", next: "" });
  const [pwError, setPwError] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    setPwError(null);
    try {
      await post("/auth/password", { current_password: pw.current || null, new_password: pw.next });
      setPw({ current: "", next: "" });
      toast.show("Password updated · other devices signed out");
      refresh();
    } catch (err) {
      setPwError((err as Error).message);
    }
  }

  async function addRule(e: React.FormEvent) {
    e.preventDefault();
    const r = await post<{ recategorized: number }>("/rules", { pattern, category });
    setPattern("");
    toast.show(`Rule saved · ${r.recategorized} transactions recategorised`);
    rules.reload();
  }

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setImportErr(null);
    setImportMsg(null);
    const fd = new FormData();
    fd.append("file", file);
    if (account) fd.append("account_id", account);
    fd.append("expenses_positive", String(expensesPositive));
    fd.append("use_ai", String(useAi));
    try {
      const r = await api<{ received: number; inserted: number; duplicates_skipped: number }>("/import", { method: "POST", body: fd });
      setImportMsg(`Imported ${r.inserted} of ${r.received} rows (${r.duplicates_skipped} duplicates skipped).`);
    } catch (err) {
      setImportErr((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    if (!(await confirmDialog({
      title: "Replace all your data with sample data?", danger: true, confirmLabel: "Replace my data", requireText: "REPLACE",
      body: "Your transactions, accounts, rules, budgets, goals, debts and chats will be deleted. Your login and plan are kept.",
    }))) return;
    await post("/demo/reset");
    toast.show("Sample data loaded");
    rules.reload();
  }

  async function changeCurrency(code: string) {
    await patch("/me", { currency: code });
    const picked = currencies.data?.currencies.find((c) => c.code === code);
    setDisplayCurrency(code, picked?.locale);
    toast.show(`Now showing amounts in ${code}`);
    refresh();
  }

  return (
    <>
      <PageHead title="Data & settings" />
      <div className="grid cols-2">
        <Card id="import" title="Import bank CSV" hint="Date, Description and Amount (or Debit/Credit) columns. Re-uploading the same file is safe.">
          <form className="stack" onSubmit={upload}>
            <input className="input" type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="CSV file" />
            <select className="select" value={account} onChange={(e) => setAccount(e.target.value)} aria-label="Account">
              <option value="">No specific account</option>
              {overview.data?.balances.accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
            <label className="row small"><input type="checkbox" checked={expensesPositive} onChange={(e) => setExpensesPositive(e.target.checked)} /> My bank shows purchases as positive numbers</label>
            <label className="row small"><input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /> Use AI to categorise merchants the rules don&apos;t recognise</label>
            <div><button className="btn primary" disabled={!file || busy}>{busy ? "Importing…" : "Import"}</button></div>
            {importMsg && <p className="pos">{importMsg}</p>}
            {importErr && <ErrorBox error={importErr} />}
          </form>
        </Card>

        <Card title="Currency" hint="Changes how amounts are written. Nothing is converted — your transactions keep their recorded values.">
          <div className="stack">
            <select className="select" value={me?.currency ?? ""} aria-label="Display currency"
                    onChange={(e) => changeCurrency(e.target.value)}>
              {currencies.data?.currencies.map((c) => (
                <option key={c.code} value={c.code}>{c.symbol} · {c.code} — {c.name}</option>
              ))}
            </select>
            <p className="small muted">
              The assistant, your insights and every export follow this setting. Plan prices stay in rupees,
              because billing is charged in INR.
            </p>
            <form className="stack" style={{ gap: 6 }} onSubmit={async (e) => {
              e.preventDefault();
              const value = (new FormData(e.currentTarget).get("vpa") as string) ?? "";
              try {
                await patch("/me", { upi_vpa: value });
                toast.show(value ? "UPI id saved" : "UPI id removed");
                refresh();
              } catch (err) {
                toast.show((err as Error).message);
              }
            }}>
              <span className="small">Your UPI id <span className="muted">— put in split reminders so friends can pay you back in one tap</span></span>
              <div className="row">
                <input className="input" name="vpa" key={me?.upi_vpa ?? "none"} defaultValue={me?.upi_vpa ?? ""}
                       placeholder="yourname@okicici" aria-label="Your UPI id" style={{ flex: 1 }} />
                <button className="btn">Save</button>
              </div>
            </form>
          </div>
        </Card>

        <Card title="AI assistant">
          <p>Status: <b>{health.data ? (health.data.llm_enabled ? `Online · ${health.data.model}` : "Offline (keyword mode)") : "…"}</b></p>
          <p className="small muted" style={{ marginTop: 8 }}>
            Set <code>OPENAI_API_KEY</code> in <code>backend/.env</code> and restart the API to enable free-form questions.
            The model only chooses which analytics to run and how to explain them — all numbers come from Python.
          </p>
          <div className="row wrap" style={{ marginTop: 16 }}>
            <a className="btn" href="/api/export.csv">Export all transactions</a>
            <button className="btn danger" onClick={reset}>Replace with sample data</button>
          </div>
        </Card>

        {me && !me.is_demo && (
          <Card title="Sign-in & security" className="span-2">
            <div className="grid cols-2">
              <div className="stack small">
                <div className="row between"><span>Email</span><span>{me.email} {me.email_verified ? <Badge kind="good">Verified</Badge> : <Badge kind="medium">Not verified</Badge>}</span></div>
                <div className="row between"><span>Google</span>{me.google_linked ? <Badge kind="good">Connected</Badge> : <span className="muted">Not connected. Sign in with Google once using the same email to link it.</span>}</div>
                <div className="row between"><span>Password</span>{me.has_password ? <Badge kind="good">Set</Badge> : <span className="muted">None (you sign in with Google)</span>}</div>
              </div>
              <form className="stack" onSubmit={changePassword}>
                <h3>{me.has_password ? "Change password" : "Add a password"}</h3>
                {me.has_password && <input className="input" type="password" required autoComplete="current-password" placeholder="Current password"
                  value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} aria-label="Current password" />}
                <input className="input" type="password" required minLength={8} autoComplete="new-password" placeholder="New password (8+ characters)"
                  value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} aria-label="New password" />
                {pwError && <div className="error" role="alert">{pwError}</div>}
                <div><button className="btn primary">{me.has_password ? "Change password" : "Add password"}</button></div>
                <p className="small muted">Other devices are signed out and you get an email confirming the change.</p>
              </form>
            </div>
          </Card>
        )}

        {me && !me.is_demo && <SecurityCard onChange={refresh} />}

        <Card title="Categorisation rules" hint="Case-insensitive text match on the merchant or description. Your rules beat the built-in ones." className="span-2">
          <form className="row wrap" onSubmit={addRule} style={{ marginBottom: 14 }}>
            <input className="input" required minLength={2} placeholder='Text to match, e.g. "Costco"' value={pattern} onChange={(e) => setPattern(e.target.value)} style={{ flex: "1 1 220px" }} />
            <select className="select" required value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
              <option value="">Category…</option>
              {cats.data?.map((c) => <option key={c}>{c}</option>)}
            </select>
            <button className="btn primary">Add rule</button>
          </form>
          {rules.data?.length === 0 ? <p className="muted">No custom rules yet. Changing a transaction&apos;s category with “Learn from my edits” on creates one.</p> : (
            <table>
              <thead><tr><th>Match</th><th>Category</th><th /></tr></thead>
              <tbody>
                {rules.data?.filter(removeRule.visible((r: Rule) => r.id)).map((r) => (
                  <tr key={r.id}>
                    <td><code>{r.pattern}</code></td>
                    <td>{r.category}</td>
                    <td className="r"><button className="btn sm danger" onClick={() =>
                      removeRule(r.id, `Rule “${r.pattern}” removed`, () => del(`/rules/${r.id}`).then(() => rules.reload()))
                    }>Remove</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        {me && !me.is_demo && (
          <Card title="Your data" hint="Take it with you, or remove it entirely." className="span-2">
            <div className="grid cols-2">
              <div className="stack">
                <h3>Download everything</h3>
                <p className="small muted">
                  A single JSON file with every account, transaction, budget, goal, alert, tag and
                  receipt record we hold. Passwords and session tokens are excluded — they&apos;re
                  secrets, not data about you.
                </p>
                <div><a className="btn" href="/api/privacy/export.json">Download my data</a></div>
              </div>
              <div className="stack">
                <h3>Delete this account</h3>
                <p className="small muted">
                  Permanently removes your account, every transaction and every uploaded receipt.
                  This cannot be undone and there is no grace period.
                </p>
                <form className="row wrap" style={{ gap: 8 }} onSubmit={async (e) => {
                  e.preventDefault();
                  if (!(await confirmDialog({
                    title: "Delete your account permanently?", danger: true, confirmLabel: "Delete forever",
                    body: "Every transaction, receipt and setting is erased immediately. This cannot be undone. Download your data first if you might want it.",
                  }))) return;
                  try {
                    await post("/privacy/delete", { confirm: confirmText });
                    window.location.assign("/login");
                  } catch (err) {
                    setDeleteError((err as Error).message);
                  }
                }}>
                  <input className="input" value={confirmText} placeholder="Type DELETE"
                         onChange={(e) => setConfirmText(e.target.value)} aria-label="Type DELETE to confirm" />
                  <button className="btn danger" disabled={confirmText.trim().toUpperCase() !== "DELETE"}>
                    Delete my account
                  </button>
                </form>
                {deleteError && <ErrorBox error={deleteError} />}
              </div>
            </div>
          </Card>
        )}
      </div>
      {toast.node}
    </>
  );
}
