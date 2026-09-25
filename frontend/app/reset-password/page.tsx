"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import AuthFrame from "@/components/AuthFrame";
import { post } from "@/lib/api";

function Reset() {
  const router = useRouter();
  const token = useSearchParams().get("token");
  const [pw, setPw] = useState({ password: "", confirm: "" });
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pw.password !== pw.confirm) return setError("The two passwords don't match.");
    setBusy(true);
    setError(null);
    try {
      const r = await post<{ mfa_required?: boolean; mfa_token?: string }>("/auth/reset", { token, password: pw.password });
      // with two-factor sign-in on, the new password is saved but the authenticator code is still needed
      if (r.mfa_required && r.mfa_token) router.replace(`/login?mfa_token=${encodeURIComponent(r.mfa_token)}`);
      else router.replace("/"); // the reset also signs you in (and signs out every other device)
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <AuthFrame>
      <h2 className="auth-title">Choose a new password</h2>
      {!token ? (
        <div className="error" role="alert">This link is missing its token. Open the link from your email again, or request a new one.</div>
      ) : (
        <form className="stack" style={{ gap: 12 }} onSubmit={submit}>
          <label className="field">
            <span className="row between">New password
              <button type="button" className="link small" onClick={() => setShow(!show)} aria-pressed={show}>{show ? "Hide" : "Show"}</button>
            </span>
            <input className="input" type={show ? "text" : "password"} required minLength={8} autoFocus autoComplete="new-password"
              value={pw.password} onChange={(e) => setPw({ ...pw, password: e.target.value })} />
            <span className="small muted">At least 8 characters. You&apos;ll be signed out on all other devices.</span>
          </label>
          <label className="field">
            <span>Confirm new password</span>
            <input className="input" type={show ? "text" : "password"} required minLength={8} autoComplete="new-password"
              value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} />
          </label>
          {error && <div className="error" role="alert">{error}</div>}
          <button className="btn primary auth-submit" disabled={busy}>{busy ? "Saving…" : "Set password and sign in"}</button>
        </form>
      )}
      <p className="small" style={{ marginTop: 18, textAlign: "center" }}>
        <Link className="link" href="/forgot-password">Request a new link</Link> · <Link className="link" href="/login">Sign in</Link>
      </p>
    </AuthFrame>
  );
}

export default function ResetPasswordPage() {
  return <Suspense fallback={null}><Reset /></Suspense>;
}
