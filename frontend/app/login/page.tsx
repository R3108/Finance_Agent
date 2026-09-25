"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import AuthFrame, { GoogleMark } from "@/components/AuthFrame";
import { api, post } from "@/lib/api";
import type { CurrencyOption } from "@/lib/session";

type Mode = "signin" | "signup";

/** Only same-site relative paths, so ?next= can't bounce users to another domain. */
function safeNext(next: string | null) {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login") ? next : "/";
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get("next"));
  const [mode, setMode] = useState<Mode>(params.get("mode") === "signup" ? "signup" : "signin");
  // ?ref= comes from a shared invite link; it rides along with the signup so both sides get credited
  const referral = params.get("ref");
  const [form, setForm] = useState({ name: "", email: "", password: "", sample_data: true, currency: "INR" });
  const [showPw, setShowPw] = useState(false);
  const [busy, setBusy] = useState<"form" | "demo" | "google" | null>(null);
  const [error, setError] = useState<string | null>(params.get("error"));
  const [google, setGoogle] = useState<boolean | null>(null); // null = still checking
  const [currencies, setCurrencies] = useState<CurrencyOption[]>([]);
  // Set after a correct password (or a Google sign-in) on an account with two-factor sign-in:
  // the page then asks for the authenticator code instead of issuing a session.
  const [mfaToken, setMfaToken] = useState<string | null>(params.get("mfa_token"));
  const [code, setCode] = useState("");

  useEffect(() => {
    api<{ currencies: CurrencyOption[] }>("/currencies").then((c) => setCurrencies(c.currencies)).catch(() => {});
  }, []);

  useEffect(() => {
    api("/me").then(() => router.replace(next)).catch(() => {}); // already signed in? skip the form
  }, [router, next]);

  // The Google button is live only while GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET exist in backend/.env.
  // Re-check whenever the tab regains focus, so adding or removing the keys shows up without a reload.
  useEffect(() => {
    const check = () => api<{ google: boolean }>("/auth/providers").then((p) => setGoogle(p.google)).catch(() => setGoogle(false));
    check();
    const onVisible = () => document.visibilityState === "visible" && check();
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy("form");
    setError(null);
    try {
      if (mode === "signup") await post("/auth/signup", { ...form, ref: referral });
      else {
        const r = await post<{ mfa_required?: boolean; mfa_token?: string }>("/auth/login", { email: form.email, password: form.password });
        if (r.mfa_required && r.mfa_token) {
          setMfaToken(r.mfa_token);
          setBusy(null);
          return;
        }
      }
      router.replace(next);
    } catch (err) {
      setError((err as Error).message);
      setBusy(null);
    }
  }

  async function submitCode(e: React.FormEvent) {
    e.preventDefault();
    setBusy("form");
    setError(null);
    try {
      await post("/auth/login/mfa", { mfa_token: mfaToken, code });
      router.replace(next);
    } catch (err) {
      const msg = (err as Error).message;
      setError(msg);
      if (msg.includes("expired")) setMfaToken(null);   // back to the password form
      setBusy(null);
    }
  }

  if (mfaToken) {
    return (
      <AuthFrame>
        <h2 className="auth-title">Two-factor sign-in</h2>
        <p className="small muted" style={{ marginBottom: 14 }}>
          Enter the 6-digit code from your authenticator app. Lost your phone? Use one of your recovery codes instead.
        </p>
        <form className="stack" style={{ gap: 12 }} onSubmit={submitCode}>
          <label className="field">
            <span>Code</span>
            <input className="input" required autoFocus autoComplete="one-time-code" inputMode="text" maxLength={20}
              placeholder="123 456" value={code} onChange={(e) => setCode(e.target.value)} />
          </label>
          {error && <div className="error" role="alert">{error}</div>}
          <button className="btn primary auth-submit" disabled={busy !== null || code.trim().length < 6}>
            {busy ? "Checking…" : "Verify and sign in"}
          </button>
          <button type="button" className="link small" onClick={() => { setMfaToken(null); setCode(""); setError(null); }}>
            ← Use a different account
          </button>
        </form>
      </AuthFrame>
    );
  }

  async function demo() {
    setBusy("demo");
    setError(null);
    try {
      // the currency picks the demo persona: INR gets Indian merchants and UPI-style descriptors
      await post(`/auth/demo?currency=${encodeURIComponent(form.currency)}`);
      router.replace("/");
    } catch (err) {
      setError((err as Error).message);
      setBusy(null);
    }
  }

  const switchMode = (m: Mode) => { setMode(m); setError(null); };

  return (
    <AuthFrame>
      <div className="auth-tabs" role="tablist" aria-label="Account">
        <button role="tab" aria-selected={mode === "signin"} className={mode === "signin" ? "on" : ""} onClick={() => switchMode("signin")}>Sign in</button>
        <button role="tab" aria-selected={mode === "signup"} className={mode === "signup" ? "on" : ""} onClick={() => switchMode("signup")}>Create account</button>
      </div>

      <h2 className="auth-title">{mode === "signin" ? "Welcome back" : "Start free — no card needed"}</h2>

      {google ? (
        // a full-page navigation: the backend redirects to Google and back
        <a className="btn auth-submit" href={`/api/auth/google/start?next=${encodeURIComponent(next)}`}
          aria-disabled={busy !== null} onClick={() => setBusy("google")}>
          <GoogleMark /> {busy === "google" ? "Redirecting to Google…" : "Continue with Google"}
        </a>
      ) : (
        <>
          <button type="button" className="btn auth-submit" disabled aria-describedby="google-off">
            <GoogleMark /> Continue with Google
          </button>
          <p id="google-off" className="small muted" style={{ marginTop: 6, textAlign: "center" }}>
            {google === null ? "Checking Google sign-in…" : "Google sign-in is currently unavailable."}
          </p>
        </>
      )}
      <div className="auth-or"><span>or use email</span></div>

      <form className="stack" style={{ gap: 12 }} onSubmit={submit}>
        {mode === "signup" && (
          <label className="field">
            <span>Name</span>
            <input className="input" required autoComplete="name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
        )}
        <label className="field">
          <span>Email</span>
          <input className="input" type="email" required autoComplete="email" autoFocus value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </label>
        <label className="field">
          <span className="row between">
            Password
            <span className="row" style={{ gap: 12 }}>
              {mode === "signin" && <Link className="link small" href={`/forgot-password${form.email ? `?email=${encodeURIComponent(form.email)}` : ""}`}>Forgot password?</Link>}
              <button type="button" className="link small" onClick={() => setShowPw(!showPw)} aria-pressed={showPw}>{showPw ? "Hide" : "Show"}</button>
            </span>
          </span>
          <input className="input" type={showPw ? "text" : "password"} required minLength={mode === "signup" ? 8 : undefined}
            autoComplete={mode === "signup" ? "new-password" : "current-password"} value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })} aria-describedby={mode === "signup" ? "pw-hint" : undefined} />
          {mode === "signup" && <span id="pw-hint" className="small muted">At least 8 characters. We&apos;ll email you a link to confirm your address.</span>}
        </label>
        {mode === "signup" && referral && (
          <p className="small pos">
            You were invited — you&apos;ll both get free Pro once you confirm your email address.
          </p>
        )}
        {mode === "signup" && (
          <label className="stack" style={{ gap: 6 }}>
            <span className="small">Currency</span>
            <select className="select" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}>
              {currencies.map((c) => <option key={c.code} value={c.code}>{c.symbol} · {c.code} — {c.name}</option>)}
            </select>
            <span className="small muted">How your amounts are written. You can change it later.</span>
          </label>
        )}
        {mode === "signup" && (
          <label className="row small">
            <input type="checkbox" checked={form.sample_data} onChange={(e) => setForm({ ...form, sample_data: e.target.checked })} />
            Fill my account with sample data so I can look around (replace it any time from Data &amp; settings)
          </label>
        )}
        {error && <div className="error" role="alert">{error}{google && mode === "signin" && error.startsWith("Incorrect") && " Signed up with Google? Use Continue with Google."}</div>}
        <button className="btn primary auth-submit" disabled={busy !== null}>
          {busy === "form" ? (mode === "signin" ? "Signing in…" : "Creating account…") : mode === "signin" ? "Sign in" : "Create account"}
        </button>
      </form>

      <div className="auth-or"><span>or</span></div>
      <button className="btn auth-submit" onClick={demo} disabled={busy !== null}>
        {busy === "demo" ? "Preparing your demo…" : "Try the demo — no account needed"}
      </button>
      <p className="small muted" style={{ marginTop: 10, textAlign: "center" }}>
        The demo is a private sandbox with 24 months of sample transactions
        {" in "}
        <select className="select inline" value={form.currency} aria-label="Demo currency"
                onChange={(e) => setForm({ ...form, currency: e.target.value })} disabled={busy !== null}>
          {currencies.map((c) => <option key={c.code} value={c.code}>{c.code}</option>)}
        </select>.
        {form.currency === "INR"
          ? " You'll see Indian merchants and UPI-style bank descriptors."
          : " You'll see US merchants and card descriptors."}
      </p>
    </AuthFrame>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
