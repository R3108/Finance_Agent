"use client";

import { useEffect, useRef, useState } from "react";
import Markdown from "@/components/Markdown";
import { Badge, PageHead, UpgradeCard } from "@/components/ui";
import { api, PLAN_REQUIRED, post, useApi } from "@/lib/api";
import type { ChatReply } from "@/lib/types";

type Msg = { role: "user" | "assistant"; content: string; meta?: Omit<ChatReply, "answer" | "thread_id"> | null };

const PROMPTS = [
  "How much did I spend on dining last month vs my average?",
  "Which subscriptions look unusual and what would I save by fixing them?",
  "Suggest a monthly budget for me.",
  "Will I run low on cash in the next 60 days?",
  "What if I cancel Hulu and Max and cut dining by 20%?",
  "Any suspicious transactions recently?",
  "Am I on track for my Japan trip?",
  "What's my financial health score and how do I improve it?",
  "When will I be debt-free if I pay $200 extra a month?",
  "What's my net worth?",
  "Which bills are still due this month?",
];

export default function Assistant() {
  const threads = useApi<{ thread_id: string; title: string }[]>("/chat/threads");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  async function openThread(id: string) {
    setThreadId(id);
    setError(null);
    setMessages(await api<Msg[]>(`/chat/${id}`));
  }

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setInput("");
    setError(null);
    setMessages((m) => [...m, { role: "user", content: q }]);
    setBusy(true);
    try {
      const r = await post<ChatReply>("/chat", { message: q, thread_id: threadId });
      const { answer, thread_id, ...meta } = r;
      if (!threadId) threads.reload();
      setThreadId(thread_id);
      setMessages((m) => [...m, { role: "assistant", content: answer, meta }]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHead title="Assistant" sub="Answers are computed by deterministic tools; every figure is checked against tool output before you see it." />
      <div className="chat">
        <div className="card chat-threads">
          <button className="btn primary" style={{ width: "100%", justifyContent: "center", marginBottom: 10 }}
            onClick={() => { setThreadId(null); setMessages([]); setError(null); }}>
            + New chat
          </button>
          {threads.data?.map((t) => (
            <button key={t.thread_id} className={`thread ${t.thread_id === threadId ? "active" : ""}`} onClick={() => openThread(t.thread_id)} title={t.title}>
              {t.title || "Untitled"}
            </button>
          ))}
        </div>

        <div className="card chat-main">
          <div className="messages">
            {messages.length === 0 && (
              <div style={{ margin: "auto", maxWidth: 640, textAlign: "center" }}>
                <h2 style={{ fontSize: 18, marginBottom: 6 }}>Ask anything about your money</h2>
                <p className="muted" style={{ marginBottom: 18 }}>Try one of these:</p>
                <div className="row wrap" style={{ justifyContent: "center" }}>
                  {PROMPTS.map((p) => <button key={p} className="chip" onClick={() => send(p)}>{p}</button>)}
                </div>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`msg ${m.role}`}>
                {m.role === "assistant" ? <Markdown text={m.content} /> : m.content}
                {m.meta && (
                  <div className="msg-meta">
                    {m.meta.grounded
                      ? <Badge kind="good">Numbers verified</Badge>
                      : <Badge kind="medium">Unverified: {m.meta.ungrounded_figures.join(", ")}</Badge>}
                    {m.meta.grounding_retried && <Badge kind="info">Self-corrected</Badge>}
                    {m.meta.mode === "offline" && <Badge kind="info">Offline mode</Badge>}
                    {m.meta.tools_used.map((t) => <span key={t} className="badge accent">{t.replaceAll("_", " ")}</span>)}
                  </div>
                )}
              </div>
            ))}
            {busy && <div className="msg assistant typing" aria-label="Thinking"><span /><span /><span /></div>}
            {error && (error.startsWith(PLAN_REQUIRED)
              ? <UpgradeCard message={error.slice(PLAN_REQUIRED.length)} />
              : <div className="error">{error}</div>)}
            <div ref={endRef} />
          </div>
          <form className="composer" onSubmit={(e) => { e.preventDefault(); send(input); }}>
            <textarea rows={1} value={input} placeholder="e.g. How much did I spend at Amazon this year?" aria-label="Message"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }} />
            <button className="btn primary" disabled={busy || !input.trim()}>Send</button>
          </form>
        </div>
      </div>
    </>
  );
}
