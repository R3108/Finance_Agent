"use client";

import { useRemovable } from "@/components/feedback";
import { Badge, Card, ErrorBox, Loading, PageHead, Progress, Stat, useToast } from "@/components/ui";
import { del, post, useApi } from "@/lib/api";
import { dateLabel, money } from "@/lib/format";
import type { Challenge, ChallengeSuggestion, Challenges } from "@/lib/types";

const STATUS: Record<Challenge["status"], { kind: string; label: string }> = {
  active: { kind: "accent", label: "In progress" },
  won: { kind: "good", label: "Won" },
  lost: { kind: "high", label: "Missed" },
};
const TYPE: Record<string, string> = { no_spend: "No-spend days", category_cap: "Category cap", merchant_break: "Habit break" };

export default function ChallengesPage() {
  const ch = useApi<Challenges>("/challenges");
  const toast = useToast();
  const removeChallenge = useRemovable<number>();

  async function start(s: ChallengeSuggestion) {
    await post("/challenges", { type: s.type, title: s.title, params: s.params, duration_days: s.duration_days });
    toast.show(`Challenge started: ${s.title}`);
    ch.reload();
  }

  if (ch.error) return <ErrorBox error={ch.error} />;
  const d = ch.data;
  const shown = d?.items.filter(removeChallenge.visible((i: Challenge) => i.id)) ?? [];
  const active = shown.filter((i) => i.status === "active");
  const done = shown.filter((i) => i.status !== "active");

  return (
    <>
      <PageHead title="Savings challenges" sub="Short, winnable challenges built from your own habits and scored automatically from your transactions." />
      {!d ? <Loading h={100} /> : (
        <div className="grid cols-4" style={{ marginBottom: 16 }}>
          <Stat label="Active" value={d.active} />
          <Stat label="Won" value={d.won} foot={d.win_streak > 1 ? `${d.win_streak} wins in a row` : undefined} />
          <Stat label="Missed" value={d.lost} />
          <Stat label="Estimated savings" value={money(d.estimated_savings_total)} tone="pos" foot="vs your previous 90-day pace" />
        </div>
      )}

      <div className="grid cols-2" style={{ marginBottom: 16 }}>
        <Card title="In progress">
          {!d ? <Loading h={160} /> : active.length === 0 ? <p className="muted">No active challenges — pick one from the suggestions.</p> : (
            <div className="stack" style={{ gap: 18 }}>{active.map((c) => <ChallengeRow key={c.id} c={c} onDelete={() => removeChallenge(c.id, `Challenge “${c.title}” deleted`, () => del(`/challenges/${c.id}`).then(ch.reload))} />)}</div>
          )}
        </Card>
        <Card title="Suggested for you" hint="Based on the last 90 days">
          {!d ? <Loading h={160} /> : d.suggestions.length === 0 ? <p className="muted">You&apos;re already running every challenge we&apos;d suggest.</p> : (
            <div className="stack" style={{ gap: 0 }}>
              {d.suggestions.map((s) => (
                <div key={s.type} className="insight">
                  <span className="dot info" aria-hidden />
                  <div>
                    <div className="title">{s.title}</div>
                    <div className="detail">{s.why} Could save about <b className="pos">{money(s.projected_savings)}</b>.</div>
                  </div>
                  <button className="btn primary sm" onClick={() => start(s)}>Start</button>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {done.length > 0 && (
        <Card title="History">
          <div className="stack" style={{ gap: 18 }}>{done.map((c) => <ChallengeRow key={c.id} c={c} onDelete={() => removeChallenge(c.id, `Challenge “${c.title}” deleted`, () => del(`/challenges/${c.id}`).then(ch.reload))} />)}</div>
        </Card>
      )}
      {toast.node}
    </>
  );
}

function ChallengeRow({ c, onDelete }: { c: Challenge; onDelete: () => void }) {
  const s = STATUS[c.status];
  const barStatus = c.status === "lost" ? "over" : c.type === "category_cap" && c.progress_pct > 85 ? "at_risk" : "on_track";
  return (
    <div>
      <div className="row between" style={{ marginBottom: 6 }}>
        <div className="row" style={{ gap: 8 }}><b>{c.title}</b><Badge kind={s.kind}>{s.label}</Badge></div>
        <span className="row" style={{ gap: 6 }}>
          <span className="small muted">{TYPE[c.type] ?? c.type}</span>
          <button className="btn sm danger" onClick={onDelete} aria-label={`Remove ${c.title}`}>×</button>
        </span>
      </div>
      <Progress value={c.progress_pct} status={barStatus} marker={c.type === "category_cap" ? (c.days_elapsed / c.days_total) * 100 : undefined} />
      <div className="row between wrap small muted" style={{ marginTop: 6 }}>
        <span>{c.detail}</span>
        <span>{dateLabel(c.start_date)} – {dateLabel(c.end_date)}{c.estimated_savings > 0 && <> · <b className="pos">≈ {money(c.estimated_savings)} saved</b></>}</span>
      </div>
    </div>
  );
}
