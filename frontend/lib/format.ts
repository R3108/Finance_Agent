/**
 * Display formatting for the signed-in user's currency.
 *
 * `money()` is imported by nearly every page, so rather than thread a currency prop through the
 * whole tree the active currency lives here and `AppShell` sets it from `/api/me` before any page
 * renders. `Intl` does the grouping, so rupees come out as ₹12,34,567.89 and dollars as
 * $1,234,567.89 without this file knowing either rule.
 *
 * Amounts are never converted: changing currency changes the symbol and grouping only, exactly as
 * it does on the server (see backend/app/money.py).
 */

let code = "INR";
let locale = "en-IN";

const cache = new Map<string, Intl.NumberFormat>();

function fmt(digits: 0 | 2): Intl.NumberFormat {
  const key = `${locale}:${code}:${digits}`;
  let f = cache.get(key);
  if (!f) {
    f = new Intl.NumberFormat(locale, {
      style: "currency",
      currency: code,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    cache.set(key, f);
  }
  return f;
}

/** Point every `money()` call at a currency. Called once from AppShell when the session loads. */
export function setDisplayCurrency(nextCode?: string | null, nextLocale?: string | null) {
  code = nextCode || "INR";
  locale = nextLocale || "en-IN";
}

export const currencyCode = () => code;

export const money = (v: number | null | undefined) => (v == null ? "—" : fmt(2).format(v));

/** Short axis labels in the user's currency: ₹12K, ₹1.2L-style grouping is left to Intl's compact form. */
export const moneyCompact = (v: number) =>
  new Intl.NumberFormat(locale, { style: "currency", currency: code, notation: "compact", maximumFractionDigits: 1 }).format(v);
export const money0 = (v: number | null | undefined) => (v == null ? "—" : fmt(0).format(v));

const inrFmt = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2, minimumFractionDigits: 0 });
/** Rupees for plan prices and receipts (₹2,699) — billing is always INR, whatever the user's currency. */
export const inr = (v: number | null | undefined) => (v == null ? "—" : inrFmt.format(v));

export const pct = (v: number | null | undefined, digits = 1) => (v == null ? "—" : `${v.toFixed(digits)}%`);

export const monthLabel = (m: string) => {
  const [y, mo] = m.split("-").map(Number);
  return new Date(y, mo - 1, 1).toLocaleDateString(locale, { month: "short", year: "2-digit" });
};

export const dateLabel = (d: string) =>
  new Date(`${d}T00:00:00`).toLocaleDateString(locale, { month: "short", day: "numeric", year: "numeric" });

export const shortDate = (d: string) =>
  new Date(`${d}T00:00:00`).toLocaleDateString(locale, { month: "short", day: "numeric" });
