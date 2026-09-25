"use client";

import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useCallback, useEffect, useState } from "react";
import CommandPalette from "@/components/CommandPalette";
import { FeedbackProvider } from "@/components/feedback";
import MobileNav from "@/components/MobileNav";
import Sidebar from "@/components/Sidebar";
import { api } from "@/lib/api";
import { setDisplayCurrency } from "@/lib/format";
import { type Me, MeContext, type Scope } from "@/lib/session";

// /join renders its own frame: the invite preview is readable before signing in, so the person
// can see what they've been invited to before deciding to create an account.
const PUBLIC = ["/welcome", "/login", "/forgot-password", "/reset-password", "/verify-email", "/join"];

function VerifyBanner({ email }: { email: string }) {
  const [state, setState] = useState<"idle" | "sending" | "sent" | string>("idle");
  async function resend() {
    setState("sending");
    try {
      await api("/auth/verify/resend", { method: "POST" });
      setState("sent");
    } catch (e) {
      setState((e as Error).message);
    }
  }
  return (
    <div className="demo-banner no-print" role="status">
      <span>
        {state === "sent" ? <>New link sent to <b>{email}</b>. It expires in 24 hours.</>
          : <>Confirm your email: we sent a link to <b>{email}</b>.{!["idle", "sending"].includes(state) && <> {state}</>}</>}
      </span>
      {state !== "sent" && <button className="btn sm" onClick={resend} disabled={state === "sending"}>{state === "sending" ? "Sending…" : "Resend link"}</button>}
    </div>
  );
}

/** Signed-in chrome (sidebar + session) for every page except the public auth pages. */
export default function AppShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC.includes(path);
  const [me, setMe] = useState<Me | null>(null);
  // Remembered per browser so the choice survives a reload; the server ignores it for anyone who
  // isn't in a household, so a stale 'household' here is harmless.
  const [scope, setScopeState] = useState<Scope>("personal");

  useEffect(() => {
    try {
      if (localStorage.getItem("ledgerly.scope") === "household") setScopeState("household");
    } catch {}
  }, []);

  const setScope = useCallback((s: Scope) => {
    setScopeState(s);
    try {
      localStorage.setItem("ledgerly.scope", s);
    } catch {}
  }, []);

  const refresh = useCallback(() => {
    // a 401 here is handled inside api(): it sends the browser to /login
    api<Me>("/me")
      .then((m) => {
        // set before setMe: pages render only once `me` exists, so every money() below is already
        // pointed at the right currency and nothing flashes in the wrong one
        setDisplayCurrency(m.currency, m.currency_locale);
        setMe(m);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!isPublic) refresh();
  }, [isPublic, path, refresh]);

  useEffect(() => {
    if (isPublic) setMe(null);
  }, [isPublic]);

  // Installable app: the service worker only serves an offline notice when the network is gone.
  // Registered in production builds only, so it never caches anything under `next dev`.
  useEffect(() => {
    if (process.env.NODE_ENV === "production" && "serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
  }, []);

  if (isPublic) return <FeedbackProvider>{children}</FeedbackProvider>;
  if (!me) return <div className="boot" aria-label="Loading"><div className="brand-mark">L</div></div>;

  return (
    <FeedbackProvider>
    <MeContext.Provider value={{ me, refresh, scope, setScope }}>
      <div className="shell">
        <Sidebar />
        <MobileNav />
        <CommandPalette />
        <main className="main">
          {me.is_demo && (
            <div className="demo-banner no-print">
              <span>You&apos;re exploring a private demo with sample data. It&apos;s deleted after 7 days.</span>
              <button className="btn sm primary" onClick={async () => {
                await api("/auth/logout", { method: "POST" }).catch(() => {});
                router.push("/login?mode=signup");
              }}>Create your account</button>
            </div>
          )}
          {!me.is_demo && me.email && !me.email_verified && <VerifyBanner email={me.email} />}
          {/* keyed on the route so each page plays its entrance (globals.css "page transition") */}
          <div key={path} className="page-enter">{children}</div>
        </main>
      </div>
    </MeContext.Provider>
    </FeedbackProvider>
  );
}
