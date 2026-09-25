"use client";

import { useState } from "react";
import { Badge, Card, Empty, ErrorBox, Loading, PageHead, UpgradeCard, useToast } from "@/components/ui";
import { api, del, patch, post, put, useApi } from "@/lib/api";
import { useMe } from "@/lib/session";
import type { HouseholdView, OwnAccount } from "@/lib/types";

const ROLE_HELP: Record<string, string> = {
  owner: "Manages people, billing and the shared ledger.",
  member: "Shares the ledger and can change shared budgets and goals.",
  viewer: "Sees the shared ledger but can't change anything.",
};

function CreateHousehold({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("Our household");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await post("/household", { name });
      onCreated();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Start a household" hint="Up to 5 people share one ledger. Everyone keeps their own login.">
      <form className="stack" onSubmit={create} style={{ gap: 12 }}>
        <label className="stack" style={{ gap: 6 }}>
          <span className="small">Household name</span>
          <input className="input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} required />
        </label>
        <p className="small muted">
          You&apos;ll be the owner. Invite people by email, and choose per account what stays private —
          a private account never appears in the shared view, not even to you.
        </p>
        {error && <ErrorBox error={error} />}
        <div><button className="btn primary" disabled={busy}>{busy ? "Creating…" : "Create household"}</button></div>
      </form>
    </Card>
  );
}

function AccountSharing() {
  const accounts = useApi<OwnAccount[]>("/accounts");
  const toast = useToast();

  async function toggle(a: OwnAccount) {
    await put(`/accounts/${a.id}/sharing`, { shared: !a.shared });
    toast.show(a.shared ? `${a.name} is now private` : `${a.name} is shared with your household`);
    accounts.reload();
  }

  return (
    <Card title="What you share" hint="Private accounts stay in your own view only. You decide, for each of your accounts.">
      {accounts.loading ? <Loading h={140} /> : (
        <div className="stack">
          {accounts.data?.map((a) => (
            <label key={a.id} className="row between">
              <span>{a.name} <span className="small muted">· {a.institution ?? a.type}</span></span>
              <span className="row small" style={{ gap: 6 }}>
                <input type="checkbox" checked={a.shared} onChange={() => toggle(a)} />
                {a.shared ? "Shared" : "Private"}
              </span>
            </label>
          ))}
          {!accounts.data?.length && <Empty>No accounts yet — import a CSV first.</Empty>}
        </div>
      )}
      {toast.node}
    </Card>
  );
}

