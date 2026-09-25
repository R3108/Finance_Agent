"""Trials, coupons and referrals — the ways someone gets paid features without paying today.

All three grant the same thing: a stretch of time on a paid tier. Rather than inventing a parallel
entitlement path, `grant_days` writes a zero-value row into `billing_passes`, which
`billing.recompute_plan` already understands. Granted time therefore starts, stacks behind existing
paid time and expires with exactly the same code that runs for a real purchase — there is no
"is this a trial?" branch anywhere in the app.

The rules that stop this being a free-money machine live here:

*   a trial is recorded on `users.trial_started_at` and given once, ever;
*   a coupon has a redemption cap and a per-user unique key, so a code can't be reused by one person;
*   a referral reward is **held until the referee verifies their email**, which is what makes
    creating throwaway accounts pointless.

Coupons grant free days rather than a percentage off. A percentage discount on a Razorpay
subscription changes the plan the subscription renews at, so a "first month 50% off" code would
quietly discount every future month too; doing it properly needs Razorpay Offers, which is a
separate integration.
"""
from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

from . import plans
from .db import get_conn, query_df

log = logging.getLogger("ledgerly.growth")

TRIAL_DAYS = 14
TRIAL_TIER = "pro"
#: Days each side gets when a referral completes. Both, so sharing is worth doing and joining is
#: worth doing — a one-sided reward reads as spam to the person receiving the link.
REFERRAL_DAYS = 30
REFERRAL_TIER = "pro"
#: Ambiguous characters left out: someone will read a code off a screen and type it wrong.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class GrowthError(Exception):
    """A trial, coupon or referral that can't be applied, phrased for the person who tried."""


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _today() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")


# ----------------------------------------------------------------------------- granting time

def grant_days(user_id: int, days: int, tier: str = "pro", reason: str = "grant") -> dict:
    """Give someone `days` of `tier`, queued behind the access they already have.

    Queueing (rather than overwriting) means a trial that turns into a purchase, or two coupons,
    never costs anyone days they were already promised.

    It only queues behind access that is *at least as good*, though. Granting Family to someone
    halfway through a Pro trial has to upgrade them now — queueing it behind the trial would make
    the better tier arrive weeks later, which is the opposite of what granting it meant. A grant of
    a lower tier still queues, so those days aren't spent while a better tier is already running.
    """
    if days <= 0:
        raise GrowthError("A grant has to be at least one day.")
    if tier not in plans.RANK or tier == "free":
        raise GrowthError(f"Unknown tier '{tier}'.")
    now = _now()
    at_least = [t for t, rank in plans.RANK.items() if rank >= plans.RANK[tier] and t != "free"]
    with get_conn() as conn:
        row = conn.execute(
            f"""SELECT MAX(ends_at) AS e FROM billing_passes
                WHERE user_id = ? AND status = 'paid' AND ends_at > ?
                  AND tier IN ({','.join('?' * len(at_least))})""",
            (user_id, now, *at_least)).fetchone()
        starts = max(now, int(row["e"] or 0))
        ends = starts + days * 86400
        conn.execute(
            """INSERT INTO billing_passes (id, user_id, tier, period, amount, currency, status,
                                           reason, starts_at, ends_at)
               VALUES (?,?,?,?,0,?, 'paid', ?,?,?)""",
            (f"grant_{uuid.uuid4().hex[:20]}", user_id, tier, f"{days}d", plans.CURRENCY,
             reason, starts, ends))
    from .billing import recompute_plan
    plan = recompute_plan(user_id)
    return {"tier": tier, "days": days, "starts_at": starts, "ends_at": ends, "plan": plan}


