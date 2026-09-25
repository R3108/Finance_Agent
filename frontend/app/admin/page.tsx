"use client";

import { useState } from "react";
import { Badge, Card, Empty, ErrorBox, Loading, PageHead, Stat, useToast } from "@/components/ui";
import { del, post, useApi } from "@/lib/api";
import { dateLabel, inr, pct } from "@/lib/format";

type Metrics = {
  generated_at: string; window_days: number;
  users: { total: number; demo_sandboxes: number; new_in_window: number };
  revenue: {
    currency: string; mrr_subscriptions: number; mrr_prepaid_equivalent: number; mrr_total: number;
    arr_projected: number; collected_30d: number; collected_365d: number; collected_lifetime: number;
    payments: number; active_subscriptions: number; active_prepaid_passes: number;
  };
  plan_mix: { plan: string; users: number; share_pct: number }[];
  activation: { window_days: number; steps: { step: string; users: number; share_pct: number }[] };
  churn: { window_days: number; ended: number; leaving_at_period_end: number; halted_payment_failure: number; active: number; churn_pct: number | null };
  growth: {
    trials_started: number; trials_converted: number; trial_conversion_pct: number | null;
    referrals_attributed: number; referrals_rewarded: number;
    coupon_codes: number; coupon_redemptions: number; free_days_granted: number;
  };
  engagement: {
    window_days: number; users_who_asked: number; questions: number; alerts_created: number;
    alerts_read_pct: number | null; households: number; receipts: number; users_using_tax_tools: number;
  };
  retention: { cohort: string; size: number; months: { offset: number; active: number; retained_pct: number }[] }[];
  signups: { day: string; signups: number }[];
  events: { name: string; count: number; users: number }[];
};

type Coupon = {
  code: string; tier: string; days: number; max_redemptions: number | null; redeemed: number;
  expires_at: string | null; note: string | null; created_at: string;
};

const STEP_LABEL: Record<string, string> = {
  signed_up: "Signed up",
  verified_email: "Confirmed email",
  has_transactions: "Imported transactions",
  asked_the_assistant: "Asked the assistant",
  set_a_budget: "Set a budget",
  paid: "Paid",
};

