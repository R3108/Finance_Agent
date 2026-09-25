"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { openPalette, shortcutLabel } from "@/components/CommandPalette";
import Icon from "@/components/Icon";
import Menu from "@/components/Menu";
import { api } from "@/lib/api";
import { isActive, NAV, QUICK_ACTIONS } from "@/lib/nav";
import { useMe } from "@/lib/session";
import type { Inbox } from "@/lib/types";

/** Unread alert count, re-read on navigation and when the tab regains focus (the background sweep
 *  can add alerts while the page sits open). Shared by the sidebar and the phone tab bar. */
export function useUnread() {
  const path = usePathname();
  const [unread, setUnread] = useState(0);
  useEffect(() => {
    const check = () => api<Inbox>("/notifications?limit=1").then((i) => setUnread(i.unread)).catch(() => {});
    check();
    window.addEventListener("focus", check);
    return () => window.removeEventListener("focus", check);
  }, [path]);
  return unread;
}

export function useLogout() {
  const router = useRouter();
  return async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => {});
    router.replace("/login");
  };
}

/** The "+ Add" button: the things people do most, one tap away on every page. */
export function AddMenu({ className = "btn primary sidebar-add", up = false, align = "left" as "left" | "right" | "center", label = "Add" }) {
  return (
    <Menu label="Add or check something" className={className} up={up} align={align}
          trigger={<><Icon d="M12 5v14M5 12h14" /> {label && <span>{label}</span>}</>}>
      {(close) => QUICK_ACTIONS.map((a) => (
        <Link key={a.href + a.label} href={a.href} className="menu-item" role="menuitem" onClick={close}>
          <Icon d={a.icon} /> {a.label}
        </Link>
      ))}
    </Menu>
  );
}

export function ScopeSwitch() {
  const { me, scope, setScope } = useMe();
  if (!me?.in_household) return null;
  return (
    <div className="scope-switch" role="group" aria-label="Whose money to show">
      <button type="button" aria-pressed={scope === "personal"} className={scope === "personal" ? "on" : ""} onClick={() => setScope("personal")}>Mine</button>
      <button type="button" aria-pressed={scope === "household"} className={scope === "household" ? "on" : ""} onClick={() => setScope("household")}>Household</button>
    </div>
  );
}

export function AccountMenu({ up = true, align = "left" as "left" | "right", compact = false }) {
  const { me } = useMe();
  const logout = useLogout();
  const account = NAV.find((g) => g.account)!;
  if (!me) return null;
  const initial = (me.name || "?").trim().charAt(0).toUpperCase();
  return (
    <Menu label="Account" up={up} align={align} className={compact ? "avatar-btn" : "account-btn"}
          trigger={compact ? <span className="avatar">{initial}</span> : (
            <>
              <span className="avatar">{initial}</span>
              <span className="account-who">
                <b className="ellipsis">{me.name}</b>
                <span className="ellipsis small muted">{me.is_demo ? "Demo sandbox" : me.email}</span>
              </span>
            </>
          )}>
      {(close) => (
        <>
          <div className="menu-head small">
            <b>{me.name}</b>
            <span className="muted"> · {me.plan.toUpperCase()} · AI {me.llm_enabled ? "online" : "offline"}</span>
          </div>
          {account.items.filter((i) => !i.adminOnly || me.is_admin).map((i) => (
            <Link key={i.href} href={i.href} className="menu-item" role="menuitem" onClick={close}>
              <Icon d={i.icon} /> {i.label}
            </Link>
          ))}
          <button className="menu-item" role="menuitem" onClick={() => { close(); logout(); }}>
            <Icon d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" /> Log out
          </button>
        </>
      )}
    </Menu>
  );
}

export default function Sidebar() {
  const path = usePathname();
  const { me } = useMe();
  const unread = useUnread();
  const navRef = useRef<HTMLDivElement>(null);
  const moreGroup = NAV.find((g) => g.collapsed);
  const inMore = !!moreGroup?.items.some((i) => isActive(path, i.href));
  const [moreOpen, setMoreOpen] = useState(false);

  useEffect(() => {
    try { setMoreOpen(localStorage.getItem("ledgerly.nav.more") === "1"); } catch {}
  }, []);
  const toggleMore = () => setMoreOpen((o) => {
    try { localStorage.setItem("ledgerly.nav.more", o ? "0" : "1"); } catch {}
    return !o;
  });

  // a deep link can land with its own entry out of sight; `nearest` keeps the list still otherwise
  useEffect(() => {
    navRef.current?.querySelector<HTMLElement>(".nav-link.active")?.scrollIntoView({ block: "nearest" });
  }, [path]);

  return (
    <nav className="sidebar" aria-label="Main">
      <div className="sidebar-head">
        <Link href="/" className="brand">
          <div className="brand-mark">L</div>
          <span className="brand-name">Ledgerly</span>
        </Link>
        <div className="sidebar-tools">
          <AddMenu />
          <button type="button" className="btn search-btn" onClick={openPalette} aria-label="Search pages and actions">
            <Icon d="M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3" />
            <span className="muted small">{shortcutLabel()}</span>
          </button>
        </div>
      </div>

      <ScopeSwitch />

      <div className="nav-scroll" ref={navRef}>
        {NAV.filter((g) => !g.account).map((group) => {
          const open = !group.collapsed || moreOpen || inMore;
          return (
            <div className="nav-group" key={group.id}>
              {group.collapsed ? (
                <button type="button" className="nav-section nav-toggle" aria-expanded={open} onClick={toggleMore} disabled={inMore}>
                  {group.section} <span aria-hidden>{open ? "▾" : "▸"}</span>
                </button>
              ) : group.section && <div className="nav-section">{group.section}</div>}
              {open && (
                <ul>
                  {group.items.map((n) => (
                    <li key={n.href}>
                      <Link href={n.href} className={`nav-link ${isActive(path, n.href) ? "active" : ""}`}
                            aria-current={isActive(path, n.href) ? "page" : undefined}>
                        <Icon d={n.icon} />
                        <span className="nav-label">{n.label}</span>
                        {n.href === "/automations" && unread > 0 && (
                          <span className="nav-count" aria-label={`${unread} unread alerts`}>{unread > 99 ? "99+" : unread}</span>
                        )}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>

      <div className="sidebar-foot">
        {me ? (
          <div className="row between" style={{ gap: 6 }}>
            <AccountMenu />
            <Link href="/plans" className={`badge ${me.plan === "free" ? "" : "accent"}`} title="Plans & billing">
              {me.plan === "free" ? "Upgrade" : me.plan.toUpperCase()}
            </Link>
          </div>
        ) : "Connecting…"}
      </div>
    </nav>
  );
}
