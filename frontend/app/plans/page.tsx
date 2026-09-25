"use client";

import { useEffect, useState } from "react";
import { useConfirm } from "@/components/feedback";
import { Badge, Card, ErrorBox, Loading, PageHead, Progress, useToast } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import { dateLabel, inr } from "@/lib/format";
import { payWithRazorpay } from "@/lib/razorpay";
import { useMe } from "@/lib/session";
import type { BillingStatus, CheckoutSession, PlanCatalog } from "@/lib/types";

const STATUS_LABEL: Record<string, { kind: string; label: string }> = {
  active: { kind: "good", label: "Active" },
  authenticated: { kind: "good", label: "Active" },
  pending: { kind: "medium", label: "Payment retrying" },
  halted: { kind: "high", label: "Payment failed" },
  cancelled: { kind: "info", label: "Cancelled" },
  completed: { kind: "info", label: "Completed" },
  expired: { kind: "info", label: "Expired" },
  paused: { kind: "medium", label: "Paused" },
};
const METHOD: Record<string, string> = { upi: "UPI", card: "Card", netbanking: "Netbanking", wallet: "Wallet", emandate: "eMandate" };
const unixDate = (s: number | null) => (s ? dateLabel(new Date(s * 1000).toISOString().slice(0, 10)) : "—");

