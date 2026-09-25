"use client";

import { useState } from "react";
import { useConfirm } from "@/components/feedback";
import { Badge, Card, Empty, ErrorBox, Loading, PageHead, Stat, useToast } from "@/components/ui";
import { api, del, post, put, useApi } from "@/lib/api";
import { dateLabel, pct } from "@/lib/format";

type Receipt = {
  id: number; filename: string; content_type: string; size_bytes: number;
  transaction_id: number | null; parsed_total: number | null; parsed_total_text: string | null;
  parsed_date: string | null; parsed_merchant: string | null; created_at: string;
  transaction_merchant: string | null; transaction_date: string | null;
};

type Match = {
  transaction_id: number; date: string; merchant: string; amount: number; amount_text: string;
  category: string; confidence: "exact" | "likely" | "possible"; why: string;
};

type Listing = {
  items: Receipt[]; business_transactions: number; with_receipt: number; coverage_pct: number | null;
};

const CONFIDENCE: Record<Match["confidence"], string> = { exact: "good", likely: "medium", possible: "info" };

function Matches({ receiptId, onAttached }: { receiptId: number; onAttached: () => void }) {
  const { data, loading } = useApi<Match[]>(`/receipts/${receiptId}/matches`);
  if (loading) return <Loading h={60} />;
  if (!data?.length) {
    return <p className="small muted">No likely match found. Attach it from the transactions list instead.</p>;
  }
  return (
    <div className="stack small" style={{ marginTop: 8 }}>
      {data.map((m) => (
        <div key={m.transaction_id} className="row between">
          <span>
            <Badge kind={CONFIDENCE[m.confidence]}>{m.confidence}</Badge>{" "}
            {m.merchant} · {m.amount_text} <span className="muted">{dateLabel(m.date)} — {m.why}</span>
          </span>
          <button className="btn sm primary" onClick={async () => {
            await put(`/receipts/${receiptId}/attach`, { transaction_id: m.transaction_id });
            onAttached();
          }}>Attach</button>
        </div>
      ))}
    </div>
  );
}

export default function Receipts() {
  const { data, error, loading, reload } = useApi<Listing>("/receipts");
  const toast = useToast();
  const confirmDialog = useConfirm();
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    let ok = 0;
    for (const file of Array.from(files)) {
      const form = new FormData();
      form.append("file", file);
      try {
        await api("/receipts", { method: "POST", body: form });
        ok += 1;
      } catch (err) {
        toast.show(`${file.name}: ${(err as Error).message}`);
      }
    }
    setBusy(false);
    if (ok) toast.show(`${ok} receipt${ok === 1 ? "" : "s"} uploaded`);
    reload();
  }

  if (loading) return <Loading h={320} />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const unattached = data.items.filter((r) => !r.transaction_id);

  return (
    <>
      <PageHead title="Receipts" sub="Proof for the expenses you claim. Drop files in and Ledgerly finds the charge they belong to.">
        <button className="btn" disabled={busy || !unattached.length} onClick={async () => {
          const r = await post<{ attached: number }>("/receipts/auto-attach");
          toast.show(r.attached ? `${r.attached} matched automatically` : "Nothing matched unambiguously");
          reload();
        }}>Match automatically</button>
      </PageHead>

      <div className="grid cols-3" style={{ marginBottom: 16 }}>
        <Stat label="Receipts stored" value={data.items.length} foot={`${unattached.length} not yet attached`} />
        <Stat label="Business expenses covered"
              value={`${data.with_receipt} / ${data.business_transactions}`}
              foot={data.coverage_pct != null ? `${pct(data.coverage_pct, 0)}` : "Classify expenses first"} />
        <Card title="Add receipts">
          <input className="input" type="file" multiple disabled={busy}
                 accept=".pdf,.png,.jpg,.jpeg,.webp,.heic,.txt"
                 onChange={(e) => upload(e.target.files)} aria-label="Receipt files" />
          <p className="small muted" style={{ marginTop: 8 }}>
            PDFs and photos, up to 8 MB each. Totals and dates are read where the file allows it;
            anything unreadable is still stored.
          </p>
        </Card>
      </div>

      <Card title="Your receipts">
        {data.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>File</th><th>Read from it</th><th>Attached to</th><th className="r" /></tr>
              </thead>
              <tbody>
                {data.items.map((r) => (
                  <>
                    <tr key={r.id}>
                      <td>
                        <a href={`/api/receipts/${r.id}/file`} target="_blank" rel="noreferrer">{r.filename}</a>
                        <div className="small muted">{dateLabel(r.created_at.slice(0, 10))} · {Math.round(r.size_bytes / 1024)} KB</div>
                      </td>
                      <td className="small muted">
                        {r.parsed_total_text || r.parsed_date || r.parsed_merchant ? (
                          <>
                            {r.parsed_total_text && <b>{r.parsed_total_text}</b>}
                            {r.parsed_date && <> · {dateLabel(r.parsed_date)}</>}
                            {r.parsed_merchant && <> · {r.parsed_merchant}</>}
                          </>
                        ) : "Couldn't read this file"}
                      </td>
                      <td className="small">
                        {r.transaction_id ? (
                          <>
                            <Badge kind="good">attached</Badge>{" "}
                            {r.transaction_merchant}
                            {r.transaction_date && <span className="muted"> · {dateLabel(r.transaction_date)}</span>}
                          </>
                        ) : <span className="muted">Not attached</span>}
                      </td>
                      <td className="r">
                        {r.transaction_id ? (
                          <button className="btn sm" onClick={async () => {
                            await put(`/receipts/${r.id}/attach`, { transaction_id: null });
                            toast.show("Detached");
                            reload();
                          }}>Detach</button>
                        ) : (
                          <button className="btn sm" onClick={() => setExpanded(expanded === r.id ? null : r.id)}>
                            {expanded === r.id ? "Hide" : "Find match"}
                          </button>
                        )}{" "}
                        <button className="btn sm danger" onClick={async () => {
                          // the stored file is erased with it, so this one asks rather than offering Undo
                          if (!(await confirmDialog({ title: `Delete ${r.filename}?`, danger: true, confirmLabel: "Delete receipt",
                            body: "The file is removed from storage. If it was attached to a business expense, that expense loses its proof." }))) return;
                          await del(`/receipts/${r.id}`);
                          toast.show("Receipt deleted");
                          reload();
                        }}>Delete</button>
                      </td>
                    </tr>
                    {expanded === r.id && (
                      <tr key={`${r.id}-matches`}>
                        <td colSpan={4}>
                          <Matches receiptId={r.id} onAttached={() => {
                            setExpanded(null);
                            toast.show("Receipt attached");
                            reload();
                          }} />
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty>No receipts yet. Upload a few and they&apos;ll be matched to your charges.</Empty>}
      </Card>
      {toast.node}
    </>
  );
}
