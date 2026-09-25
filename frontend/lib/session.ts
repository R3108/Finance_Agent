"use client";

import { createContext, useContext } from "react";

export type Me = {
  id: number; name: string; email: string | null; plan: string; is_demo: boolean; llm_enabled: boolean; transactions: number;
  email_verified: boolean; has_password: boolean; google_linked: boolean; mfa_enabled: boolean;
  /** The user's own UPI id, used in split reminders. */
  upi_vpa: string | null;
  currency: string; currency_symbol: string; currency_locale: string;
  /** Shows the Operations link. Every admin route re-checks this server-side regardless. */
  is_admin: boolean;
  in_household: boolean;
};

export type CurrencyOption = { code: string; symbol: string; name: string; locale: string; region: string };

export type Scope = "personal" | "household";

export const MeContext = createContext<{
  me: Me | null;
  refresh: () => void;
  /** 'household' widens every data request to the shared ledger. Ignored without a household. */
  scope: Scope;
  setScope: (s: Scope) => void;
}>({ me: null, refresh: () => {}, scope: "personal", setScope: () => {} });

/** The signed-in user; `refresh()` refetches after plan or profile changes. */
export const useMe = () => useContext(MeContext);

/**
 * Appends the active scope to an API path, so a page written as `useApi(scoped("/overview"))`
 * follows the household toggle without knowing it exists.
 */
export function withScope(path: string, scope: Scope): string {
  if (scope !== "household") return path;
  return `${path}${path.includes("?") ? "&" : "?"}scope=household`;
}

/** `scoped("/overview")` -> the path for the currently selected scope. */
export function useScoped() {
  const { scope } = useContext(MeContext);
  return (path: string) => withScope(path, scope);
}