def granted_access(user_id: int) -> dict | None:
    """The free access a user currently holds, if any — what the UI shows as 'trial ends in N days'."""
    now = _now()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT tier, reason, starts_at, ends_at FROM billing_passes
               WHERE user_id = ? AND status = 'paid' AND amount = 0 AND starts_at <= ? AND ends_at > ?
               ORDER BY ends_at DESC LIMIT 1""", (user_id, now, now)).fetchone()
    if not row:
        return None
    return {"tier": row["tier"], "reason": row["reason"],
            "ends_at": row["ends_at"], "days_left": max(0, (row["ends_at"] - now) // 86400)}


# ----------------------------------------------------------------------------- trials

def trial_status(user_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT trial_started_at, plan FROM users WHERE id = ?", (user_id,)).fetchone()
    started = row["trial_started_at"] if row else None
    active = granted_access(user_id)
    return {
        "available": started is None,
        "days": TRIAL_DAYS,
        "tier": TRIAL_TIER,
        "started_at": started,
        "active": bool(active and active["reason"] == "trial"),
        "days_left": active["days_left"] if active and active["reason"] == "trial" else None,
    }


def start_trial(user_id: int) -> dict:
    """Begin the one free trial this account gets. No card, no auto-charge at the end."""
    with get_conn() as conn:
        row = conn.execute("SELECT trial_started_at, is_demo FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise GrowthError("Account not found.")
        if row["is_demo"]:
            raise GrowthError("Demo sandboxes already include every feature — create an account to start a trial.")
        if row["trial_started_at"]:
            raise GrowthError("You've already used your free trial.")
        # written before the grant so two simultaneous clicks can't both pass the check above
        conn.execute("UPDATE users SET trial_started_at = ? WHERE id = ? AND trial_started_at IS NULL",
                     (_today(), user_id))
    granted = grant_days(user_id, TRIAL_DAYS, TRIAL_TIER, "trial")
    track(user_id, "trial_started", {"days": TRIAL_DAYS, "tier": TRIAL_TIER})
    return granted


# ----------------------------------------------------------------------------- coupons

def create_coupon(code: str, days: int, tier: str = "pro", max_redemptions: int | None = None,
                  expires_at: date | None = None, note: str | None = None) -> dict:
    code = code.strip().upper()
    if not 3 <= len(code) <= 32 or not code.replace("-", "").isalnum():
        raise GrowthError("A code is 3–32 letters, digits or hyphens.")
    if days <= 0:
        raise GrowthError("A coupon has to grant at least one day.")
    if tier not in plans.RANK or tier == "free":
        raise GrowthError(f"Unknown tier '{tier}'.")
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM coupons WHERE code = ?", (code,)).fetchone():
            raise GrowthError(f"The code {code} already exists.")
        conn.execute(
            "INSERT INTO coupons (code, tier, days, max_redemptions, expires_at, note) VALUES (?,?,?,?,?,?)",
            (code, tier, days, max_redemptions, expires_at.isoformat() if expires_at else None, note))
    return {"code": code, "tier": tier, "days": days, "max_redemptions": max_redemptions,
            "expires_at": expires_at.isoformat() if expires_at else None, "note": note}


def redeem_coupon(user_id: int, code: str) -> dict:
    code = code.strip().upper()
    today = date.today().isoformat()
    with get_conn() as conn:
        coupon = conn.execute("SELECT * FROM coupons WHERE code = ?", (code,)).fetchone()
        if not coupon:
            raise GrowthError("That code isn't valid.")
        if coupon["expires_at"] and coupon["expires_at"] < today:
            raise GrowthError("That code has expired.")
        if conn.execute("SELECT 1 FROM coupon_redemptions WHERE code = ? AND user_id = ?",
                        (code, user_id)).fetchone():
            raise GrowthError("You've already used that code.")
        # the conditional UPDATE is the claim: two simultaneous redemptions can't both take the
        # last remaining use of a capped code
        claimed = conn.execute(
            "UPDATE coupons SET redeemed = redeemed + 1 WHERE code = ? AND (max_redemptions IS NULL OR redeemed < max_redemptions)",
            (code,)).rowcount
        if not claimed:
            raise GrowthError("That code has been fully used.")
        conn.execute("INSERT INTO coupon_redemptions (code, user_id) VALUES (?,?)", (code, user_id))

    granted = grant_days(user_id, int(coupon["days"]), coupon["tier"], f"coupon:{code}")
    track(user_id, "coupon_redeemed", {"code": code, "days": coupon["days"]})
    return {**granted, "code": code}


def list_coupons() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM coupons ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def delete_coupon(code: str) -> int:
    with get_conn() as conn:
        return conn.execute("DELETE FROM coupons WHERE code = ?", (code.strip().upper(),)).rowcount


# ----------------------------------------------------------------------------- referrals

def referral_code(user_id: int) -> str:
    """This user's code to share, minted on first request and stable afterwards."""
    with get_conn() as conn:
        row = conn.execute("SELECT referral_code FROM users WHERE id = ?", (user_id,)).fetchone()
        if row and row["referral_code"]:
            return row["referral_code"]
    for _ in range(10):
        candidate = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        try:
            with get_conn() as conn:
                conn.execute("UPDATE users SET referral_code = ? WHERE id = ? AND referral_code IS NULL",
                             (candidate, user_id))
                row = conn.execute("SELECT referral_code FROM users WHERE id = ?", (user_id,)).fetchone()
            if row and row["referral_code"]:
                return row["referral_code"]
        except Exception:      # the unique index rejected a collision; try another
            continue
    raise GrowthError("Couldn't create a referral code. Try again.")


