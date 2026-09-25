"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import AuthFrame from "@/components/AuthFrame";
import { api, post } from "@/lib/api";
import type { InvitePreview } from "@/lib/types";

function Join() {
  const router = useRouter();
  const token = useSearchParams().get("token") ?? "";
  const [preview, setPreview] = useState<InvitePreview | null>(null);
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!token) {
      setError("This link is missing its invite code. Ask for a new one.");
      return;
    }
    api<InvitePreview>(`/household/invites/preview?token=${encodeURIComponent(token)}`)
      .then(setPreview)
      .catch((e: Error) => setError(e.message));
    // the invite must be accepted by the account it was addressed to, so we need to know who's here
    api("/me").then(() => setSignedIn(true)).catch(() => setSignedIn(false));
  }, [token]);

  async function accept() {
    setBusy(true);
    setError(null);
    try {
      await post(`/household/invites/accept?token=${encodeURIComponent(token)}`);
      router.replace("/household");
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  const signInHref = `/login?next=${encodeURIComponent(`/join?token=${token}`)}`;

  return (
    <AuthFrame>
      <h1>{preview ? `Join ${preview.household}` : "Household invite"}</h1>
      {preview && (
        <p className="small muted" style={{ marginTop: 4 }}>
          {preview.invited_by ?? "Someone"} invited <b>{preview.email}</b> to join as a {preview.role}.
        </p>
      )}
      {error && <div className="error" role="alert" style={{ marginTop: 12 }}>{error}</div>}

      {preview && (
        <div className="stack" style={{ gap: 12 }}>
          <p className="small muted">
            You&apos;ll share one ledger: combined spending, budgets and goals. Accounts you mark
            private stay private — they never appear in the shared view.
          </p>
          {signedIn === false ? (
            <>
              <p className="small">Sign in as <b>{preview.email}</b> to accept.</p>
              <a className="btn primary auth-submit" href={signInHref}>Sign in to accept</a>
            </>
          ) : (
            <button className="btn primary auth-submit" onClick={accept} disabled={busy || signedIn === null}>
              {busy ? "Joining…" : "Accept invite"}
            </button>
          )}
        </div>
      )}

      {!preview && !error && <p className="small muted">Checking your invite…</p>}
    </AuthFrame>
  );
}

export default function JoinPage() {
  return <Suspense fallback={null}><Join /></Suspense>;
}
