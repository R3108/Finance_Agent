"use client";

import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { Badge, Card, useToast } from "@/components/ui";
import { del, post, useApi } from "@/lib/api";
import { dateLabel } from "@/lib/format";
import type { SecurityOverview } from "@/lib/types";

const EVENT_LABEL: Record<string, string> = {
  login: "Signed in", login_google: "Signed in with Google", login_2fa: "Signed in with 2FA",
  login_recovery_code: "Signed in with a recovery code", login_failed: "Wrong password entered",
  signup: "Account created", password_changed: "Password changed", password_reset: "Password reset by email",
  mfa_enabled: "Two-factor sign-in turned on", mfa_disabled: "Two-factor sign-in turned off",
  recovery_codes_regenerated: "New recovery codes created", session_revoked: "A device was signed out",
  sessions_revoked: "All other devices signed out",
};
const RISKY = new Set(["login_failed", "mfa_disabled", "login_recovery_code", "password_reset"]);

const when = (ts: string) => `${dateLabel(ts.slice(0, 10))} ${ts.slice(11, 16)} UTC`;

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const text = `Ledgerly recovery codes — each works once.\n\n${codes.join("\n")}\n`;
  return (
    <div className="stack small">
      <b>Save these recovery codes now. They won&apos;t be shown again.</b>
      <p className="muted">If you lose your phone, each code signs you in once in place of an authenticator code.</p>
      <div className="codes secret">{codes.map((c) => <span key={c}>{c}</span>)}</div>
      <div className="row wrap">
        <a className="btn sm" download="ledgerly-recovery-codes.txt" href={`data:text/plain;charset=utf-8,${encodeURIComponent(text)}`}>Download</a>
        <button className="btn sm" onClick={() => navigator.clipboard?.writeText(text)}>Copy</button>
        <button className="btn sm primary" onClick={onDone}>I&apos;ve saved them</button>
      </div>
    </div>
  );
}

