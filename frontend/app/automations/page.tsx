"use client";

import { useState } from "react";
import { Badge, Card, Empty, ErrorBox, Loading, PageHead, useToast } from "@/components/ui";
import { del, post, put, useApi } from "@/lib/api";
import { dateLabel } from "@/lib/format";
import { useMe } from "@/lib/session";
import type { AlertRule, Automations, Inbox, RuleType } from "@/lib/types";

/** A rule's configured value for a parameter, falling back to the type's default. */
const paramValue = (rule: AlertRule | null, p: RuleType["params"][number]) =>
  rule?.params?.[p.name] ?? p.default;

function describe(rule: AlertRule, types: RuleType[]) {
  const type = types.find((t) => t.key === rule.kind);
  if (!type) return rule.kind;
  const parts = type.params.map((p) => `${p.label.toLowerCase()} ${paramValue(rule, p)}`);
  return parts.length ? parts.join(" · ") : type.description;
}

function RuleEditor({ types, rule, limits, onDone, onCancel }: {
  types: RuleType[]; rule: AlertRule | null; limits: Automations["limits"];
  onDone: () => void; onCancel: () => void;
}) {
  const [kind, setKind] = useState(rule?.kind ?? types[0]?.key ?? "");
  const [params, setParams] = useState<Record<string, string | number>>(rule?.params ?? {});
  const [email, setEmail] = useState(rule?.channels.includes("email") ?? false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const type = types.find((t) => t.key === kind);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body = { kind, params, channels: email ? ["inapp", "email"] : ["inapp"], active: rule?.active ?? true };
    try {
      if (rule) await put(`/automations/${rule.id}`, body);
      else await post("/automations", body);
      onDone();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={save} style={{ gap: 12 }}>
      <label className="stack" style={{ gap: 6 }}>
        <span className="small">Alert me about</span>
        <select className="select" value={kind} onChange={(e) => { setKind(e.target.value); setParams({}); }}>
          {types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select>
        {type && <span className="small muted">{type.description}</span>}
      </label>

      {type?.params.map((p) => (
        <label key={p.name} className="stack" style={{ gap: 6 }}>
          <span className="small">{p.label}</span>
          {p.type === "category" ? (
            <CategorySelect value={String(paramValue(rule, p))}
                            onChange={(v) => setParams({ ...params, [p.name]: v })} current={params[p.name]} />
          ) : (
            <input className="input" type="number" min={1} step={p.type === "days" ? 1 : "any"}
                   value={params[p.name] ?? p.default}
                   onChange={(e) => setParams({ ...params, [p.name]: e.target.value })} />
          )}
        </label>
      ))}

      <label className="row small">
        <input type="checkbox" checked={email} disabled={!limits.email}
               onChange={(e) => setEmail(e.target.checked)} />
        Email me when this fires
        {!limits.email && <span className="muted"> — Pro only</span>}
      </label>

      {error && <ErrorBox error={error} />}
      <div className="row">
        <button className="btn primary" disabled={busy}>{busy ? "Saving…" : rule ? "Save changes" : "Create alert"}</button>
        <button className="btn" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function CategorySelect({ value, current, onChange }: {
  value: string; current: string | number | undefined; onChange: (v: string) => void;
}) {
  const cats = useApi<string[]>("/categories");
  return (
    <select className="select" value={String(current ?? value)} onChange={(e) => onChange(e.target.value)}>
      {(cats.data ?? [value]).map((c) => <option key={c} value={c}>{c}</option>)}
    </select>
  );
}

export default function AutomationsPage() {
  const { data, error, loading, reload } = useApi<Automations>("/automations");
  const inbox = useApi<Inbox>("/notifications?limit=20");
  const { me } = useMe();
  const toast = useToast();
  const [editing, setEditing] = useState<AlertRule | null | "new">(null);
  const [running, setRunning] = useState(false);

  async function runNow() {
    setRunning(true);
    try {
      const r = await post<{ created: number }>("/automations/run");
      toast.show(r.created ? `${r.created} new alert${r.created === 1 ? "" : "s"}` : "Nothing new — you're all clear");
      inbox.reload();
    } catch (err) {
      toast.show((err as Error).message);
    } finally {
      setRunning(false);
    }
  }

  async function toggle(rule: AlertRule) {
    await put(`/automations/${rule.id}`, {
      kind: rule.kind, params: rule.params, channels: rule.channels, active: !rule.active,
    });
    reload();
  }

  async function setDigest(period: string) {
    try {
      await put("/automations/digest", { period });
      toast.show(period === "off" ? "Digest turned off" : `Digest set to ${period}`);
      reload();
    } catch (err) {
      toast.show((err as Error).message);
    }
  }

  if (loading) return <Loading h={320} />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const atLimit = data.limits.rules != null && data.rules.length >= data.limits.rules;

  return (
    <>
      <PageHead
        title="Automations"
        sub="Ledgerly checks your accounts in the background and tells you when something needs attention — you don't have to come looking."
      >
        <button className="btn" onClick={runNow} disabled={running}>{running ? "Checking…" : "Check now"}</button>
      </PageHead>

      <div className="grid cols-2">
        <Card
          title="Alerts"
          hint={data.limits.rules == null
            ? `${data.rules.length} rules · unlimited on your plan`
            : `${data.rules.length} of ${data.limits.rules} rules on the ${me?.plan ?? "free"} plan`}
          action={editing === null && !atLimit
            ? <button className="btn sm primary" onClick={() => setEditing("new")}>New alert</button>
            : undefined}
          className="span-2"
        >
          {editing !== null ? (
            <RuleEditor types={data.types} rule={editing === "new" ? null : editing} limits={data.limits}
                        onCancel={() => setEditing(null)}
                        onDone={() => { setEditing(null); toast.show("Alert saved"); reload(); }} />
          ) : (
            <>
              {atLimit && (
                <p className="small muted" style={{ marginBottom: 10 }}>
                  You&apos;ve used every alert on the free plan. Pro adds unlimited rules, email delivery and the weekly digest.
                </p>
              )}
              <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Alert</th><th>Settings</th><th>Delivery</th><th className="r" /></tr>
                </thead>
                <tbody>
                  {data.rules.map((r) => (
                    <tr key={r.id} className={r.active ? "" : "muted"}>
                      <td>
                        <b>{r.label}</b>
                        {!r.active && <> <Badge kind="info">Paused</Badge></>}
                      </td>
                      <td className="small muted">{describe(r, data.types)}</td>
                      <td className="small">{r.channels.includes("email") ? "In-app + email" : "In-app"}</td>
                      <td className="r">
                        <button className="btn sm" onClick={() => setEditing(r)}>Edit</button>{" "}
                        <button className="btn sm" onClick={() => toggle(r)}>{r.active ? "Pause" : "Resume"}</button>{" "}
                        <button className="btn sm danger" onClick={async () => {
                          await del(`/automations/${r.id}`);
                          toast.show("Alert removed");
                          reload();
                        }}>Remove</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              {!data.rules.length && <Empty>No alerts yet. Create one and Ledgerly will watch for it.</Empty>}
            </>
          )}
        </Card>

        <Card title="Weekly digest" hint="A short summary of where your money went, by email.">
          <div className="stack">
            <select className="select" value={data.digest_period} disabled={!data.limits.digest}
                    onChange={(e) => setDigest(e.target.value)} aria-label="Digest frequency">
              <option value="off">Don&apos;t send a digest</option>
              <option value="weekly">Every week</option>
              <option value="monthly">Every month</option>
            </select>
            {!data.limits.digest && (
              <p className="small muted">
                The digest is part of Pro. It sums up your spending, health score and anything the watchers
                found — with every figure computed in Python, never written by the model.
              </p>
            )}
            <p className="small muted">
              Last background check: {data.last_run_at ? dateLabel(data.last_run_at.slice(0, 10)) : "not yet"}.
            </p>
          </div>
        </Card>

        <Card title="Recent alerts" hint={inbox.data?.unread ? `${inbox.data.unread} unread` : "All caught up"}
              action={inbox.data?.unread
                ? <button className="btn sm" onClick={async () => { await post("/notifications/read"); inbox.reload(); }}>
                    Mark all read
                  </button>
                : undefined}>
          {inbox.loading ? <Loading h={160} /> : (
            <div className="stack">
              {inbox.data?.items.slice(0, 8).map((n) => (
                <div key={n.id} className="row between" style={{ alignItems: "flex-start", gap: 10 }}>
                  <div>
                    <div className="row" style={{ gap: 6 }}>
                      <Badge kind={n.severity} />
                      <b className={n.read ? "muted" : ""}>{n.title}</b>
                    </div>
                    <div className="small muted">{n.body}</div>
                  </div>
                </div>
              ))}
              {!inbox.data?.items.length && <Empty>Nothing yet. Hit “Check now” to run every watcher.</Empty>}
            </div>
          )}
        </Card>
      </div>
      {toast.node}
    </>
  );
}
