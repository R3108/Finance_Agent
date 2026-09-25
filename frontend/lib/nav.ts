/**
 * The app's map, shared by the sidebar, the phone tab bar and "More" sheet, and the command palette,
 * so a page added here shows up everywhere at once and the four can never disagree.
 *
 * Groups are ordered by how often people use them. `collapsed` groups sit behind a "More" toggle in
 * the sidebar (auto-opened when you're on one of their pages); `account` items live in the account
 * menu in the sidebar foot rather than in the main list.
 */

export type NavItem = {
  href: string; label: string; icon: string; keywords?: string;
  adminOnly?: boolean;
};

export type NavGroup = { id: string; section: string | null; collapsed?: boolean; account?: boolean; items: NavItem[] };

export const NAV: NavGroup[] = [
  {
    id: "top", section: null, items: [
      { href: "/", label: "Dashboard", icon: "M3 12l9-8 9 8M5 10v10h14V10", keywords: "home overview" },
      { href: "/assistant", label: "Assistant", icon: "M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z", keywords: "ai chat ask question" },
      { href: "/transactions", label: "Transactions", icon: "M4 6h16M4 12h16M4 18h10", keywords: "spending search history" },
    ],
  },
  {
    id: "plan", section: "Plan", items: [
      { href: "/budgets", label: "Budgets", icon: "M12 2v20M17 6H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6", keywords: "limits categories" },
      { href: "/afford", label: "Can I afford it?", icon: "M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9", keywords: "purchase buy check" },
      { href: "/goals", label: "Goals & What-if", icon: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", keywords: "savings simulate" },
      { href: "/net-worth", label: "Net worth & debt", icon: "M3 17l6-6 4 4 8-8M15 7h6v6", keywords: "assets loans payoff" },
    ],
  },
  {
    id: "track", section: "Track", items: [
      { href: "/subscriptions", label: "Subscriptions", icon: "M4 4v6h6M20 20v-6h-6M5 15a8 8 0 0 0 14 2M19 9A8 8 0 0 0 5 7", keywords: "recurring cancel netflix" },
      { href: "/calendar", label: "Bill calendar", icon: "M4 5h16v15H4zM4 10h16M9 3v4M15 3v4", keywords: "due upcoming" },
      { href: "/splits", label: "Split & settle", icon: "M16 3h5v5M21 3l-7 7M8 21H3v-5M3 21l7-7M14 14l7 7M3 3l7 7", keywords: "friends upi owe" },
      { href: "/insights", label: "Insights", icon: "M3 20h18M6 16v-5M11 16V8M16 16v-3M21 16V5", keywords: "trends anomalies" },
      { href: "/automations", label: "Alerts", icon: "M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0", keywords: "automations notifications inbox digest" },
    ],
  },
  {
    id: "more", section: "More", collapsed: true, items: [
      { href: "/report", label: "Monthly report", icon: "M7 3h8l4 4v14H7zM15 3v4h4M10 12h6M10 16h6", keywords: "pdf print" },
      { href: "/challenges", label: "Challenges", icon: "M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0zM7 6H4a3 3 0 0 0 3 4M17 6h3a3 3 0 0 1-3 4", keywords: "no spend" },
      { href: "/wrapped", label: "Money Wrapped", icon: "M12 3l2.5 5.5L20 9l-4 4 1 6-5-3-5 3 1-6-4-4 5.5-.5z", keywords: "year review" },
      { href: "/freelance", label: "Business & tax", icon: "M4 7h16v13H4zM9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M4 12h16", keywords: "freelancer gst deductions" },
      { href: "/receipts", label: "Receipts", icon: "M6 3v18l3-2 3 2 3-2 3 2V3zM9 8h6M9 12h6", keywords: "bills proof upload" },
      { href: "/household", label: "Household", icon: "M17 20v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 10a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 20v-2a4 4 0 0 0-3-3.9M16 2.1a4 4 0 0 1 0 7.8", keywords: "family share" },
    ],
  },
  {
    id: "account", section: "Account", account: true, items: [
      { href: "/settings", label: "Data & settings", icon: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM4 12h2M18 12h2M12 4v2M12 18v2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4", keywords: "security 2fa import export password upi" },
      { href: "/plans", label: "Plans & billing", icon: "M3 7h18v10H3zM3 11h18", keywords: "upgrade pro pricing subscription" },
      { href: "/rewards", label: "Free access", icon: "M20 12v10H4V12M2 7h20v5H2zM12 22V7", keywords: "referral coupon trial" },
      { href: "/admin", label: "Operations", icon: "M3 3v18h18M7 15l4-4 3 3 5-6", adminOnly: true, keywords: "admin metrics" },
    ],
  },
];

/** Things you *do* rather than places you go: the "+ Add" menu and the top of the command palette. */
export const QUICK_ACTIONS: NavItem[] = [
  { href: "/capture", label: "Add from bank SMS", icon: "M4 5h16v11H8l-4 4zM8 9h8M8 12h5", keywords: "paste upi import quick add" },
  { href: "/splits", label: "Split a bill", icon: "M16 3h5v5M21 3l-7 7M8 21H3v-5M3 21l7-7", keywords: "friends owe" },
  { href: "/afford", label: "Check a purchase", icon: "M9 11l3 3 8-8", keywords: "afford buy" },
  { href: "/settings#import", label: "Import a bank CSV", icon: "M12 3v12M7 10l5 5 5-5M4 21h16", keywords: "statement upload" },
  { href: "/goals", label: "Add a savings goal", icon: "M12 5v14M5 12h14", keywords: "target" },
];

export const allItems = (isAdmin: boolean) =>
  NAV.flatMap((g) => g.items.filter((i) => !i.adminOnly || isAdmin).map((i) => ({ ...i, group: g.section ?? "Go to" })));

export const isActive = (path: string, href: string) => (href === "/" ? path === "/" : path === href.split("#")[0]);
