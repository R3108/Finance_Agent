"use client";

/**
 * Ctrl/⌘ + K: jump to any page or action by typing. With twenty-odd pages, typing "split" beats
 * scanning a sidebar — and it's how power users expect a finance tool to work.
 *
 * Opened by the shortcut or by `openPalette()` (the sidebar's search button and the phone top bar).
 */
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import Icon from "@/components/Icon";
import { api } from "@/lib/api";
import { allItems, QUICK_ACTIONS } from "@/lib/nav";
import { useMe } from "@/lib/session";

const EVENT = "ledgerly:palette";
export const openPalette = () => window.dispatchEvent(new Event(EVENT));

type Entry = { href: string; label: string; icon: string; keywords?: string; group: string };

export const shortcutLabel = () =>
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘K" : "Ctrl K";

export default function CommandPalette() {
  const router = useRouter();
  const { me } = useMe();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener(EVENT, onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener(EVENT, onOpen);
    };
  }, []);

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (open && !d.open) { setQ(""); setCursor(0); d.showModal(); }
    if (!open && d.open) d.close();
  }, [open]);

  const entries: Entry[] = useMemo(() => [
    ...QUICK_ACTIONS.map((a) => ({ ...a, group: "Actions" })),
    ...allItems(!!me?.is_admin),
    { href: "#logout", label: "Log out", icon: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9", group: "Account" },
  ], [me?.is_admin]);

  const results = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return entries;
    return entries.filter((e) => {
      const hay = `${e.label} ${e.keywords ?? ""} ${e.group}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    });
  }, [q, entries]);

  async function go(e: Entry) {
    setOpen(false);
    if (e.href === "#logout") {
      await api("/auth/logout", { method: "POST" }).catch(() => {});
      router.replace("/login");
      return;
    }
    router.push(e.href);
  }

  return (
    <dialog ref={dialog} className="palette" aria-label="Go to" onClose={() => setOpen(false)}
            onClick={(ev) => { if (ev.target === dialog.current) setOpen(false); }}>
      <div className="palette-inner">
        <input className="palette-input" autoFocus placeholder="Search pages and actions…" value={q}
               role="combobox" aria-expanded aria-controls="palette-list"
               aria-activedescendant={results[cursor] ? `pal-${cursor}` : undefined}
               onChange={(e) => { setQ(e.target.value); setCursor(0); }}
               onKeyDown={(e) => {
                 if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, results.length - 1)); }
                 if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
                 if (e.key === "Enter" && results[cursor]) { e.preventDefault(); go(results[cursor]); }
               }} />
        <ul id="palette-list" role="listbox" className="palette-list">
          {results.length === 0 && <li className="muted small" style={{ padding: 14 }}>Nothing matches “{q}”.</li>}
          {results.map((r, i) => (
            <li key={`${r.group}-${r.href}-${r.label}`} id={`pal-${i}`} role="option" aria-selected={i === cursor}
                className={`palette-item ${i === cursor ? "on" : ""}`}
                onMouseEnter={() => setCursor(i)} onClick={() => go(r)}>
              <Icon d={r.icon} />
              <span className="nav-label">{r.label}</span>
              <span className="small muted">{r.group}</span>
            </li>
          ))}
        </ul>
        <div className="palette-foot small muted">↑↓ to move · Enter to open · Esc to close</div>
      </div>
    </dialog>
  );
}