export default function HouseholdPage() {
  const { data, error, loading, reload } = useApi<HouseholdView>("/household");
  const { me, refresh } = useMe();
  const toast = useToast();
  const [invite, setInvite] = useState({ email: "", role: "member" });
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function sendInvite(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setInviteError(null);
    try {
      await post("/household/invites", invite);
      toast.show(`Invite sent to ${invite.email}`);
      setInvite({ email: "", role: "member" });
      reload();
    } catch (err) {
      setInviteError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function act(fn: () => Promise<unknown>, message: string) {
    try {
      await fn();
      toast.show(message);
      reload();
      refresh();
    } catch (err) {
      toast.show((err as Error).message);
    }
  }

  if (loading) return <Loading h={320} />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  if (!data.household) {
    return (
      <>
        <PageHead title="Household" sub="Share one ledger with the people you actually share money with." />
        {data.can_create
          ? <CreateHousehold onCreated={() => { toast.show("Household created"); reload(); }} />
          : <UpgradeCard message={`A shared household ledger for up to ${data.max_members} people is part of Ledgerly Family. Everyone you invite gets the full paid feature set on your one bill.`} />}
        {toast.node}
      </>
    );
  }

  const h = data.household;
  const full = data.members.length >= data.max_members;

  return (
    <>
      <PageHead
        title={h.name}
        sub={`${data.members.length} of ${data.max_members} people · you're ${h.role === "owner" ? "the owner" : `a ${h.role}`}. Switch any page to the shared view with the Household toggle.`}
      />

      <div className="grid cols-2">
        <Card title="People" className="span-2">
          <div className="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Role</th><th /></tr></thead>
              <tbody>
                {data.members.map((m) => (
                  <tr key={m.user_id}>
                    <td>
                      <b>{m.name}</b>{m.user_id === me?.id && <span className="small muted"> · you</span>}
                      <div className="small muted">{m.email}</div>
                    </td>
                    <td>
                      <Badge kind={m.role === "owner" ? "accent" : "info"}>{m.role}</Badge>
                      <div className="small muted">{ROLE_HELP[m.role]}</div>
                    </td>
                    <td className="r">
                      {h.is_owner && m.user_id !== h.owner_id && (
                        <>
                          <select className="select" value={m.role} aria-label={`Role for ${m.name}`}
                                  onChange={(e) => act(() => put(`/household/members/${m.user_id}`, { role: e.target.value }),
                                                       `${m.name} is now a ${e.target.value}`)}>
                            <option value="member">member</option>
                            <option value="viewer">viewer</option>
                          </select>{" "}
                          <button className="btn sm" onClick={() => act(
                            () => post(`/household/transfer/${m.user_id}`), `${m.name} now owns this household`)}>
                            Make owner
                          </button>{" "}
                          <button className="btn sm danger" onClick={() => act(
                            () => del(`/household/members/${m.user_id}`), `${m.name} removed`)}>Remove</button>
                        </>
                      )}
                      {m.user_id === me?.id && !h.is_owner && (
                        <button className="btn sm danger" onClick={() => act(
                          () => del(`/household/members/${m.user_id}`), "You left the household")}>Leave</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        {h.can_write && (
          <Card title="Invite someone" hint={full ? "This household is full." : "They'll get an email with a link that works once."}>
            <form className="stack" onSubmit={sendInvite} style={{ gap: 12 }}>
              <input className="input" type="email" placeholder="their@email.com" required disabled={full}
                     value={invite.email} onChange={(e) => setInvite({ ...invite, email: e.target.value })} />
              <select className="select" value={invite.role} disabled={full}
                      onChange={(e) => setInvite({ ...invite, role: e.target.value })} aria-label="Role">
                <option value="member">Member — can change shared budgets and goals</option>
                <option value="viewer">Viewer — read-only</option>
              </select>
              {inviteError && <ErrorBox error={inviteError} />}
              <div><button className="btn primary" disabled={busy || full}>{busy ? "Sending…" : "Send invite"}</button></div>
            </form>

            {data.invites.length > 0 && (
              <div className="stack" style={{ marginTop: 16 }}>
                <div className="small muted">Waiting to be accepted</div>
                {data.invites.map((i) => (
                  <div key={i.email} className="row between small">
                    <span>{i.email} <span className="muted">· {i.role}</span></span>
                    <button className="btn sm" onClick={() => act(
                      () => api(`/household/invites?email=${encodeURIComponent(i.email)}`, { method: "DELETE" }),
                      "Invite revoked")}>Revoke</button>
                  </div>
                ))}
              </div>
            )}
          </Card>
        )}

        <AccountSharing />

        {h.can_write && (
          <Card title="Household settings" className="span-2">
            <div className="stack">
              <label className="stack" style={{ gap: 6 }}>
                <span className="small">Name</span>
                <input className="input" defaultValue={h.name} maxLength={80}
                       onBlur={(e) => e.target.value !== h.name
                         && act(() => patch("/household", { name: e.target.value }), "Household renamed")} />
              </label>
              <p className="small muted">
                {h.is_owner
                  ? "Deleting the household removes the shared view. Everyone keeps every transaction, account and goal they own — shared goals go back to whoever created them."
                  : "Leaving removes you from the shared view. You keep all of your own data."}
              </p>
              <div>
                <button className="btn danger" onClick={() => act(
                  () => del("/household"), h.is_owner ? "Household deleted" : "You left the household")}>
                  {h.is_owner ? "Delete household" : "Leave household"}
                </button>
              </div>
            </div>
          </Card>
        )}
      </div>
      {toast.node}
    </>
  );
}
