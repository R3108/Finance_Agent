"use client";

import { useState } from "react";
import { Badge, Card, Empty, ErrorBox, Loading, PageHead, Stat, useToast } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import { dateLabel } from "@/lib/format";
import { useMe } from "@/lib/session";

type Rewards = {
  trial: { available: boolean; days: number; tier: string; started_at: string | null; active: boolean; days_left: number | null };
  referral: {
    code: string; link: string; reward_days: number; invited: number; rewarded: number;
    pending: number; days_earned: number;
    people: { name: string; joined: string; status: string }[];
  };
  granted: { tier: string; reason: string; ends_at: number; days_left: number } | null;
};

/** "trial" / "coupon:LAUNCH50" / "referral" -> something a person would say. */
function describeGrant(reason: string) {
  if (reason.startsWith("coupon:")) return `Coupon ${reason.slice(7)}`;
  return { trial: "Free trial", referral: "Referral reward", grant: "Granted by support" }[reason] ?? reason;
}

export default function RewardsPage() {
  const { data, error, loading, reload } = useApi<Rewards>("/rewards");
  const { refresh } = useMe();
  const toast = useToast();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  async function act(fn: () => Promise<unknown>, message: string) {
    setBusy(true);
    try {
      await fn();
      toast.show(message);
      reload();
      refresh();
    } catch (err) {
      toast.show((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function copyLink() {
    if (!data) return;
    try {
      await navigator.clipboard.writeText(data.referral.link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.show("Couldn't copy — select the link and copy it manually.");
    }
  }

  if (loading) return <Loading h={300} />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const { trial, referral, granted } = data;

  return (
    <>
      <PageHead title="Free access" sub="Try Pro, share Ledgerly, or redeem a code." />

      {granted && (
        <div className="demo-banner" role="status" style={{ marginBottom: 16 }}>
          <span>
            <b>{describeGrant(granted.reason)}</b> — {granted.tier.toUpperCase()} access for{" "}
            {granted.days_left} more {granted.days_left === 1 ? "day" : "days"}.
          </span>
        </div>
      )}

      <div className="grid cols-2">
        <Card title="Free trial" hint={`${trial.days} days of ${trial.tier.toUpperCase()}, no card needed.`}>
          {trial.available ? (
            <div className="stack">
              <p className="small muted">
                Unlimited AI questions, the debt planner, savings challenges, Money Wrapped, the
                business and tax tools, email alerts and the weekly digest. Nothing is charged when
                it ends — you simply go back to Free.
              </p>
              <div>
                <button className="btn primary" disabled={busy}
                        onClick={() => act(() => post("/rewards/trial"), `Your ${trial.days}-day trial has started`)}>
                  Start my free trial
                </button>
              </div>
            </div>
          ) : (
            <div className="stack">
              <p>
                {trial.active
                  ? <>Your trial is running — <b>{trial.days_left} days</b> left.</>
                  : <>You&apos;ve used your free trial{trial.started_at && <> ({dateLabel(trial.started_at.slice(0, 10))})</>}.</>}
              </p>
              <div><a className="btn" href="/plans">See plans</a></div>
            </div>
          )}
        </Card>

        <Card title="Redeem a code" hint="Launch codes, partner offers and support credits.">
          <form className="stack" onSubmit={(e) => {
            e.preventDefault();
            act(() => post("/rewards/coupon", { code }), "Code applied").then(() => setCode(""));
          }}>
            <input className="input" value={code} maxLength={32} placeholder="LAUNCH50"
                   onChange={(e) => setCode(e.target.value.toUpperCase())} aria-label="Coupon code" required />
            <div><button className="btn primary" disabled={busy || code.length < 3}>Redeem</button></div>
          </form>
        </Card>

        <Card title="Invite a friend"
              hint={`You both get ${referral.reward_days} days of Pro once they confirm their email.`}
              className="span-2">
          <div className="grid cols-3" style={{ marginBottom: 14 }}>
            <Stat label="People joined" value={referral.invited} foot={`${referral.pending} not confirmed yet`} />
            <Stat label="Rewards earned" value={referral.rewarded} />
            <Stat label="Free days earned" value={referral.days_earned} />
          </div>

          <div className="row wrap" style={{ gap: 8 }}>
            <input className="input" readOnly value={referral.link} style={{ flex: 1, minWidth: 260 }}
                   onFocus={(e) => e.currentTarget.select()} aria-label="Your invite link" />
            <button className="btn primary" onClick={copyLink}>{copied ? "Copied" : "Copy link"}</button>
          </div>
          <p className="small muted" style={{ marginTop: 8 }}>
            Your code is <b>{referral.code}</b>. The reward lands once they confirm their email
            address, so sharing it around doesn&apos;t cost you anything if nobody sticks.
          </p>

          {referral.people.length > 0 ? (
            <div className="stack small" style={{ marginTop: 14 }}>
              {referral.people.map((p, i) => (
                <div key={`${p.name}-${i}`} className="row between">
                  <span>{p.name} <span className="muted">· joined {dateLabel(p.joined.slice(0, 10))}</span></span>
                  <Badge kind={p.status === "rewarded" ? "good" : "info"}>{p.status}</Badge>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ marginTop: 14 }}>
              <Empty>Nobody has used your link yet.</Empty>
            </div>
          )}
        </Card>
      </div>
      {toast.node}
    </>
  );
}
