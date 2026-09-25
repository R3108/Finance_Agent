"""SQLite persistence layer.

Money is stored as integer cents everywhere so that every aggregate is exact.
Convention: amount_cents < 0 is money leaving an account (expense),
amount_cents > 0 is money coming in (income / refund).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    email           TEXT UNIQUE,
    currency        TEXT NOT NULL DEFAULT 'INR',   -- display only; amount_cents is always minor units
    plan            TEXT NOT NULL DEFAULT 'free',
    password_hash   TEXT,                   -- NULL = cannot sign in with a password (e.g. demo sandboxes)
    is_demo         INTEGER NOT NULL DEFAULT 0,
    email_verified_at TEXT,                 -- NULL until the user clicks the emailed link (or signs in with Google)
    google_sub      TEXT,                   -- Google account id when linked
    referral_code   TEXT,                   -- this user's own code to share; minted on first use
    trial_started_at TEXT,                  -- set once; a trial is never given twice
    totp_secret     TEXT,                   -- base32 authenticator secret (see mfa.py)
    totp_enabled_at TEXT,                   -- NULL = two-factor sign-in is off, even if a secret exists
    totp_last_step  INTEGER,                -- last accepted 30-second step: a code can't be replayed
    upi_vpa         TEXT,                   -- the user's own UPI id, used in split reminders
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash  TEXT PRIMARY KEY,           -- sha256 of the emailed token
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose     TEXT NOT NULL,              -- verify_email | reset_password
    expires_at  TEXT NOT NULL,
    used_at     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_auth_tokens_user ON auth_tokens(user_id, purpose);

-- ---------------------------------------------------------------- billing (Razorpay)
CREATE TABLE IF NOT EXISTS billing_plans (          -- Razorpay plan ids, created on first use
    key      TEXT PRIMARY KEY,                       -- tier|period|amount|currency (a price change makes a new plan)
    plan_id  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_subscriptions (
    id                   TEXT PRIMARY KEY,          -- Razorpay subscription id (sub_...)
    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tier                 TEXT NOT NULL,              -- pro | family
    period               TEXT NOT NULL,              -- monthly | annual
    amount               INTEGER NOT NULL,           -- paise per period
    currency             TEXT NOT NULL,
    status               TEXT NOT NULL,              -- created | authenticated | active | pending | halted | cancelled | completed | expired | paused
    current_start        INTEGER,                    -- unix seconds
    current_end          INTEGER,
    cancel_at_cycle_end  INTEGER NOT NULL DEFAULT 0,
    short_url            TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_billing_subs_user ON billing_subscriptions(user_id);

CREATE TABLE IF NOT EXISTS billing_payments (
    id               TEXT PRIMARY KEY,              -- Razorpay payment id (pay_...)
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subscription_id  TEXT,
    amount           INTEGER NOT NULL,               -- paise
    currency         TEXT NOT NULL,
    status           TEXT NOT NULL,                  -- captured | failed | refunded | authorized
    method           TEXT,                           -- upi | card | netbanking | wallet ...
    invoice_id       TEXT,
    created_at       INTEGER NOT NULL                -- unix seconds (Razorpay time)
);
CREATE INDEX IF NOT EXISTS ix_billing_payments_user ON billing_payments(user_id, created_at);

CREATE TABLE IF NOT EXISTS billing_passes (          -- prepaid Pro time bought with a one-time Razorpay order
    id                TEXT PRIMARY KEY,               -- Razorpay order id (order_...)
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tier              TEXT NOT NULL,
    period            TEXT NOT NULL,                  -- monthly | annual
    amount            INTEGER NOT NULL,               -- paise
    currency          TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'created',-- created | paid
    -- purchase | trial | coupon:<CODE> | referral | grant. Free time is a paid pass worth zero,
    -- so plan entitlement needs no special case for it (see growth.grant_days).
    reason            TEXT NOT NULL DEFAULT 'purchase',
    payment_id        TEXT,
    starts_at         INTEGER,                        -- unix seconds; a renewal starts where the previous pass ends
    ends_at           INTEGER,
    reminder_sent_at  TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_billing_passes_user ON billing_passes(user_id, status, ends_at);

CREATE TABLE IF NOT EXISTS billing_events (          -- webhook idempotency: each event is applied once
    event_id     TEXT PRIMARY KEY,
    event        TEXT NOT NULL,
    received_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,           -- sha256 of the cookie value; the raw token is never stored
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  TEXT NOT NULL,
    user_agent  TEXT,                       -- shown in Settings → signed-in devices
    ip          TEXT,
    last_seen_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS accounts (
    id                     INTEGER PRIMARY KEY,
    user_id                INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                   TEXT NOT NULL,
    type                   TEXT NOT NULL,          -- checking | savings | credit
    institution            TEXT,
    opening_balance_cents  INTEGER NOT NULL DEFAULT 0,
    -- Whether this account is visible to the rest of the household. Private accounts stay in the
    -- owner's personal view only; shared is the default so joining a household is useful at once.
    shared                 INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS transactions (
    id               INTEGER PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id       INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    date             TEXT NOT NULL,             -- ISO yyyy-mm-dd
    description      TEXT NOT NULL,             -- raw bank descriptor
    merchant         TEXT NOT NULL,             -- normalised merchant name
    amount_cents     INTEGER NOT NULL,
    category         TEXT NOT NULL DEFAULT 'Uncategorized',
    category_source  TEXT NOT NULL DEFAULT 'rule',   -- rule | user | llm | default
    is_transfer      INTEGER NOT NULL DEFAULT 0,
    notes            TEXT,
    import_hash      TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, import_hash)
);
CREATE INDEX IF NOT EXISTS ix_tx_user_date ON transactions(user_id, date);
CREATE INDEX IF NOT EXISTS ix_tx_user_merchant ON transactions(user_id, merchant);

CREATE TABLE IF NOT EXISTS category_rules (
    id        INTEGER PRIMARY KEY,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pattern   TEXT NOT NULL,        -- case-insensitive substring of description or merchant
    category  TEXT NOT NULL,
    priority  INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, pattern)
);

CREATE TABLE IF NOT EXISTS budgets (
    id                  INTEGER PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category            TEXT NOT NULL,
    monthly_limit_cents INTEGER NOT NULL,
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, category)
);

CREATE TABLE IF NOT EXISTS goals (
    id                   INTEGER PRIMARY KEY,
    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                 TEXT NOT NULL,
    target_cents         INTEGER NOT NULL,
    saved_cents          INTEGER NOT NULL DEFAULT 0,
    target_date          TEXT NOT NULL,
    -- Set when the goal belongs to the household rather than one person; everyone in it can see
    -- and contribute to it. NULL means a private goal.
    household_id         INTEGER REFERENCES households(id) ON DELETE SET NULL,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subscription_actions (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sub_key    TEXT NOT NULL,           -- merchant|account_id
    status     TEXT NOT NULL,           -- keep | cancel_planned | cancelled | ignored
    note       TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, sub_key)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id   TEXT NOT NULL,
    role        TEXT NOT NULL,          -- user | assistant
    content     TEXT NOT NULL,
    meta_json   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_chat_thread ON chat_messages(user_id, thread_id, id);

CREATE TABLE IF NOT EXISTS debts (
    id                  INTEGER PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    kind                TEXT NOT NULL DEFAULT 'loan',   -- credit_card | student_loan | auto_loan | mortgage | personal | loan
    balance_cents       INTEGER NOT NULL,
    apr_bps             INTEGER NOT NULL,              -- APR in basis points: 2699 = 26.99%
    min_payment_cents   INTEGER NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS assets (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'other',        -- investment | retirement | property | vehicle | other
    value_cents  INTEGER NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- growth (trials, coupons, referrals)
-- Free Pro time — a trial, a coupon or a referral reward — is stored as a zero-value row in
-- `billing_passes`, so `billing.recompute_plan` grants and expires it with no special cases.
-- These tables only record *why* the time was given, and stop it being given twice.

CREATE TABLE IF NOT EXISTS coupons (
    code             TEXT PRIMARY KEY,                 -- stored upper-case; compared case-insensitively
    tier             TEXT NOT NULL DEFAULT 'pro',
    days             INTEGER NOT NULL,                 -- days of free access granted
    max_redemptions  INTEGER,                          -- NULL = unlimited
    redeemed         INTEGER NOT NULL DEFAULT 0,
    expires_at       TEXT,                             -- NULL = never expires
    note             TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS coupon_redemptions (
    code        TEXT NOT NULL REFERENCES coupons(code) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    redeemed_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (code, user_id)                        -- one redemption per person per code
);

CREATE TABLE IF NOT EXISTS referrals (
    referee_id   INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,  -- one referrer per person, forever
    referrer_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code         TEXT NOT NULL,
    -- Set when both sides were actually rewarded. Held back until the referee verifies their email,
    -- so signing up with throwaway addresses doesn't mint free months.
    rewarded_at  TEXT,
    reward_days  INTEGER,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_referrals_referrer ON referrals(referrer_id, rewarded_at);

CREATE TABLE IF NOT EXISTS analytics_events (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    props_json  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_events_name ON analytics_events(name, created_at);
CREATE INDEX IF NOT EXISTS ix_events_user ON analytics_events(user_id, created_at);

-- ---------------------------------------------------------------- freelancer & tax
CREATE TABLE IF NOT EXISTS tax_profiles (        -- one row per user; created on first use
    user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    regime            TEXT NOT NULL DEFAULT 'IN_new',  -- a key in tax.REGIMES
    financial_year    TEXT,                            -- e.g. '2026-27'; NULL = whichever contains today
    business_share_default INTEGER NOT NULL DEFAULT 100,  -- % of a business-tagged expense that is deductible
    gst_registered    INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tax_tags (            -- per-transaction business classification
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,                     -- business | personal (explicitly not business)
    deduction       TEXT,                              -- a key in tax.DEDUCTIONS; NULL for income rows
    share_pct       INTEGER NOT NULL DEFAULT 100,      -- mixed-use: the deductible share of this charge
    client          TEXT,                              -- attribution for income rows
    note            TEXT,
    source          TEXT NOT NULL DEFAULT 'user',      -- user | rule | suggestion (who classified it)
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, transaction_id)
);
CREATE INDEX IF NOT EXISTS ix_tax_tags_kind ON tax_tags(user_id, kind);

CREATE TABLE IF NOT EXISTS tax_payments (        -- advance tax / estimated payments already made
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    paid_on      TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'advance',      -- advance | self_assessment | tds
    note         TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_tax_payments_user ON tax_payments(user_id, paid_on);

CREATE TABLE IF NOT EXISTS receipts (            -- files attached to a transaction as proof
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    transaction_id  INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    filename        TEXT NOT NULL,
    content_type    TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    stored_path     TEXT NOT NULL,                     -- relative to settings.receipts_dir
    -- Parsed from the file when possible, and used to suggest which transaction it belongs to.
    parsed_total_cents INTEGER,
    parsed_date        TEXT,
    parsed_merchant    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_receipts_user ON receipts(user_id, transaction_id);

-- ---------------------------------------------------------------- households (Family plan)
CREATE TABLE IF NOT EXISTS households (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    owner_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS household_members (
    household_id  INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role          TEXT NOT NULL DEFAULT 'member',   -- owner | member | viewer
    joined_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (household_id, user_id)
);
-- One household per person. Without this a user could be pulled into two ledgers and their
-- transactions would appear in both households' totals.
CREATE UNIQUE INDEX IF NOT EXISTS ux_household_members_user ON household_members(user_id);

CREATE TABLE IF NOT EXISTS household_invites (
    token_hash    TEXT PRIMARY KEY,                 -- sha256 of the emailed token; the raw value is never stored
    household_id  INTEGER NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    email         TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'member',
    invited_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    expires_at    TEXT NOT NULL,
    accepted_at   TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_household_invites ON household_invites(household_id, accepted_at);

-- ---------------------------------------------------------------- automations (proactive agent)
CREATE TABLE IF NOT EXISTS alert_rules (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,                        -- a key in automations.RULE_TYPES
    params_json  TEXT NOT NULL DEFAULT '{}',           -- threshold, category, … (validated per kind)
    channels     TEXT NOT NULL DEFAULT 'inapp',        -- comma-separated: inapp,email
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_alert_rules_user ON alert_rules(user_id, active);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rule_id     INTEGER REFERENCES alert_rules(id) ON DELETE SET NULL,
    kind        TEXT NOT NULL,
    severity    TEXT NOT NULL DEFAULT 'info',          -- high | medium | low | info
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    action_url  TEXT,
    -- Identifies the underlying *event*, so a watcher that runs every few hours can re-detect the
    -- same condition without ever notifying twice. Keys embed the period or date they refer to
    -- (e.g. 'category_over:Dining:2026-09'), which is what gives each alert its natural cadence.
    dedupe_key  TEXT NOT NULL,
    emailed_at  TEXT,
    read_at     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS ix_notifications_user ON notifications(user_id, read_at, id);

CREATE TABLE IF NOT EXISTS automation_state (   -- one row per user: when the watchers last swept
    user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    last_run_at     TEXT,
    last_digest_at  TEXT,
    digest_period   TEXT NOT NULL DEFAULT 'weekly',    -- off | weekly | monthly
    last_error      TEXT
);

CREATE TABLE IF NOT EXISTS challenges (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type         TEXT NOT NULL,                        -- no_spend | category_cap | merchant_break
    title        TEXT NOT NULL,
    params_json  TEXT NOT NULL DEFAULT '{}',
    start_date   TEXT NOT NULL,
    end_date     TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- security (2FA, activity log)
CREATE TABLE IF NOT EXISTS mfa_recovery_codes (   -- single-use fallbacks for a lost authenticator
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash  TEXT NOT NULL,                      -- sha256 of the normalised code; the code itself is shown once
    used_at    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, code_hash)
);

CREATE TABLE IF NOT EXISTS security_events (      -- what the user sees under "Recent security activity"
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,                     -- login | login_mfa | mfa_enabled | mfa_disabled | ...
    ip          TEXT,
    user_agent  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_security_events_user ON security_events(user_id, id);

-- ---------------------------------------------------------------- SMS / alert capture
CREATE TABLE IF NOT EXISTS capture_tokens (       -- lets a phone forward bank SMS without a session
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,              -- sha256; the raw token is shown once when minted
    account_id  INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    last_used_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- split & settle
CREATE TABLE IF NOT EXISTS splits (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    transaction_id  INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    person          TEXT NOT NULL,                 -- display name, compared case-insensitively
    person_vpa      TEXT,                          -- their UPI id, if known (for "pay them" links)
    -- > 0: they owe the user; < 0: the user owes them. Integer minor units like every other amount.
    amount_cents    INTEGER NOT NULL,
    note            TEXT,
    settled_at      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_splits_user ON splits(user_id, settled_at);
"""


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


