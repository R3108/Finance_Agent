"use client";

/**
 * App-wide feedback: toasts (with an optional action such as Undo) and a confirm dialog.
 *
 * Mounted once in AppShell, above the page, so a toast outlives the page that raised it — an
 * "Undo" offered on one screen still works after the user navigates to the next.
 *
 * - `useToast().show(msg, { tone, action })` — the long-standing page API, now backed by one stack.
 * - `useConfirm()({ title, body, danger, requireText })` — resolves true/false. A real modal
 *   `<dialog>` (focus trapped, Esc cancels), replacing the browser's unstyled `confirm()`.
 * - `useUndoableDelete()` — hide now, delete after a few seconds unless the user taps Undo.
 */
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";

type Tone = "info" | "success" | "error";
type ToastAction = { label: string; onClick: () => void };
type ToastOpts = { tone?: Tone; action?: ToastAction; duration?: number };
type Toast = { id: number; message: string; tone: Tone; action?: ToastAction; duration: number };

type ConfirmOpts = {
  title: string; body?: ReactNode; confirmLabel?: string; cancelLabel?: string; danger?: boolean;
  /** Make the user type this word before the confirm button enables, for irreversible actions. */
  requireText?: string;
};

type Ctx = {
  toast: (message: string, opts?: ToastOpts) => number;
  dismiss: (id: number) => void;
  confirm: (opts: ConfirmOpts) => Promise<boolean>;
};

const FeedbackContext = createContext<Ctx | null>(null);
let nextId = 1;

function ConfirmDialog({ opts, onClose }: { opts: ConfirmOpts; onClose: (ok: boolean) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [typed, setTyped] = useState("");
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);
  const ready = !opts.requireText || typed.trim().toUpperCase() === opts.requireText.toUpperCase();
  return (
    <dialog ref={ref} className="dialog" aria-labelledby="dlg-title"
            onCancel={(e) => { e.preventDefault(); onClose(false); }}
            onClick={(e) => { if (e.target === ref.current) onClose(false); }}>
      <form method="dialog" className="stack" onSubmit={(e) => { e.preventDefault(); if (ready) onClose(true); }}>
        <h2 id="dlg-title">{opts.title}</h2>
        {opts.body && <div className="muted">{opts.body}</div>}
        {opts.requireText && (
          <label className="field">
            <span>Type <b>{opts.requireText}</b> to confirm</span>
            <input className="input" autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} />
          </label>
        )}
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 6 }}>
          <button type="button" className="btn" onClick={() => onClose(false)} autoFocus={!opts.requireText}>
            {opts.cancelLabel ?? "Cancel"}
          </button>
          <button type="submit" className={`btn ${opts.danger ? "danger-solid" : "primary"}`} disabled={!ready}>
            {opts.confirmLabel ?? "Confirm"}
          </button>
        </div>
      </form>
    </dialog>
  );
}

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [pending, setPending] = useState<{ opts: ConfirmOpts; resolve: (ok: boolean) => void } | null>(null);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const toast = useCallback((message: string, opts: ToastOpts = {}) => {
    const id = nextId++;
    const duration = opts.duration ?? (opts.action ? 6000 : 3200);
    // newest last; keep the stack short so it never covers the page
    setToasts((t) => [...t.slice(-2), { id, message, tone: opts.tone ?? "info", action: opts.action, duration }]);
    timers.current.set(id, setTimeout(() => dismiss(id), duration));
    return id;
  }, [dismiss]);

  const confirm = useCallback((opts: ConfirmOpts) => new Promise<boolean>((resolve) => setPending({ opts, resolve })), []);

  return (
    <FeedbackContext.Provider value={{ toast, dismiss, confirm }}>
      {children}
      <div className="toasts no-print" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`}>
            <span>{t.message}</span>
            {t.action && (
              <button className="toast-action" onClick={() => { t.action!.onClick(); dismiss(t.id); }}>{t.action.label}</button>
            )}
            <button className="toast-close" aria-label="Dismiss" onClick={() => dismiss(t.id)}>×</button>
          </div>
        ))}
      </div>
      {pending && (
        <ConfirmDialog opts={pending.opts} onClose={(ok) => { pending.resolve(ok); setPending(null); }} />
      )}
    </FeedbackContext.Provider>
  );
}

function useFeedback(): Ctx {
  const ctx = useContext(FeedbackContext);
  if (ctx) return ctx;
  // outside the provider (shouldn't happen): degrade to the browser's own UI rather than crash
  return {
    toast: () => 0, dismiss: () => {},
    confirm: async (o) => typeof window !== "undefined" && window.confirm(o.title),
  };
}

export const useConfirm = () => useFeedback().confirm;

export function useNotify() {
  return useFeedback().toast;
}

/**
 * Undoable removal of a list item in one call:
 *   const remove = useRemovable<number>();
 *   rows.filter(remove.visible(r => r.id))  ...  onClick={() => remove(r.id, "Goal deleted", () => del(...))}
 */
export function useRemovable<K extends string | number>() {
  const [hidden, setHidden] = useState<Set<K>>(new Set());
  const undoable = useUndoableDelete();
  const remove = useCallback((key: K, message: string, commit: () => Promise<unknown>) => undoable({
    message, commit,
    hide: () => setHidden((s) => new Set(s).add(key)),
    restore: () => setHidden((s) => { const n = new Set(s); n.delete(key); return n; }),
  }), [undoable]);
  return Object.assign(remove, {
    visible: <T,>(keyOf: (item: T) => K) => (item: T) => !hidden.has(keyOf(item)),
  });
}

/**
 * Optimistic delete with Undo: `remove({ message, hide, restore, commit })` hides the item at once,
 * then calls `commit()` (the real DELETE) when the toast times out — or `restore()` if Undo is tapped.
 * Leaving the page doesn't cancel it: the provider lives above every page.
 */
export function useUndoableDelete() {
  const { toast } = useFeedback();
  return useCallback((o: { message: string; hide: () => void; restore: () => void; commit: () => Promise<unknown> }) => {
    o.hide();
    let undone = false;
    const run = () => {
      if (undone) return;
      o.commit().catch(() => {
        o.restore();
        toast("Couldn't delete that — it's been put back.", { tone: "error" });
      });
    };
    const timer = setTimeout(run, 6000);
    toast(o.message, {
      duration: 6000,
      action: { label: "Undo", onClick: () => { undone = true; clearTimeout(timer); o.restore(); } },
    });
  }, [toast]);
}
