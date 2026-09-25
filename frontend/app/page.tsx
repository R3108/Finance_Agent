"use client";

import Link from "next/link";
import { CategoryBars, IncomeSpendChart } from "@/components/charts";
import { Badge, Card, ErrorBox, Loading, PageHead, Stat } from "@/components/ui";
import { useApi } from "@/lib/api";
import DashboardTools from "@/components/DashboardTools";
import { CountUp } from "@/components/motion";
import { EmptyDashboard, GettingStarted } from "@/components/Onboarding";
import { useMe, useScoped } from "@/lib/session";
import { dateLabel, money, monthLabel, pct, shortDate } from "@/lib/format";
import type { Overview } from "@/lib/types";

export default function Dashboard() {
  const { me } = useMe();
  // a brand-new account gets a welcome with ways to add data, not a page of ₹0 charts
  if (me && me.transactions === 0 && !me.in_household) return <EmptyDashboard />;
  return <FullDashboard />;
}

function FullDashboard() {
  const scoped = useScoped();
  const { data, error } = useApi<Overview>(scoped("/overview"));
  if (error) return <ErrorBox error={error} />;
  if (!data) return <div className="grid"><Loading h={40} /><div className="grid cols-4">{[0, 1, 2, 3].map((i) => <Loading key={i} />)}</div><Loading h={300} /></div>;

  const cur = data.current_month;
  const lastFull = data.monthly.filter((m) => !m.partial).at(-1);
  const sts = data.safe_to_spend;

  return (
    <>
      <PageHead
        title={data.household ? data.household.name : "Dashboard"}
        sub={`${data.household ? "Everyone's shared accounts · " : ""}Data through ${dateLabel(data.as_of)}`}
      >
        <Link href="/assistant" className="btn primary">Ask the assistant</Link>
      </PageHead>

      <GettingStarted />

      {data.household && data.household.split.length > 1 && (
        <Card title="Who spent what" hint="Across the shared accounts, over the loaded period"
              action={<Link className="small muted" href="/household">Manage household →</Link>}>
          <div className="grid cols-4">
            {data.household.split.map((m) => (
              <Stat key={m.user_id} label={m.name} value={money(m.spent)}
                    foot={m.share_pct != null ? `${pct(m.share_pct)} of the total` : undefined} />
            ))}
          </div>
        </Card>
      )}

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <Stat label="Net cash (all accounts)" value={<CountUp value={data.balances.net_cash} format={money} />} foot={`${money(data.balances.liquid_cash)} in checking + savings`} />
        <Stat label={`Spent in ${monthLabel(cur.month)} so far`} value={<CountUp value={cur.total} format={money} />} foot={lastFull ? `${money(lastFull.spending)} last month` : undefined} />
        <Stat label="Safe to spend this month" value={<CountUp value={sts.safe_to_spend} format={money} />} tone={sts.safe_to_spend < 0 ? "neg" : undefined}
          foot={sts.safe_to_spend > 0 ? `≈ ${money(sts.per_day)}/day for ${sts.days_left} days` : `after ${pct(sts.savings_target_pct, 0)} savings target`} />
        <Stat label="Financial health" value={<><CountUp value={data.health.score} /><span className="muted" style={{ fontSize: 16 }}>/100 · {data.health.grade}</span></>}
          foot={`Savings rate ${pct(data.health.metrics.savings_rate_pct)} (3 mo)`} />
      </div>

      {data.scope === "personal" && <DashboardTools />}

      <div className="grid cols-3" style={{ marginBottom: 16 }}>
        <Card title="Income vs spending" hint="Last 12 months · current month is partial" className="span-2">
          <IncomeSpendChart data={data.monthly} />
        </Card>
        <Card title={`Where it went · ${monthLabel(cur.month)}`} action={<Link className="small muted" href="/insights">Trends →</Link>}>
          <CategoryBars rows={cur.categories} />
        </Card>
      </div>

      <div className="grid cols-3">
        <Card title="Needs your attention" hint="Ranked by severity and money at stake" className="span-2"
          action={<Link className="small muted" href="/insights">All insights →</Link>}>
          {data.insights.map((i, idx) => (
            <div className="insight" key={idx}>
              <span className={`dot ${i.severity}`} aria-hidden />
              <div>
                <div className="title">{i.title}</div>
                <div className="detail">{i.detail}</div>
              </div>
              <div className="stack" style={{ alignItems: "flex-end", gap: 4 }}>
                <Badge kind={i.severity} />
                {i.impact_monthly > 0 && <span className="small muted num">{money(i.impact_monthly)}/mo</span>}
              </div>
            </div>
          ))}
        </Card>

        <div className="stack" style={{ gap: 16 }}>
          <Card title="Upcoming charges" hint="Rest of this month">
            {sts.upcoming_charges.length === 0 ? <p className="muted">Nothing else scheduled this month.</p> : (
              <div className="stack" style={{ gap: 8 }}>
                {sts.upcoming_charges.map((c, i) => (
                  <div key={i} className="row between">
                    <span><span className="muted small" style={{ display: "inline-block", width: 52 }}>{shortDate(c.date)}</span>{c.merchant}</span>
                    <b className="num">{money(c.amount)}</b>
                  </div>
                ))}
              </div>
            )}
          </Card>
          <Card title="Subscriptions" action={<Link className="small muted" href="/subscriptions">Manage →</Link>}>
            <div className="stat-value"><CountUp value={data.subscriptions.subscriptions_monthly_total} format={money} /><span className="muted" style={{ fontSize: 14 }}>/mo</span></div>
            <div className="stat-foot">{data.subscriptions.active_subscriptions} active · {money(data.subscriptions.subscriptions_annual_total)}/yr</div>
          </Card>
          <Card title="Accounts">
            <div className="stack" style={{ gap: 8 }}>
              {data.balances.accounts.map((a) => (
                <div key={a.id} className="row between">
                  <span>{a.name} <span className="muted small">· {a.institution}</span></span>
                  <b className={`num ${a.balance < 0 ? "neg" : ""}`}>{money(a.balance)}</b>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}