def attribute_referral(referee_id: int, code: str | None) -> bool:
    """Record who referred a new signup. The reward is not paid yet — see `complete_referral`."""
    if not code:
        return False
    code = code.strip().upper()
    with get_conn() as conn:
        referrer = conn.execute("SELECT id FROM users WHERE referral_code = ?", (code,)).fetchone()
        if not referrer or int(referrer["id"]) == referee_id:
            return False      # unknown code, or someone using their own link
        if conn.execute("SELECT 1 FROM referrals WHERE referee_id = ?", (referee_id,)).fetchone():
            return False      # a person can only ever be referred once
        conn.execute("INSERT INTO referrals (referee_id, referrer_id, code) VALUES (?,?,?)",
                     (referee_id, int(referrer["id"]), code))
    track(referee_id, "referral_attributed", {"code": code})
    return True


def complete_referral(referee_id: int) -> dict | None:
    """Pay both sides, once the referee has verified their email. Safe to call repeatedly."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT referrer_id FROM referrals WHERE referee_id = ? AND rewarded_at IS NULL",
            (referee_id,)).fetchone()
        if not row:
            return None
        verified = conn.execute("SELECT email_verified_at FROM users WHERE id = ?", (referee_id,)).fetchone()
        if not verified or not verified["email_verified_at"]:
            return None
        # claim it first: this is what makes a second call a no-op rather than a second payout
        claimed = conn.execute(
            "UPDATE referrals SET rewarded_at = ?, reward_days = ? WHERE referee_id = ? AND rewarded_at IS NULL",
            (_today(), REFERRAL_DAYS, referee_id)).rowcount
        if not claimed:
            return None
        referrer_id = int(row["referrer_id"])

    grant_days(referee_id, REFERRAL_DAYS, REFERRAL_TIER, "referral")
    grant_days(referrer_id, REFERRAL_DAYS, REFERRAL_TIER, "referral")
    track(referrer_id, "referral_completed", {"referee_id": referee_id, "days": REFERRAL_DAYS})
    log.info("Referral completed: %s -> %s (%s days each)", referrer_id, referee_id, REFERRAL_DAYS)
    return {"referrer_id": referrer_id, "referee_id": referee_id, "days": REFERRAL_DAYS}


def referral_status(user_id: int, app_url: str) -> dict:
    code = referral_code(user_id)
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT r.rewarded_at, r.reward_days, u.name, u.created_at
               FROM referrals r JOIN users u ON u.id = r.referee_id
               WHERE r.referrer_id = ? ORDER BY r.created_at DESC""", (user_id,)).fetchall()
    joined = [dict(r) for r in rows]
    earned = sum(int(r["reward_days"] or 0) for r in joined)
    return {
        "code": code,
        "link": f"{app_url}/login?mode=signup&ref={code}",
        "reward_days": REFERRAL_DAYS,
        "invited": len(joined),
        "rewarded": sum(1 for r in joined if r["rewarded_at"]),
        "pending": sum(1 for r in joined if not r["rewarded_at"]),
        "days_earned": earned,
        "people": [{"name": r["name"], "joined": r["created_at"],
                    "status": "rewarded" if r["rewarded_at"] else "waiting on email confirmation"}
                   for r in joined],
    }


# ----------------------------------------------------------------------------- product analytics

def track(user_id: int | None, name: str, props: dict | None = None) -> None:
    """Record a product event. Never raises: analytics must not be able to fail a user's request."""
    try:
        with get_conn() as conn:
            conn.execute("INSERT INTO analytics_events (user_id, name, props_json) VALUES (?,?,?)",
                         (user_id, name[:60], json.dumps(props or {}, default=str)[:2000]))
    except Exception:
        log.debug("Event %r not recorded", name, exc_info=True)


def events(name: str | None = None, days: int = 30) -> "object":
    where = "WHERE created_at >= datetime('now', ?)"
    params: tuple = (f"-{days} days",)
    if name:
        where += " AND name = ?"
        params += (name,)
    return query_df(f"SELECT name, user_id, props_json, created_at FROM analytics_events {where}", params)


def overview(user_id: int, app_url: str) -> dict:
    """Everything the Rewards page needs: trial state, referral stats and current free access."""
    return {
        "trial": trial_status(user_id),
        "referral": referral_status(user_id, app_url),
        "granted": granted_access(user_id),
    }