_db_path: Path = settings.database_path


def set_db_path(path: Path) -> None:
    """Point the app at a different database (used by tests)."""
    global _db_path
    _db_path = Path(path)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect(_db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


MIGRATIONS = {  # (table, column): DDL for databases created before the column existed
    ("users", "password_hash"): "ALTER TABLE users ADD COLUMN password_hash TEXT",
    ("users", "is_demo"): "ALTER TABLE users ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0",
    ("users", "email_verified_at"): "ALTER TABLE users ADD COLUMN email_verified_at TEXT",
    ("users", "google_sub"): "ALTER TABLE users ADD COLUMN google_sub TEXT",
    ("accounts", "shared"): "ALTER TABLE accounts ADD COLUMN shared INTEGER NOT NULL DEFAULT 1",
    ("goals", "household_id"): "ALTER TABLE goals ADD COLUMN household_id INTEGER",
    ("users", "referral_code"): "ALTER TABLE users ADD COLUMN referral_code TEXT",
    ("users", "trial_started_at"): "ALTER TABLE users ADD COLUMN trial_started_at TEXT",
    # why a pass exists: purchase | trial | coupon:<CODE> | referral | grant
    ("billing_passes", "reason"): "ALTER TABLE billing_passes ADD COLUMN reason TEXT NOT NULL DEFAULT 'purchase'",
    # two-factor sign-in: the secret is written at setup and only counts once totp_enabled_at is set
    ("users", "totp_secret"): "ALTER TABLE users ADD COLUMN totp_secret TEXT",
    ("users", "totp_enabled_at"): "ALTER TABLE users ADD COLUMN totp_enabled_at TEXT",
    ("users", "totp_last_step"): "ALTER TABLE users ADD COLUMN totp_last_step INTEGER",   # replay guard
    ("users", "upi_vpa"): "ALTER TABLE users ADD COLUMN upi_vpa TEXT",   # where friends pay back a split
    # device list: which browser a session belongs to and when it was last used
    ("sessions", "user_agent"): "ALTER TABLE sessions ADD COLUMN user_agent TEXT",
    ("sessions", "ip"): "ALTER TABLE sessions ADD COLUMN ip TEXT",
    ("sessions", "last_seen_at"): "ALTER TABLE sessions ADD COLUMN last_seen_at TEXT",
}
# SQLite can't add a UNIQUE column in place, so uniqueness lives in an index created after migrations.
POST_MIGRATION = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_referral_code ON users(referral_code) WHERE referral_code IS NOT NULL;
"""


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        for (table, column), ddl in MIGRATIONS.items():
            if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(ddl)
        conn.executescript(POST_MIGRATION)


def query_df(sql: str, params: tuple | dict = ()) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def load_transactions(user_id: int, member_ids: list[int] | None = None) -> pd.DataFrame:
    """Transactions as a typed DataFrame — the single source for every analytic.

    With `member_ids` this loads a whole household's ledger instead of one person's. Accounts marked
    `shared = 0` are excluded from that wider view, except for the viewer's own: a private account
    stays in its owner's personal view and never appears in anyone else's. Transactions with no
    account attached (a CSV imported without one) are treated as shared, since there is no account
    to have marked private.

    The `member_id` column identifies whose transaction each row is, which is what lets the
    household views break spending down per person.
    """
    ids = sorted({user_id, *(member_ids or [])})
    placeholders = ",".join("?" * len(ids))
    df = query_df(
        f"""SELECT t.id, t.user_id AS member_id, t.account_id, a.name AS account, t.date, t.description,
                   t.merchant, t.amount_cents, t.category, t.category_source, t.is_transfer, t.notes
            FROM transactions t LEFT JOIN accounts a ON a.id = t.account_id
            WHERE t.user_id IN ({placeholders})
              AND (a.id IS NULL OR a.shared = 1 OR t.user_id = ?)
            ORDER BY t.date, t.id""",
        (*ids, user_id),
    )
    df["date"] = pd.to_datetime(df["date"])
    df["amount_cents"] = df["amount_cents"].astype("int64")
    df["is_transfer"] = df["is_transfer"].astype(bool)
    df["month"] = df["date"].dt.strftime("%Y-%m")
    return df
