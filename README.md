# Ledgerly — agentic personal-finance assistant

A personal-finance assistant that categorises spending, detects subscriptions, flags unusual
ones, suggests budgets and answers questions in plain English. **Python does every calculation.**
The LLM (gpt-4o-mini) picks which analytics to run and explains the results. It never produces
a number on its own.

```
Next.js UI  ──/api──►  FastAPI  ──►  LangGraph agent ──► tools ──► pandas analytics ──► SQLite
 (port 3000)          (port 8000)    (gpt-4o-mini)                (integer cents)
```

## Quick start (Windows)

```powershell
# 1. put your key in backend/.env   ->   OPENAI_API_KEY=sk-...
# 2. launch both servers
./start.ps1
```

Open http://localhost:3000. You'll land on the sign-in page. Create an account (optionally filled
with sample data), or click **Try the demo** to get a private sandbox with 24 months of synthetic
transactions. Without a key, the assistant runs in **offline mode**: a keyword router over the
same tools.

With Docker: `docker compose up --build` (reads secrets from `backend/.env`, keeps data in a volume).

Manual start:

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000      # API docs: /docs
cd frontend && npm install && npm run dev
cd backend && .venv/Scripts/python -m pytest -q                        # tests
```

## How "no LLM math" is enforced

1. **Integer cents everywhere.** SQLite stores `amount_cents INTEGER`. CSV amounts are parsed with
   `Decimal`. Pandas sums integers. Values become dollars only at the output boundary.
2. **Tool-only numbers.** The agent's 22 tools wrap `analytics.py` and `planning.py`. Each returns finished figures:
   totals, percentages, projections and savings.
3. **Exact calculator tool.** When the model needs a derived figure, it must call `calculator`. The
   calculator uses a whitelisted AST with `Decimal`. `eval` is never used.
4. **Grounding guard (LangGraph `verify` node).** Before an answer is returned, every money figure,
   percentage and decimal in it is matched against numbers from tool outputs. If a figure doesn't
   match, the graph sends the model back once with the offending figures. If the retry still fails,
   the answer is labelled *Unverified* in the UI. Answers that pass show a **Numbers verified**
   badge.

```
START → agent ⇄ tools
          ↓
        verify ──(ungrounded figures, 1 retry)──► agent
          ↓
         END
