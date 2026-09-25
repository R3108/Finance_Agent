"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import AuthFrame from "@/components/AuthFrame";
import { api, post } from "@/lib/api";

function Forgot() {
  const params = useSearchParams();
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [devMail, setDevMail] = useState(false);

  useEffect(() => {
    api<{ email_delivery: string }>("/auth/providers").then((p) => setDevMail(p.email_delivery === "console")).catch(() => {});
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setSent((await post<{ message: string }>("/auth/forgot", { email })).message);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthFrame>
      <h2 className="auth-title">Reset your password</h2>
      {sent ? (
        <div className="stack">
          <div className="notice" role="status">
            <b>Check your inbox.</b> {sent} The link works once and expires in 1 hour.
          </div>
          {devMail && <p className="small muted">Development mode: no email provider is configured, so the email is in the API server&apos;s console and <code>backend/data/outbox/</code>.</p>}
          <button className="btn" onClick={() => setSent(null)}>Send again</button>
        </div>
      ) : (
        <form className="stack" style={{ gap: 12 }} onSubmit={submit}>
          <p className="muted">Enter the email you signed up with and we&apos;ll send you a link to choose a new password.</p>
          <label className="field">
            <span>Email</span>
            <input className="input" type="email" required autoFocus autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          {error && <div className="error" role="alert">{error}</div>}
          <button className="btn primary auth-submit" disabled={busy}>{busy ? "Sending…" : "Send reset link"}</button>
        </form>
      )}
      <p className="small" style={{ marginTop: 18, textAlign: "center" }}><Link className="link" href="/login">← Back to sign in</Link></p>
    </AuthFrame>
  );
}

export default function ForgotPasswordPage() {
  return <Suspense fallback={null}><Forgot /></Suspense>;
}