function Funnel({ steps }: { steps: Metrics["activation"]["steps"] }) {
  return (
    <div className="stack">
      {steps.map((s) => (
        <div key={s.step}>
          <div className="row between small">
            <span>{STEP_LABEL[s.step] ?? s.step}</span>
            <span><b>{s.users}</b> <span className="muted">{pct(s.share_pct, 0)}</span></span>
          </div>
          <div className="bar" role="presentation">
            <span style={{ width: `${Math.min(100, s.share_pct)}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function CouponManager() {
  const { data, loading, reload } = useApi<Coupon[]>("/admin/coupons");
  const toast = useToast();
  const [form, setForm] = useState({ code: "", days: 30, tier: "pro", max_redemptions: "", note: "" });

  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      await post("/admin/coupons", {
        code: form.code, days: Number(form.days), tier: form.tier,
        max_redemptions: form.max_redemptions ? Number(form.max_redemptions) : null,
        note: form.note || null,
      });
      toast.show(`${form.code} created`);
      setForm({ code: "", days: 30, tier: "pro", max_redemptions: "", note: "" });
      reload();
    } catch (err) {
      toast.show((err as Error).message);
    }
  }

  return (
    <Card title="Coupons" hint="Codes grant free days. Discounts on a recurring price need Razorpay Offers."
          className="span-2">
      <form className="row wrap" onSubmit={create} style={{ gap: 8, marginBottom: 14 }}>
        <input className="input" placeholder="CODE" required minLength={3} maxLength={32} value={form.code}
               onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} aria-label="Code" />
        <input className="input" type="number" min={1} required style={{ width: 90 }} value={form.days}
               onChange={(e) => setForm({ ...form, days: Number(e.target.value) })} aria-label="Days" />
        <select className="select" value={form.tier} onChange={(e) => setForm({ ...form, tier: e.target.value })}
                aria-label="Tier">
          <option value="pro">Pro</option>
          <option value="family">Family</option>
        </select>
        <input className="input" type="number" min={1} placeholder="Max uses" style={{ width: 110 }}
               value={form.max_redemptions} onChange={(e) => setForm({ ...form, max_redemptions: e.target.value })}
               aria-label="Maximum redemptions" />
        <input className="input" placeholder="Note" maxLength={140} value={form.note} style={{ flex: 1, minWidth: 140 }}
               onChange={(e) => setForm({ ...form, note: e.target.value })} aria-label="Note" />
        <button className="btn primary">Create</button>
      </form>

      {loading ? <Loading h={100} /> : data?.length ? (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Code</th><th>Grants</th><th className="r">Used</th><th>Expires</th><th>Note</th><th className="r" /></tr></thead>
            <tbody>
              {data.map((c) => (
                <tr key={c.code}>
                  <td><b>{c.code}</b></td>
                  <td>{c.days} days of {c.tier}</td>
                  <td className="r">{c.redeemed}{c.max_redemptions ? ` / ${c.max_redemptions}` : ""}</td>
                  <td className="small muted">{c.expires_at ? dateLabel(c.expires_at) : "never"}</td>
                  <td className="small muted">{c.note}</td>
                  <td className="r">
                    <button className="btn sm danger" onClick={async () => {
                      await del(`/admin/coupons/${c.code}`);
                      toast.show(`${c.code} removed`);
                      reload();
                    }}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <Empty>No coupons yet.</Empty>}
      {toast.node}
    </Card>
  );
}

export default function AdminPage() {
  const [days, setDays] = useState(30);
  const { data, error, loading } = useApi<Metrics>(`/admin/metrics?days=${days}`);

  if (loading) return <Loading h={320} />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const { revenue: rev, growth: g, engagement: e, churn } = data;

  return (
    <>
      <PageHead title="Operations" sub={`Generated ${dateLabel(data.generated_at.slice(0, 10))} · revenue in ${rev.currency}`}>
        <select className="select" value={days} onChange={(ev) => setDays(Number(ev.target.value))}
                aria-label="Window">
          {[7, 30, 90, 365].map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </select>
      </PageHead>

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <Stat label="MRR (projected)" value={inr(rev.mrr_total)}
              foot={`${inr(rev.mrr_subscriptions)} subs + ${inr(rev.mrr_prepaid_equivalent)} prepaid`} />
        <Stat label="ARR (projected)" value={inr(rev.arr_projected)} foot={`${rev.active_subscriptions} active subscriptions`} />
        <Stat label={`Collected (${days}d)`} value={inr(rev.collected_30d)} foot={`${inr(rev.collected_lifetime)} lifetime`} />
        <Stat label="Accounts" value={data.users.total}
              foot={`${data.users.new_in_window} new · ${data.users.demo_sandboxes} demo sandboxes`} />
      </div>

      <div className="grid cols-3">
        <Card title="Activation funnel" hint={`Accounts created in the last ${data.activation.window_days} days`}>
          <Funnel steps={data.activation.steps} />
        </Card>

        <Card title="Plan mix">
          <div className="stack">
            {data.plan_mix.map((p) => (
              <div key={p.plan} className="row between">
                <span>{p.plan}</span>
                <span><b>{p.users}</b> <span className="muted small">{pct(p.share_pct, 0)}</span></span>
              </div>
            ))}
            <p className="small muted">
              “On free time” counts people holding a trial, coupon or referral grant — they show as
              paid tiers above, but they aren&apos;t revenue.
            </p>
          </div>
        </Card>

        <Card title="Churn" hint={`Subscriptions changed in the last ${churn.window_days} days`}>
          <div className="stack">
            <Stat label="Churn rate" value={churn.churn_pct != null ? pct(churn.churn_pct) : "—"}
                  foot={`${churn.ended} ended vs ${churn.active} active`} />
            <div className="row between small"><span>Leaving at period end</span><b>{churn.leaving_at_period_end}</b></div>
            <div className="row between small"><span>Halted (payment failed)</span><b>{churn.halted_payment_failure}</b></div>
          </div>
        </Card>

        <Card title="Growth programs">
          <div className="stack small">
            <div className="row between"><span>Trials started</span><b>{g.trials_started}</b></div>
            <div className="row between">
              <span>Trials converted</span>
              <b>{g.trials_converted}{g.trial_conversion_pct != null && <span className="muted"> · {pct(g.trial_conversion_pct, 0)}</span>}</b>
            </div>
            <div className="row between"><span>Referrals attributed</span><b>{g.referrals_attributed}</b></div>
            <div className="row between"><span>Referrals rewarded</span><b>{g.referrals_rewarded}</b></div>
            <div className="row between"><span>Coupon redemptions</span><b>{g.coupon_redemptions} / {g.coupon_codes} codes</b></div>
            <div className="row between"><span>Free days granted</span><b>{g.free_days_granted}</b></div>
          </div>
        </Card>

        <Card title="Engagement" hint={`Last ${e.window_days} days`}>
          <div className="stack small">
            <div className="row between"><span>Asked the assistant</span><b>{e.users_who_asked} people · {e.questions} questions</b></div>
            <div className="row between">
              <span>Alerts created</span>
              <b>{e.alerts_created}{e.alerts_read_pct != null && <span className="muted"> · {pct(e.alerts_read_pct, 0)} read</span>}</b>
            </div>
            <div className="row between"><span>Households</span><b>{e.households}</b></div>
            <div className="row between"><span>Using the tax tools</span><b>{e.users_using_tax_tools}</b></div>
            <div className="row between"><span>Receipts stored</span><b>{e.receipts}</b></div>
          </div>
        </Card>

        <Card title="Top events">
          {data.events.length ? (
            <div className="stack small">
              {data.events.map((ev) => (
                <div key={ev.name} className="row between">
                  <span>{ev.name}</span>
                  <span><b>{ev.count}</b> <span className="muted">{ev.users} people</span></span>
                </div>
              ))}
            </div>
          ) : <Empty>No events recorded yet.</Empty>}
        </Card>

        <Card title="Retention by signup month" className="span-3">
          {data.retention.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Cohort</th><th className="r">Size</th>
                    {[0, 1, 2, 3, 4, 5].map((m) => <th key={m} className="r">M{m}</th>)}</tr>
                </thead>
                <tbody>
                  {data.retention.map((c) => (
                    <tr key={c.cohort}>
                      <td>{c.cohort}</td>
                      <td className="r">{c.size}</td>
                      {[0, 1, 2, 3, 4, 5].map((m) => {
                        const cell = c.months.find((x) => x.offset === m);
                        return <td key={m} className="r">{cell ? <Badge kind={cell.retained_pct >= 50 ? "good" : "info"}>{pct(cell.retained_pct, 0)}</Badge> : "—"}</td>;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <Empty>Not enough history yet.</Empty>}
        </Card>

        <CouponManager />
      </div>
    </>
  );
}