```

## Features

| Area | What it does |
|---|---|
| **Accounts & sign-in** | Email + password sign-up and sign-in (PBKDF2-SHA256, 600k iterations, per-user salt). Server-side sessions in an httpOnly, SameSite=Lax cookie; only the token's SHA-256 is stored. `Authorization: Bearer` is also accepted. Sign-in is rate-limited to 5 failures per email per 15 minutes. **Try the demo** creates a private sandbox that is purged after 7 days. Every API route requires a session. |
| **Email verification** | Signing up emails a confirmation link (single-use, 24 h, stored hashed). An in-app banner offers **Resend link** (3 per 15 min). Upgrading to a paid plan requires a verified email. |
| **Forgot / reset password** | `/forgot-password` always gives the same answer, so it can't reveal which emails have accounts. It is rate-limited to 3 per 15 minutes. Reset links are single-use, expire in 1 hour, and are only ever sent by email, never in API responses. A reset signs out every device and sends a "password changed" notice. Signed-in users can change or add a password in Settings. |
| **Sign in with Google** | OAuth 2.0 code flow with PKCE, a `state` cookie and a nonce. The ID token's issuer, audience, expiry, nonce and `email_verified` are checked. Accounts match on Google's `sub`, then on verified email. Linking to an existing account that never verified its email wipes that account's password and sessions, which blocks pre-registration hijacks. The button appears only when `GOOGLE_CLIENT_ID`/`SECRET` are set. |
| Categorisation | ~70 merchant rules normalise messy descriptors (`TST* CHIPOTLE 2231` → Chipotle / Dining). User rules take priority. **Learn-from-edit**: fixing one transaction creates a rule and recategorises history. Optional gpt-4o-mini fallback for unknown merchants on CSV import; it can only choose from the fixed category list. |
| Subscription detection | Groups charges by merchant and account, then classifies cadence (weekly, 2- or 4-weekly, monthly, quarterly, semiannual, annual) from interval medians and amount stability. Separates bills, subscriptions and other recurring charges. Predicts the next charge. |
| Unusual subscriptions | Price increases (with date and old price), duplicate billing across accounts, overlapping services (video, music, cloud storage), new sign-ups and silent trial conversions, high cost, annual renewals due within 30 days, subscriptions that stopped charging. Each flag comes with a potential saving. |
| Monthly spending | Income, spending, net and savings rate. Category breakdown with a partial-month-aware comparison against your usual pace. Top merchants, weekday pattern, 12-month category mix. |
| Budgets | Suggestions per category type: fixed = recent max, essentials = 75th percentile, discretionary = median −10%, irregular = sinking fund. Each suggestion shows its rationale. 50/30/20 check. Live status with month-end projection, per-day allowance and at-risk alerts. |
| Anomalies | Robust z-score (median/MAD) per category, duplicate charges, unknown merchants, first-time large purchases, bank fees. |
| **Safe-to-spend** | Money left this month after recurring charges still due and a 20% savings target. |
| **Cash-flow forecast** | 30/60/90-day projection from detected paychecks, recurring charges and average variable spend. Shows the lowest-balance date. |
| **Financial health score** | 0–100 with an explanation for each part: savings rate, emergency fund, budget adherence, subscription load, spending stability. |
| **Goals + what-if simulator** | Goal feasibility against your real monthly surplus. You can simulate cancelling subscriptions or trimming categories and see the monthly, annual and 5-year (4% APY) effect and how many months sooner each goal is reached. |
| **Insights feed** | One ranked list of everything needing attention. |
| **Monthly report** | A printable one-page digest (Print → PDF). |
| **Data** | Bank CSV import (signed Amount or Debit/Credit, dedup-safe re-uploads), CSV export, rule management, demo reset. |
| Assistant | Multi-thread chat with saved history. Shows which tools ran, the verification badge and whether the answer self-corrected. 22 tools, including `can_i_afford`. |
| **Bill calendar** | Month grid of every recurring bill, subscription and paycheck. Posted charges come from statements; upcoming ones are projected from cadence. Totals still due and the heaviest day. Navigates up to 3 months ahead. |
| **Net worth** | Cash accounts + manual assets (investments, retirement, property, vehicles) − card balances − debts. Asset mix and a 12-month month-end cash history rebuilt from transactions. |
| **Debt payoff planner** | Avalanche vs snowball vs minimum-only, simulated month by month in integer cents (interest rounded half-up, freed minimums roll over). Debt-free date, total interest, interest and months saved, payoff order, first win. Detects debts whose minimum never covers interest. Extra-payment slider. |
| **Savings challenges** | No-spend days, category caps and habit breaks (e.g. "14 days without Blue Bottle Coffee"). Suggested from your own last 90 days, scored automatically from transactions, with estimated savings vs your prior pace and a win streak. |
| **Money Wrapped** | Shareable year in review: spending persona, savings rate, most-visited merchant, biggest purchase, coffee and delivery counts, no-spend days and streak, priciest/leanest month. "Copy share text" excludes dollar amounts. |
| **Automations (proactive agent)** | Watchers that run in the background every 6 hours instead of waiting for you to open the app: budget at risk, subscription price rises, duplicate billing, new sign-ups, annual renewals, unusual charges, large charges, a category cap you set, cash-flow dipping below a floor, safe-to-spend running low, and bills due in a few days. Each finding carries a **dedupe key** naming the event it describes (`price_up:Netflix:2026-08-15`), and the notifications table is UNIQUE on it — so the sweep is idempotent and one real event produces exactly one alert, however often it runs. A broken rule or a bad row fails that user only. In-app inbox with an unread count in the sidebar; high-severity alerts also go out by email on Pro. New accounts get a starter set sized to their plan. |
| **Weekly digest** | A recurring email summarising spend vs the period before, health score, safe-to-spend, top categories and recent alerts. Every figure is computed by `analytics`; the model is allowed to write only the one opening sentence, and that sentence is run through the **same grounding guard as the assistant** — if it contains a figure the digest didn't produce, it is dropped rather than shown. |
| **Multi-currency (India-first)** | Currency is a per-user setting applied end to end: analytics narrative, insight copy, budget rationales, alert bodies, digest emails, the assistant's system prompt and the browser. Indian digit grouping is real (₹12,34,567.89), and rounding steps scale with the currency so a budget suggestion lands on ₹500, not ₹10. **Nothing is ever converted** — `amount_cents` keeps its recorded value; only the presentation changes. The registry deliberately holds 2-decimal currencies only, because the integer-minor-unit invariant depends on it. |
| **India merchant pack** | ~60 rules for Indian statements (Swiggy, Zomato, Blinkit, DMart, BESCOM, Airtel, IRCTC, FASTag, cult.fit, Flipkart…) plus a **UPI descriptor parser** that pulls the payee out of `UPI/DR/412345678901/SWIGGY/YESB/swiggy@ybl/Payment`, skipping reference numbers, IFSC codes and PSP short codes. The demo can be seeded as an Indian persona (₹1.4L/month salary, NACH mandates, UPI spending) or the original US one. |
| **Household (Family plan)** | Up to 5 people share one ledger. Email invites (single-use, 7 days, hashed, redeemable only by the address they were sent to), roles (owner / member / viewer), ownership transfer, and a **Mine ↔ Household toggle** that widens every money page. **Private accounts** stay out of the shared view — including from the owner. Shared goals anyone can fund, household budgets summed per category, and a per-person spending split. **Plan inheritance**: the owner buys Family and every member gets the paid feature set, while their own row stays `free` — billing never learns households exist. Leaving or dissolving costs nobody their own data. |
| **Business & tax (freelancers)** | Split the ledger into business and personal, group expenses into deduction buckets with mixed-use shares (a phone bill at 50%), attribute income to clients, and get an estimated tax bill. India: new / old / **44ADA presumptive** regimes costed side by side, plus the four advance-tax instalments with what's due now. **Rates are versioned by financial year** — when a year isn't in the table the most recent one is used and the response carries `rates_stale` with the year it actually used, so a stale estimate says so instead of quietly lying. Nothing is auto-classified: suggestions are proposals, and a merchant becomes a rule only after you've tagged it the same way twice. One-click CSV export for an accountant. |
| **Receipts** | Upload PDFs or photos, matched to charges by amount and date. Totals, dates and merchants are parsed when `pypdf`/`pytesseract` happen to be installed — **neither is a dependency**, and an unreadable file is still stored and still attachable. Auto-match only takes unambiguous candidates, so two identical charges on one day are never guessed at. Files are content-hashed (same bytes stored once), typed from an allow-list rather than the filename, and path-checked on the way out. |
| **Trials, coupons & referrals** | A 14-day Pro trial (once per account, no card), coupon codes with caps and expiry, and a referral programme that pays **both sides** 30 days — held until the referee verifies their email, which is what makes throwaway signups pointless. All three grant a zero-value row in `billing_passes`, so existing billing code starts, stacks and expires free time with no special cases. A better tier starts immediately rather than queueing; a lesser one queues so its days aren't wasted. |
| **Operator metrics** | `/admin` for allow-listed emails (`ADMIN_EMAILS`; unset = nobody): MRR and ARR split between subscriptions and prepaid, collections, plan mix with free time counted separately from revenue, a 6-step activation funnel, churn, cohort retention, referral/trial/coupon performance, engagement and a product-event feed. Projections are labelled as projections. |
| **Data export & deletion** | Download everything as JSON, or delete the account outright. The export walks the live schema rather than a hand-written list, so a table added later is included automatically; secrets and hashes are excluded. Deletion relies on `ON DELETE CASCADE` and then **verifies** nothing was left behind, reporting any table that needed manual cleanup. A household owner with members must hand over or dissolve first. |
| **Plans & paywall** | Free / Pro / Family tiers in `plans.py`. Routes use `require(feature)` → HTTP 402; the UI turns 402 into an upgrade card. Free tier gets 15 AI questions and 5 alert rules a month; Pro adds unlimited rules, email alerts, the digest and the business/tax tools; Family adds the shared household. Pricing page with monthly/yearly toggle. |
| **Payments (Razorpay)** | Pro at ₹299/month or ₹2,699/year via Razorpay Subscriptions (UPI Autopay, cards, netbanking, wallets). Checkout opens in-page. The payment signature is verified server-side and the plan unlocks **immediately**. Signed, de-duplicated webhooks handle renewals, retries, halts and cancellations, and each re-fetches the subscription from Razorpay. Cancelling takes effect at the end of the paid period. Stale subscriptions are re-checked whenever billing is viewed, so a missed webhook can't leave Pro on forever. Payment history is kept. Prices are set server-side in paise. The Upgrade buttons only work while the Razorpay keys are in `.env`. |
| **Quick add from bank SMS** | Paste the alerts your bank texts you (UPI, card, salary, ATM, refunds) and they become transactions, previewed first. Deterministic parsing only: amounts go through `Decimal`; figures after "Avl Bal", "limit" or "due" are never taken as the amount; OTPs, declined payments, reminders and "will be debited" mandate notices are skipped *with a reason*. UPI alerts are written as UPI descriptors so the existing UPI parser and India merchant pack categorise them. Re-pasting is a no-op (import hash + UPI reference). **Pro: automatic tracking** — a per-user forwarding key (hashed, add-only, rotatable) lets an Android SMS-forwarder app or an iOS Shortcut post every bank SMS to `/api/capture/inbound` as it arrives. Bank-grade coverage with no aggregator licence. |
| **Split & settle** | Split any transaction (or a bill someone else paid) equally or by custom shares — computed in integer paise with the remainder assigned deterministically, so parts always sum to the whole. Balances net per person. Settling produces a `upi://pay` link (you owe them) or a WhatsApp-ready reminder carrying *your* UPI id and a pay link (they owe you). "Your real share this month" subtracts what you fronted for friends. |
| **Can I afford it?** | Replays the 90-day cash-flow forecast with a purchase in it (one-off or monthly) and returns a verdict — comfortable / tight / not now — with reasons, lowest balance and safe-to-spend before/after, goal delays, emergency-fund months, and for a one-off that doesn't fit today, the **earliest date it would**. Also an assistant tool (`can_i_afford`), so chat answers match the page. |
| **Two-factor sign-in & devices** | TOTP (RFC 6238, stdlib only) with QR setup, a two-step enable that can't lock anyone out, ±1 step drift, **replay protection** (last accepted step stored, claimed atomically), 10 hashed single-use recovery codes, per-user lockout on wrong codes. Password reset and Google sign-in both still require the code. Settings lists signed-in devices (browser/OS, IP, last active) with per-device and "all others" sign-out, plus a 180-day security activity log. Email notices when 2FA changes or a recovery code is used. Secrets are excluded from data export. |
| **Operations** | `X-Request-ID` on every response (honoured from upstream, quoted in 500s), security headers + `Cache-Control: no-store`, optional JSON access logs (path only — never query strings), `/api/health/live` and `/api/health/ready` probes, per-IP limits on sign-in/sign-up/demo/forwarding that read `X-Forwarded-For` only as far as `TRUSTED_PROXY_HOPS` vouches. `docker-compose.yml` + Dockerfiles (non-root, healthchecked) and a GitHub Actions CI running pytest, `tsc` and `next build`. |
| **Landing & onboarding** | Signed-out visitors to `/` get a public landing page (`/welcome`, statically prerendered for SEO) with live pricing from `/api/plans/public` and a one-click demo. A new account with no transactions sees a welcome with three ways to add data instead of empty charts, then a getting-started checklist whose ticks come from real data (`/api/onboarding`). |
| **Navigation & app shell** | One nav map (`lib/nav.ts`) feeds the sidebar (12 links + a collapsible "More"), a "+ Add" quick-action menu, an account menu, a Ctrl/⌘K command palette, and on phones a bottom tab bar with a "More" sheet. Tables become stacked cards on narrow screens. Installable as an app (web manifest, generated icons, a service worker that only serves an offline notice — financial data is never cached). |
| **Feedback** | Styled, focus-trapped confirm dialogs (type-to-confirm for irreversible actions) replace `window.confirm`. One toast stack with Undo: deleting a goal, budget, rule, asset, debt, challenge or split hides it at once and only deletes after 6 seconds unless undone. |
| **Prepaid fallback** | If the Razorpay account can't create subscriptions (a cached read-only probe), checkout sells prepaid passes through one-time Razorpay Orders: 1 month for ₹299 or 1 year for ₹2,699. The signature, order, amount and capture are verified before Pro unlocks. Renewing early stacks after the current end date. Pro drops to Free exactly when the pass ends; the plan is re-derived on every request. An `order.paid` webhook activates passes whose tab closed before verification. A reminder email goes out 3 days before expiry (background job, every 30 minutes). Once Subscriptions becomes available, checkout switches to auto-renew by itself, and a subscription bought while a pass is active starts billing when the pass ends. |

