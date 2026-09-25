"use client";

/**
 * Phone navigation (≤ 800px; the sidebar is hidden there). A slim top bar with search and the
 * account menu, and a bottom tab bar within thumb reach: Home, Transactions, + Add, Assistant, More.
 * "More" opens a sheet with every section, so nothing is lost by having only five tabs.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { openPalette } from "@/components/CommandPalette";
import Icon from "@/components/Icon";
import { AccountMenu, AddMenu, ScopeSwitch, useUnread } from "@/components/Sidebar";
import { isActive, NAV } from "@/lib/nav";
import { useMe } from "@/lib/session";

const TABS = [
  { href: "/", label: "Home", icon: "M3 12l9-8 9 8M5 10v10h14V10" },
  { href: "/transactions", label: "Activity", icon: "M4 6h16M4 12h16M4 18h10" },
  { href: "/assistant", label: "Ask", icon: "M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" },
];

function MoreSheet({ open, onClose, unread }: { open: boolean; onClose: () => void; unread: number }) {
  const ref = useRef<HTMLDialogElement>(null);
  const path = usePathname();
  const { me } = useMe();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  useEffect(() => { onClose(); }, [path]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <dialog ref={ref} className="sheet" aria-label="All sections" onClose={onClose}
            onClick={(e) => { if (e.target === ref.current) onClose(); }}>
      <div className="sheet-inner">
        <div className="row between" style={{ marginBottom: 8 }}>
          <h2>Everything</h2>
          <button className="btn sm" onClick={onClose}>Close</button>
        </div>
        <ScopeSwitch />
        {NAV.filter((g) => g.id !== "top").map((g) => (
          <section key={g.id} className="sheet-group">
            <div className="nav-section">{g.section}</div>
            <div className="sheet-grid">
              {g.items.filter((i) => !i.adminOnly || me?.is_admin).map((i) => (
                <Link key={i.href} href={i.href} className={`sheet-link ${isActive(path, i.href) ? "active" : ""}`}>
                  <Icon d={i.icon} size={20} />
                  <span>{i.label}</span>
                  {i.href === "/automations" && unread > 0 && <span className="nav-count">{unread > 99 ? "99+" : unread}</span>}
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </dialog>
  );
}

export default function MobileNav() {
  const path = usePathname();
  const unread = useUnread();
  const [more, setMore] = useState(false);

  return (
    <>
      <header className="m-top no-print">
        <Link href="/" className="brand"><div className="brand-mark">L</div><span>Ledgerly</span></Link>
        <div className="row" style={{ gap: 6 }}>
          <button className="icon-btn" onClick={openPalette} aria-label="Search pages and actions">
            <Icon d="M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3" />
          </button>
          <AccountMenu up={false} align="right" compact />
        </div>
      </header>

      <nav className="m-tabs no-print" aria-label="Main">
        {TABS.slice(0, 2).map((t) => (
          <Link key={t.href} href={t.href} className={`m-tab ${isActive(path, t.href) ? "active" : ""}`}
                aria-current={isActive(path, t.href) ? "page" : undefined}>
            <Icon d={t.icon} size={22} /><span>{t.label}</span>
          </Link>
        ))}
        <div className="m-tab m-add">
          <AddMenu className="m-add-btn" up align="center" label="" />
        </div>
        {TABS.slice(2).map((t) => (
          <Link key={t.href} href={t.href} className={`m-tab ${isActive(path, t.href) ? "active" : ""}`}
                aria-current={isActive(path, t.href) ? "page" : undefined}>
            <Icon d={t.icon} size={22} /><span>{t.label}</span>
          </Link>
        ))}
        <button className={`m-tab ${more ? "active" : ""}`} onClick={() => setMore(true)} aria-haspopup="dialog">
          <span className="m-tab-icon">
            <Icon d="M4 6h16M4 12h16M4 18h16" size={22} />
            {unread > 0 && <i className="m-dot" aria-label={`${unread} unread alerts`} />}
          </span>
          <span>More</span>
        </button>
      </nav>

      <MoreSheet open={more} onClose={() => setMore(false)} unread={unread} />
    </>
  );
}