export default function Plans() {
  const cat = useApi<PlanCatalog>("/plans");
  const bill = useApi<BillingStatus>("/billing");
  const [annual, setAnnual] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const confirmDialog = useConfirm();
  const { me, refresh } = useMe();
  const { reload: reloadCat } = cat;
  const { reload: reloadBill } = bill;

  // keys added/removed in .env, or a webhook changed the subscription in another tab: pick it up on focus
  useEffect(() => {
    const onFocus = () => { reloadBill(); reloadCat(); };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [reloadBill, reloadCat]);

  const refreshAll = () => { cat.reload(); bill.reload(); refresh(); };

  async function upgrade(plan: string) {
    setBusy(plan);
    setError(null);
    try {
      const session = await post<CheckoutSession>("/billing/checkout", { plan, period: annual ? "annual" : "monthly" });
      const result = await payWithRazorpay(session);
      if ("closed" in result) {
        if (result.lastError) setError(`Payment didn't go through: ${result.lastError}`);
        else toast.show("Checkout closed. You haven't been charged.");
        return;
      }
      setBusy("verifying");
      const st = await post<BillingStatus>("/billing/verify", result.paid); // unlocks the plan immediately
      toast.show(st.plan === "free" ? "Payment received. Activating your plan…"
        : st.pass && session.mode === "order" ? `Pro is active until ${unixDate(st.pass.ends_at)} 🎉` : "Welcome to Pro! 🎉");
      refreshAll();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function cancel() {
    const end = unixDate(bill.data?.subscription?.current_period_end ?? null);
    if (!(await confirmDialog({
      title: "Cancel your subscription?", danger: true, confirmLabel: "Cancel subscription", cancelLabel: "Keep Pro",
      body: `You keep Pro until ${end} and won't be charged again. You can resubscribe any time.`,
    }))) return;
    setBusy("cancel");
    setError(null);
    try {
      await post("/billing/cancel");
      toast.show(`Cancelled. Pro stays active until ${end}.`);
      refreshAll();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (cat.error) return <ErrorBox error={cat.error} />;
  const d = cat.data;
  const b = bill.data;
  const sub = b?.subscription;
  const pass = b?.pass;
  const prepaid = b?.mode === "prepaid";
  const usage = d?.usage;
  const payable = !!b?.configured;
  const periodWord = annual ? "1 year" : "1 month";
  const blockedReason = me?.is_demo ? "Create an account to subscribe" : me && !me.email_verified ? "Verify your email to upgrade" : null;
  const maxSaving = Math.max(0, ...(d?.plans.map((p) => p.annual_saving_pct) ?? [0]));

  return (
    <>
      <PageHead title="Plans" sub="Start free. Upgrade when you want planning tools and unlimited AI.">
        <div className="row" role="group" aria-label="Billing period">
          <button className={`chip ${!annual ? "on" : ""}`} aria-pressed={!annual} onClick={() => setAnnual(false)}>Monthly</button>
          <button className={`chip ${annual ? "on" : ""}`} aria-pressed={annual} onClick={() => setAnnual(true)}>Yearly{maxSaving ? ` · save ${maxSaving}%` : ""}</button>
        </div>
      </PageHead>

      {error && <div className="error" role="alert" style={{ marginBottom: 16 }}>{error}</div>}
      {b && !b.configured && (
        <div className="demo-banner" role="status">Payments are currently unavailable, so upgrades are paused. Existing plans are unaffected.</div>
      )}
      {b?.test_mode && <div className="demo-banner" role="status"><span><b>Razorpay test mode:</b> no real money moves. Use test UPI <code>success@razorpay</code> or a Razorpay test card.</span></div>}

      {usage && usage.ai_questions_limit != null && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="row between small" style={{ marginBottom: 6 }}>
            <span>AI questions this month</span>
            <b className="num">{usage.ai_questions_this_month} / {usage.ai_questions_limit}</b>
          </div>
          <Progress value={(usage.ai_questions_this_month / usage.ai_questions_limit) * 100}
            status={usage.ai_questions_this_month >= usage.ai_questions_limit ? "over" : "on_track"} />
        </div>
      )}

      {!d ? <Loading h={400} /> : (
        <div className="grid cols-3" style={{ marginBottom: 16 }}>
          {d.plans.map((p) => {
            const current = d.current === p.id;
            const perMonth = annual ? p.price_annual / 12 : p.price_monthly;
            const lower = d.plans.findIndex((x) => x.id === p.id) < d.plans.findIndex((x) => x.id === d.current);
            let label: string;
            let action: (() => void) | null = null;
            const price = inr(annual ? p.price_annual : p.price_monthly);
            if (current && pass && !sub?.has_access && payable && !blockedReason) {
              // prepaid pass holder: renewing early adds time after the current pass
              label = busy === p.id ? "Opening checkout…" : busy === "verifying" ? "Confirming payment…" : `Extend by ${periodWord} · ${price}`;
              action = () => upgrade(p.id);
            } else if (current) label = sub?.cancel_at_period_end ? `Ends ${unixDate(sub.current_period_end)}` : "Your plan";
            else if (p.coming_soon) label = "Coming soon";
            else if (p.id === "free") label = lower && sub?.has_access && !sub.cancel_at_period_end ? "Cancel to downgrade ↓" : "Included";
            else if (lower) label = `Included in ${d.plans.find((x) => x.id === d.current)?.name}`;
            else if (!payable) label = "Payments unavailable";
            else if (blockedReason) label = blockedReason;
            else {
              label = busy === p.id ? "Opening checkout…" : busy === "verifying" ? "Confirming payment…"
                : prepaid ? `Get ${p.name} for ${periodWord} · ${price}` : `Upgrade to ${p.name}`;
              action = () => upgrade(p.id);
            }
            return (
              <section key={p.id} className={`card plan ${current ? "current" : ""}`}>
                <div className="row between">
                  <h2>{p.name}</h2>
                  {current ? <Badge kind="accent">Current plan</Badge> : p.coming_soon ? <Badge kind="info">Coming soon</Badge> : null}
                </div>
                <div>
                  <span className="price">{inr(Math.round(perMonth))}</span><span className="muted"> /month</span>
                  {p.price_monthly > 0 && <div className="small muted">
                    {prepaid
                      ? `${annual ? `${inr(p.price_annual)} for 1 year` : "for 1 month"} · one-time payment, no auto-renew`
                      : annual ? `${inr(p.price_annual)} billed yearly` : "billed monthly · cancel any time"}
                  </div>}
                  {current && pass && !sub?.has_access && <div className="small" style={{ marginTop: 4 }}>
                    Active until <b>{unixDate(pass.ends_at)}</b> ({pass.days_left} day{pass.days_left === 1 ? "" : "s"} left)</div>}
                </div>
                <p className="muted">{p.tagline}</p>
                <button className={`btn ${action ? "primary" : ""}`} style={{ justifyContent: "center" }}
                  disabled={!action || busy !== null} onClick={action ?? undefined}>{label}</button>
                <ul>
                  <li><b>{p.limits.ai_questions_per_month == null ? "Unlimited" : p.limits.ai_questions_per_month} AI questions / month</b></li>
                  {p.features.filter((f) => f.key !== "unlimited_ai").map((f) => (
                    <li key={f.key} className={f.included ? "" : "off"}>
                      <span aria-hidden>{f.included ? "✓ " : "– "}</span>{f.label}
                      {!f.included && <span className="sr-only"> (not included)</span>}
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}

      {pass && !sub?.has_access && (
        <Card title="Billing" hint="Payments are processed securely by Razorpay. Ledgerly never sees your card or UPI details.">
          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <div><div className="stat-label">Plan</div><div><b>{pass.tier === "pro" ? "Pro" : pass.tier}</b> · prepaid</div></div>
            <div><div className="stat-label">Status</div><Badge kind={pass.days_left <= 3 ? "medium" : "good"}>{pass.days_left <= 3 ? "Ending soon" : "Active"}</Badge></div>
            <div><div className="stat-label">Pro until</div><div>{unixDate(pass.ends_at)}</div></div>
            <div><div className="stat-label">Renewal</div><div>Manual · no auto-charge</div></div>
          </div>
          <p className="small muted" style={{ marginBottom: 12 }}>
            We&apos;ll email you 3 days before it ends. Renewing early adds the new period after {unixDate(pass.ends_at)}, so you never lose days.
            {b?.mode === "subscription" && " Auto-renew is now available: subscribing starts billing only when your prepaid time runs out."}
          </p>
          <PaymentHistory payments={b!.payments} />
        </Card>
      )}

      {sub && (
        <Card title="Billing" hint="Payments are processed securely by Razorpay. Ledgerly never sees your card or UPI details."
          action={sub.has_access && !sub.cancel_at_period_end && (
            <button className="btn danger sm" onClick={cancel} disabled={busy !== null}>{busy === "cancel" ? "Cancelling…" : "Cancel subscription"}</button>)}>
          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            <div><div className="stat-label">Plan</div><div><b>{sub.tier === "pro" ? "Pro" : sub.tier}</b> · {sub.period === "annual" ? "Yearly" : "Monthly"}</div></div>
            <div><div className="stat-label">Status</div><Badge kind={STATUS_LABEL[sub.status]?.kind ?? "info"}>{STATUS_LABEL[sub.status]?.label ?? sub.status}</Badge></div>
            <div><div className="stat-label">{sub.cancel_at_period_end || !sub.has_access ? "Access until" : "Renews on"}</div><div>{unixDate(sub.current_period_end)}</div></div>
            <div><div className="stat-label">Price</div><div>{inr(sub.amount)} / {sub.period === "annual" ? "year" : "month"}</div></div>
          </div>
          {sub.status === "pending" && <p className="small" style={{ marginBottom: 12 }}>Your last renewal payment didn&apos;t go through. Razorpay will retry automatically; you keep Pro meanwhile.{sub.manage_url && <> <a className="link" href={sub.manage_url} target="_blank" rel="noreferrer">Update payment method ↗</a></>}</p>}
          {sub.status === "halted" && <p className="small" style={{ marginBottom: 12 }}>Renewal payments failed, so the plan was paused. Subscribe again above to restore Pro.</p>}
          <PaymentHistory payments={b!.payments} />
        </Card>
      )}
      <p className="small muted" style={{ marginTop: 14 }}>
        {prepaid
          ? "Pro is sold as prepaid time: pay once by UPI, card, netbanking or wallet. Nothing renews or charges automatically; we email you before it ends."
          : "Subscriptions renew automatically (UPI Autopay, card or eMandate) until cancelled. Cancel any time; you keep access until the end of the paid period."}
      </p>
      {toast.node}
    </>
  );
}

function PaymentHistory({ payments }: { payments: BillingStatus["payments"] }) {
  return (
    <>
      <h3 style={{ marginBottom: 8 }}>Payment history</h3>
      {payments.length === 0 ? <p className="muted small">No payments yet.</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Date</th><th>Payment ID</th><th>Method</th><th>Status</th><th className="r">Amount</th></tr></thead>
            <tbody>
              {payments.map((p) => (
                <tr key={p.id}>
                  <td>{unixDate(p.date)}</td>
                  <td><code className="small">{p.id}</code></td>
                  <td>{p.method ? METHOD[p.method] ?? p.method : "—"}</td>
                  <td><Badge kind={p.status === "captured" ? "good" : p.status === "failed" ? "high" : "info"}>{p.status === "captured" ? "Paid" : p.status}</Badge></td>
                  <td className="r">{inr(p.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