## Synthetic data

`app/synthetic.py` generates seeded, realistic data with planted edge cases the tests check for:
a Netflix price hike, an expired NYT promo, Spotify billed on two accounts, four stacked streaming
services, a new ChatGPT subscription, a cancelled Headspace, an Amazon Prime renewal due soon, a
$1,299 one-off purchase, a duplicate grocery charge, an unknown merchant, and dining creeping up.
The demo also seeds four debts (including a 0% plan, so avalanche and snowball diverge), three
manual assets and a running no-spend challenge.

## Project layout

```
backend/app/
  config.py        env settings (OPENAI_API_KEY, OPENAI_MODEL=gpt-4o-mini)
  db.py            SQLite schema (multi-tenant: every table keyed by user_id)
  money.py         currency registry, Indian/western grouping, per-request currency context
  categorizer.py   merchant rules (US + India), UPI descriptor parsing, user rules, LLM fallback
  ingest.py        CSV parsing, dedup hashing, insertion
  synthetic.py     demo data generator (US persona) + dataset dispatch
  synthetic_in.py  demo data generator (India persona: UPI, NACH, ₹)
  automations.py   proactive watchers, dedupe, notification inbox, digest
  capture.py       bank SMS / alert parsing, preview, import, forwarding tokens
  splits.py        split & settle: exact shares, per-person balances, UPI links and reminders
  afford.py        "can I afford it?" — purchase replayed through the cash-flow forecast
  mfa.py           TOTP two-factor sign-in and recovery codes
  household.py     shared ledgers: membership, invites, roles, plan inheritance
  tax.py           business/personal split, deduction buckets, regimes, advance tax, export
  receipts.py      receipt storage, optional parsing, transaction matching
  growth.py        trials, coupons, referrals (all granted as zero-value billing passes)
  admin.py         operator metrics (MRR, funnel, churn, retention) — allow-listed emails only
  privacy.py       schema-walking data export and verified account deletion
  analytics.py     all deterministic calculations (pandas)
  planning.py      debt payoff, net worth, bill calendar, challenges, year in review (same integer-cents rules)
  plans.py         tiers, INR prices, feature entitlements, AI quota
  billing.py       Razorpay subscriptions: checkout, signature verification, webhooks, cancel, reconcile
  auth.py          password hashing, sessions, one-time email tokens, rate limiting
  mailer.py        SMTP email (Gmail-ready, or dev outbox), templates, `python -m app.mailer test <addr>`
  google_oauth.py  Sign in with Google (OAuth 2.0 + PKCE, OIDC claim checks, safe account linking)
  calc.py          safe Decimal calculator
  agent.py         LangGraph graph, tools, grounding guard, offline router
  services.py      per-user data loading
  main.py          FastAPI routes
backend/tests/     analytics + agent tests (scripted fake LLM, no network)
frontend/          Next.js 16 app router, TypeScript, Recharts
```