export default function SecurityCard({ onChange }: { onChange?: () => void }) {
  const { data, reload } = useApi<SecurityOverview>("/auth/security");
  const toast = useToast();
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [action, setAction] = useState<"disable" | "regenerate" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!setup) return setQr(null);
    QRCode.toDataURL(setup.otpauth_uri, { margin: 0, width: 300 }).then(setQr).catch(() => setQr(null));
  }, [setup]);

  async function run(fn: () => Promise<void>) {
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  if (!data) return null;
  const on = data.mfa.enabled;

  return (
    <Card title="Two-factor sign-in & devices" className="span-2"
          hint="A code from your phone on every sign-in, so a leaked password alone can't open your finances.">
      <div className="grid cols-2">
        <div className="stack">
          <div className="row between">
            <h3>Authenticator app</h3>
            {on ? <Badge kind="good">On</Badge> : <Badge kind="medium">Off</Badge>}
          </div>

          {codes ? (
            <RecoveryCodes codes={codes} onDone={() => { setCodes(null); reload(); onChange?.(); }} />
          ) : on ? (
            <div className="stack small">
              <p className="muted">
                On since {data.mfa.enabled_at && dateLabel(data.mfa.enabled_at.slice(0, 10))}.{" "}
                {data.mfa.recovery_codes_left} recovery code{data.mfa.recovery_codes_left === 1 ? "" : "s"} left
                {data.mfa.recovery_codes_left <= 3 && " — create new ones soon"}.
              </p>
              {action ? (
                <form className="row wrap" onSubmit={(e) => {
                  e.preventDefault();
                  run(async () => {
                    if (action === "disable") {
                      await post("/auth/mfa/disable", { code });
                      toast.show("Two-factor sign-in turned off");
                      onChange?.();
                    } else {
                      setCodes((await post<{ recovery_codes: string[] }>("/auth/mfa/recovery-codes", { code })).recovery_codes);
                    }
                    setAction(null);
                    setCode("");
                    reload();
                  });
                }}>
                  <input className="input" autoFocus autoComplete="one-time-code" placeholder="Code from your app" value={code}
                         onChange={(e) => setCode(e.target.value)} aria-label="Authenticator code" style={{ flex: "1 1 160px" }} />
                  <button className={`btn ${action === "disable" ? "danger" : "primary"}`}>{action === "disable" ? "Turn off" : "Create new codes"}</button>
                  <button type="button" className="btn" onClick={() => { setAction(null); setError(null); }}>Cancel</button>
                </form>
              ) : (
                <div className="row wrap">
                  <button className="btn" onClick={() => setAction("regenerate")}>New recovery codes</button>
                  <button className="btn danger" onClick={() => setAction("disable")}>Turn off</button>
                </div>
              )}
            </div>
          ) : setup ? (
            <div className="stack small">
              <ol className="steps">
                <li>Open Google Authenticator, Microsoft Authenticator, Authy or 1Password and scan this code.</li>
                <li>Enter the 6-digit code it shows to finish.</li>
              </ol>
              <div className="row wrap" style={{ alignItems: "flex-start" }}>
                {qr ? <img className="qr" src={qr} alt="QR code for your authenticator app" /> : <div className="qr" />}
                <div className="stack" style={{ flex: "1 1 180px" }}>
                  <span className="muted">Can&apos;t scan? Enter this key:</span>
                  <div className="secret">{setup.secret.match(/.{1,4}/g)?.join(" ")}</div>
                  <a className="link" href={setup.otpauth_uri}>Open in authenticator app (on this phone)</a>
                </div>
              </div>
              <form className="row wrap" onSubmit={(e) => {
                e.preventDefault();
                run(async () => {
                  const r = await post<{ recovery_codes: string[] }>("/auth/mfa/enable", { code });
                  setCodes(r.recovery_codes);
                  setSetup(null);
                  setCode("");
                  toast.show("Two-factor sign-in is on");
                });
              }}>
                <input className="input" autoFocus inputMode="numeric" autoComplete="one-time-code" placeholder="123456" maxLength={7}
                       value={code} onChange={(e) => setCode(e.target.value)} aria-label="Code from your app" />
                <button className="btn primary" disabled={code.replace(/\D/g, "").length !== 6}>Turn on</button>
                <button type="button" className="btn" onClick={() => { setSetup(null); setCode(""); setError(null); }}>Cancel</button>
              </form>
            </div>
          ) : (
            <div className="stack small">
              <p className="muted">Recommended for everyone who connects real bank data. Takes about a minute.</p>
              <div><button className="btn primary" onClick={() => run(async () => setSetup(await post("/auth/mfa/setup")))}>Set up two-factor sign-in</button></div>
            </div>
          )}
          {error && <div className="error" role="alert">{error}</div>}
        </div>

        <div className="stack">
          <div className="row between">
            <h3>Signed-in devices</h3>
            {data.sessions.length > 1 && (
              <button className="btn sm" onClick={() => run(async () => {
                const r = await post<{ signed_out: number }>("/auth/sessions/revoke-others");
                toast.show(`Signed out ${r.signed_out} other device${r.signed_out === 1 ? "" : "s"}`);
                reload();
              })}>Sign out all others</button>
            )}
          </div>
          <div className="stack small">
            {data.sessions.map((s) => (
              <div key={s.id} className="row between">
                <span>
                  <b>{s.device}</b> {s.current && <Badge kind="good">This device</Badge>}
                  <div className="muted">Last active {when(s.last_seen_at)}{s.ip && ` · ${s.ip}`}</div>
                </span>
                {!s.current && (
                  <button className="btn sm" onClick={() => run(async () => { await del(`/auth/sessions/${s.id}`); toast.show("Signed out"); reload(); })}>
                    Sign out
                  </button>
                )}
              </div>
            ))}
          </div>

          <h3 style={{ marginTop: 8 }}>Recent security activity</h3>
          {data.events.length ? (
            <div className="stack small">
              {data.events.slice(0, 8).map((e, i) => (
                <div key={i} className="row between">
                  <span className={RISKY.has(e.kind) ? "neg" : undefined}>{EVENT_LABEL[e.kind] ?? e.kind}</span>
                  <span className="muted" style={{ textAlign: "right" }}>{e.device} · {when(e.created_at)}</span>
                </div>
              ))}
            </div>
          ) : <p className="small muted">Nothing yet.</p>}
          <p className="small muted">Don&apos;t recognise something? Change your password — that signs out every device.</p>
        </div>
      </div>
      {toast.node}
    </Card>
  );
}
