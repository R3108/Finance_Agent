"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import AuthFrame from "@/components/AuthFrame";
import { post } from "@/lib/api";

function Verify() {
  const token = useSearchParams().get("token");
  const [state, setState] = useState<"working" | "done" | "failed">(token ? "working" : "failed");
  const [error, setError] = useState<string>("This link is missing its token.");
  const once = useRef(false); // tokens are single-use: never submit twice (React dev mode runs effects twice)

  useEffect(() => {
    if (!token || once.current) return;
    once.current = true;
    post("/auth/verify", { token })
      .then(() => setState("done"))
      .catch((e: Error) => { setError(e.message); setState("failed"); });
  }, [token]);

  return (
    <AuthFrame>
      {state === "working" && <h2 className="auth-title">Confirming your email…</h2>}
      {state === "done" && (
        <div className="stack">
          <h2 className="auth-title">Email confirmed ✓</h2>
          <p className="muted">Thanks, your address is verified. You can now upgrade your plan and recover your account by email.</p>
          <Link className="btn primary auth-submit" href="/">Go to your dashboard</Link>
        </div>
      )}
      {state === "failed" && (
        <div className="stack">
          <h2 className="auth-title">We couldn&apos;t confirm your email</h2>
          <div className="error" role="alert">{error}</div>
          <p className="muted small">Sign in and use <b>Resend link</b> on the banner at the top of the app to get a fresh one.</p>
          <Link className="btn primary auth-submit" href="/">Open Ledgerly</Link>
        </div>
      )}
    </AuthFrame>
  );
}

export default function VerifyEmailPage() {
  return <Suspense fallback={null}><Verify /></Suspense>;
}
