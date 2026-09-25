"use client";

/**
 * The dashboard's "do something" row: an inline purchase check, where you stand with friends, and
 * whether automatic tracking is running. Each card is useful on its own and links to its full page.
 */
import Link from "next/link";
import { useState } from "react";
import { Badge, Card } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import { dateLabel, money } from "@/lib/format";
import type { AffordResult, SplitsOverview } from "@/lib/types";

const VERDICT = { comfortable: ["good", "Comfortable"], tight: ["medium", "Tight"], not_now: ["high", "Not now"] } as const;

function AffordMini() {
  const [amount, setAmount] = useState("");
  const [r, setR] = useState<AffordResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <Card title="Can I afford it?" action={<Link className="small muted" href="/afford">Full check →</Link>}>
      <form className="row" onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try { setR(await post<AffordResult>("/afford", { amount: Number(amount) })); }
        catch (err) { setError((err as Error).message); }
        finally { setBusy(false); }
      }}>
        <input className="input" type="number" min="1" step="any" required placeholder="Price" value={amount}
               onChange={(e) => { setAmount(e.target.value); setR(null); }} style={{ flex: 1 }} aria-label="Price to check" />
        <button className="btn primary" disabled={busy || !Number(amount)}>{busy ? "…" : "Check"}</button>
      </form>
      <div className="small" style={{ marginTop: 10, minHeight: 40 }} aria-live="polite">
        {error && <span className="neg">{error}</span>}
        {r ? (
          <>
            <Badge kind={VERDICT[r.verdict][0]}>{VERDICT[r.verdict][1]}</Badge>{" "}
            <span className="muted">{r.reasons[0]}</span>
            {r.wait_until && <div style={{ marginTop: 4 }}>Fits comfortably from <b>{dateLabel(r.wait_until)}</b>.</div>}
          </>
        ) : !error && <span className="muted">Checked against your next 90 days of bills and paychecks.</span>}
      </div>
    </Card>
  );
}

function SplitsMini() {
  const { data } = useApi<SplitsOverview>("/splits");
  const open = data?.balances.filter((b) => b.direction !== "even") ?? [];
  return (
    <Card title="Split & settle" action={<Link className="small muted" href="/splits">Open →</Link>}>
      {!data ? <div className="skeleton" style={{ height: 70 }} /> : open.length ? (
        <div className="stack small" style={{ gap: 6 }}>
          <div className="row between">
            <span>Owed to you</span><b className="num pos">{money(data.owed_to_you)}</b>
          </div>
          <div className="row between">
            <span>You owe</span><b className={`num ${data.you_owe ? "neg" : ""}`}>{money(data.you_owe)}</b>
          </div>
          <div className="muted">
            {open.slice(0, 2).map((b) => `${b.person} ${b.direction === "owes_you" ? "owes you" : "is owed"} ${b.net_text}`).join(" · ")}
            {open.length > 2 && ` · +${open.length - 2} more`}
          </div>
        </div>
      ) : (
        <div className="stack small">
          <span className="muted">Shared a dinner or a cab? Split it and send a UPI pay link in one tap.</span>
          <Link className="btn sm" href="/splits" style={{ alignSelf: "flex-start" }}>Split a bill</Link>
        </div>
      )}
    </Card>
  );
}

type TokenStatus = { active: boolean; last_used_at?: string | null; allowed: boolean };

function CaptureMini() {
  const { data } = useApi<TokenStatus>("/capture/token");
  return (
    <Card title="Automatic tracking" action={<Link className="small muted" href="/capture">Manage →</Link>}>
      {!data ? <div className="skeleton" style={{ height: 70 }} /> : data.active ? (
        <div className="stack small">
          <span><Badge kind="good">On</Badge> Your phone forwards bank SMS as they arrive.</span>
          <span className="muted">
            {data.last_used_at ? `Last message ${dateLabel(data.last_used_at.slice(0, 10))}.` : "Waiting for the first message."}
          </span>
        </div>
      ) : (
        <div className="stack small">
          <span className="muted">
            {data.allowed ? "Set up once and every UPI and card payment lands here by itself."
                          : "Pro adds hands-free tracking from bank SMS. You can still paste alerts any time."}
          </span>
          <div className="row wrap" style={{ gap: 6 }}>
            <Link className="btn sm primary" href={data.allowed ? "/capture" : "/plans"}>{data.allowed ? "Set up" : "See Pro"}</Link>
            <Link className="btn sm" href="/capture">Paste SMS</Link>
          </div>
        </div>
      )}
    </Card>
  );
}

export default function DashboardTools() {
  return (
    <div className="grid cols-3" style={{ marginBottom: 16 }}>
      <AffordMini />
      <SplitsMini />
      <CaptureMini />
    </div>
  );
}