## Roadmap to production / commercial launch

- **Auth at scale**: a shared rate-limit store (Redis) once the API runs on more than one worker, and a
  queue (instead of FastAPI background tasks) for email if volume grows. 2FA (TOTP) is built; encrypting
  `users.totp_secret` at rest with a KMS-held key is the next step before a security audit.
- **Bank connections**: Plaid, TrueLayer or GoCardless feeding `ingest.insert_transactions`
  (dedup hashing is already in place).
- **Postgres** for multi-user scale. The SQL is portable and `db.py` is the only connection point.
- **Billing**: Razorpay is integrated (`billing.py`). Before charging real customers: activate your Razorpay
  account (KYC), switch to live keys, and get GST invoices and refund terms right for your business.
- **Family plan**: built — see **Household** above. It is a real, purchasable tier now.
- **Tax rates**: `tax.SLABS` and `tax.PARAMS` ship the FY 2025-26 India tables and **must be reviewed
  every budget**. A missing year falls back to the latest known table and flags `rates_stale`, so the
  product stays usable, but adding the new year is a yearly maintenance task, not an optional one.
- **Tier shape**: entitlement is a single linear rank (`plans.RANK`), so a plan includes everything
  below it. Selling a "Business" tier with the freelancer tools but *not* the household would need
  `FEATURES` to map to explicit per-plan feature sets instead. Worth doing only if that tier is sold.
- **Receipt storage**: files go to local disk (`RECEIPTS_DIR`). Point it at a mounted volume, or swap
  `receipts.store`/`path_for` for object storage, before running more than one API replica.
- **Coupon discounts**: codes grant free days, not a percentage off. A percentage discount on a
  Razorpay subscription changes the plan it renews at, so "first month 50% off" would discount every
  future month too; doing it properly needs Razorpay Offers.
- **Scheduled alerts**: done — see **Automations** above. The sweep currently runs inside the API
  process (`BACKGROUND_JOBS` in `main.py`); move it to a worker or cron once there is more than one
  API replica, so the watchers don't run N times. The jobs are idempotent, so duplicate runs are
  harmless in the meantime.
- **Compliance**: encryption at rest, audit log, data export/delete (GDPR). The assistant already
  states that it gives educational guidance, not regulated advice.
