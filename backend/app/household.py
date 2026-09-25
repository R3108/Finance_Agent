"""Households: several people sharing one ledger (the Family plan).

The rest of the app scopes every query to a single `user_id`. A household widens that to a *set* of
user ids, so the model here is deliberately small and the widening happens in exactly two places:
`db.load_transactions` (which takes a scope) and `services.UserData` (which decides the scope).

Three rules keep this honest:

*   **One household per person.** Enforced by a unique index on `household_members.user_id`. Without
    it someone's transactions could be counted in two households' totals at once.
*   **Private accounts stay private.** An account marked `shared = 0` is visible only in its owner's
    personal view, never in the household ledger — including to the household owner.
*   **Roles gate writes, not reads.** Every member sees the same household figures; `viewer` simply
    can't change shared budgets, goals or membership. The check lives in `require_role`.

Plan inheritance is what makes the tier sellable: the owner buys Family, and `effective_plan` below
hands that plan to every member, so a member's own `users.plan` row stays 'free' and nothing in
billing has to know households exist.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from . import auth
from .db import get_conn

MAX_MEMBERS = 5
INVITE_TTL = timedelta(days=7)
ROLES = ("owner", "member", "viewer")
#: Roles allowed to change shared things (budgets, goals, membership, the household name).
WRITE_ROLES = ("owner", "member")


class HouseholdError(Exception):
    """A household operation that can't be completed, phrased for the person who tried it."""


@dataclass(frozen=True)
class Membership:
    household_id: int
    name: str
    owner_id: int
    role: str

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def can_write(self) -> bool:
        return self.role in WRITE_ROLES


def membership_for(user_id: int) -> Membership | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT h.id, h.name, h.owner_id, m.role
               FROM household_members m JOIN households h ON h.id = m.household_id
               WHERE m.user_id = ?""", (user_id,)).fetchone()
    return Membership(row["id"], row["name"], row["owner_id"], row["role"]) if row else None


def member_ids(household_id: int) -> list[int]:
    with get_conn() as conn:
        return [r[0] for r in conn.execute(
            "SELECT user_id FROM household_members WHERE household_id = ? ORDER BY user_id",
            (household_id,))]


def members(household_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT m.user_id, m.role, m.joined_at, u.name, u.email
               FROM household_members m JOIN users u ON u.id = m.user_id
               WHERE m.household_id = ?
               ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'member' THEN 1 ELSE 2 END, u.name""",
            (household_id,)).fetchall()
    return [dict(r) for r in rows]


def effective_plan(user_id: int, own_plan: str) -> str:
    """The plan this user actually gets: their own, or the household owner's if that is better.

    Called from `UserData.plan`, so a member of a Family household sees Pro-and-above features
    without ever having a paid row of their own. Compared by rank, so a member who separately buys
    Pro is never downgraded by joining a household on a lesser plan.
    """
    from .plans import RANK

    m = membership_for(user_id)
    if m is None or m.owner_id == user_id:
        return own_plan
    with get_conn() as conn:
        row = conn.execute("SELECT plan FROM users WHERE id = ?", (m.owner_id,)).fetchone()
    owner_plan = row["plan"] if row else "free"
    # only a household plan is inherited; an owner on Pro doesn't make their members Pro
    if RANK.get(owner_plan, 0) < RANK["family"]:
        return own_plan
    return owner_plan if RANK.get(owner_plan, 0) > RANK.get(own_plan, 0) else own_plan


# ----------------------------------------------------------------------------- lifecycle

def create(user_id: int, name: str) -> Membership:
    if membership_for(user_id):
        raise HouseholdError("You're already in a household. Leave it before starting another.")
    name = name.strip() or "Our household"
    with get_conn() as conn:
        household_id = conn.execute("INSERT INTO households (name, owner_id) VALUES (?,?)",
                                    (name, user_id)).lastrowid
        conn.execute("INSERT INTO household_members (household_id, user_id, role) VALUES (?,?, 'owner')",
                     (household_id, user_id))
    return Membership(household_id, name, user_id, "owner")


def rename(household_id: int, name: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE households SET name = ? WHERE id = ?", (name.strip() or "Our household", household_id))


def invite(household_id: int, email: str, role: str, invited_by: int) -> str:
    """Create a single-use invite and return the raw token (emailed; only its hash is stored)."""
    if role not in ("member", "viewer"):
        raise HouseholdError("Invite someone as a member or a viewer.")
    email = auth.normalize_email(email)
    if not auth.EMAIL_RE.match(email):
        raise HouseholdError("Enter a valid email address.")
    if len(member_ids(household_id)) >= MAX_MEMBERS:
        raise HouseholdError(f"A household holds up to {MAX_MEMBERS} people.")
    with get_conn() as conn:
        already = conn.execute(
            """SELECT 1 FROM household_members m JOIN users u ON u.id = m.user_id
               WHERE m.household_id = ? AND u.email = ?""", (household_id, email)).fetchone()
    if already:
        raise HouseholdError("They're already in this household.")

    token = secrets.token_urlsafe(32)
    with get_conn() as conn:
        # a fresh invite supersedes any outstanding one for the same address
        conn.execute("DELETE FROM household_invites WHERE household_id = ? AND email = ? AND accepted_at IS NULL",
                     (household_id, email))
        conn.execute(
            """INSERT INTO household_invites (token_hash, household_id, email, role, invited_by, expires_at)
               VALUES (?,?,?,?,?,?)""",
            (hashlib.sha256(token.encode()).hexdigest(), household_id, email, role, invited_by,
             (auth._now() + INVITE_TTL).isoformat(sep=" ", timespec="seconds")))
    return token


def pending_invites(household_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT email, role, created_at, expires_at FROM household_invites
               WHERE household_id = ? AND accepted_at IS NULL AND expires_at >= ?
               ORDER BY created_at DESC""",
            (household_id, auth._now().isoformat(sep=" ", timespec="seconds"))).fetchall()
    return [dict(r) for r in rows]


def revoke_invite(household_id: int, email: str) -> int:
    with get_conn() as conn:
        return conn.execute(
            "DELETE FROM household_invites WHERE household_id = ? AND email = ? AND accepted_at IS NULL",
            (household_id, auth.normalize_email(email))).rowcount


def preview_invite(token: str) -> dict:
    """What an invite link points at, for the accept screen — without redeeming it."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT i.email, i.role, i.expires_at, i.accepted_at, h.name, u.name AS invited_by
               FROM household_invites i JOIN households h ON h.id = i.household_id
               LEFT JOIN users u ON u.id = i.invited_by
               WHERE i.token_hash = ?""",
            (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not row or row["accepted_at"] or row["expires_at"] < auth._now().isoformat(sep=" ", timespec="seconds"):
        raise HouseholdError("This invite link has expired or was already used. Ask for a new one.")
    return {"household": row["name"], "role": row["role"], "email": row["email"],
            "invited_by": row["invited_by"]}


def accept(token: str, user_id: int) -> Membership:
    """Redeem an invite exactly once, for the signed-in user."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = auth._now().isoformat(sep=" ", timespec="seconds")
    if membership_for(user_id):
        raise HouseholdError("You're already in a household. Leave it before joining another.")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT household_id, email, role FROM household_invites WHERE token_hash = ? AND accepted_at IS NULL AND expires_at >= ?",
            (token_hash, now)).fetchone()
        if not row:
            raise HouseholdError("This invite link has expired or was already used. Ask for a new one.")
        user = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
        # the invite is addressed to one person: redeeming it from another account would let a
        # forwarded link hand over access to the household's whole ledger
        if not user or auth.normalize_email(user["email"] or "") != row["email"]:
            raise HouseholdError(f"This invite was sent to {row['email']}. Sign in as that account to accept it.")
        if len(member_ids(row["household_id"])) >= MAX_MEMBERS:
            raise HouseholdError(f"That household is full ({MAX_MEMBERS} people).")
        # claiming the invite and joining happen in one transaction, so a double-click can't
        # consume the invite without adding the member
        conn.execute("UPDATE household_invites SET accepted_at = ? WHERE token_hash = ?", (now, token_hash))
        conn.execute("INSERT INTO household_members (household_id, user_id, role) VALUES (?,?,?)",
                     (row["household_id"], user_id, row["role"]))
    return membership_for(user_id)


def set_role(household_id: int, user_id: int, role: str, acting_user: int) -> None:
    if role not in ROLES:
        raise HouseholdError(f"Role must be one of: {', '.join(ROLES)}")
    if role == "owner":
        raise HouseholdError("Transfer ownership instead of setting someone as owner.")
    with get_conn() as conn:
        owner = conn.execute("SELECT owner_id FROM households WHERE id = ?", (household_id,)).fetchone()
        if owner and owner["owner_id"] == user_id:
            raise HouseholdError("The household owner's role can't be changed.")
        changed = conn.execute("UPDATE household_members SET role = ? WHERE household_id = ? AND user_id = ?",
                               (role, household_id, user_id)).rowcount
    if not changed:
        raise HouseholdError("That person isn't in this household.")


def remove_member(household_id: int, user_id: int) -> None:
    """Remove someone. Their own data is untouched — they simply stop appearing in the shared ledger."""
    with get_conn() as conn:
        owner = conn.execute("SELECT owner_id FROM households WHERE id = ?", (household_id,)).fetchone()
        if owner and owner["owner_id"] == user_id:
            raise HouseholdError("The owner can't be removed. Transfer ownership or delete the household.")
        conn.execute("DELETE FROM household_members WHERE household_id = ? AND user_id = ?",
                     (household_id, user_id))
        # shared goals stay with the household, not with the person leaving
        conn.execute("UPDATE goals SET household_id = NULL WHERE user_id = ? AND household_id = ?",
                     (user_id, household_id))


def transfer_ownership(household_id: int, to_user: int) -> None:
    if to_user not in member_ids(household_id):
        raise HouseholdError("That person isn't in this household.")
    with get_conn() as conn:
        previous = conn.execute("SELECT owner_id FROM households WHERE id = ?", (household_id,)).fetchone()["owner_id"]
        conn.execute("UPDATE households SET owner_id = ? WHERE id = ?", (to_user, household_id))
        conn.execute("UPDATE household_members SET role = 'owner' WHERE household_id = ? AND user_id = ?",
                     (household_id, to_user))
        conn.execute("UPDATE household_members SET role = 'member' WHERE household_id = ? AND user_id = ?",
                     (household_id, previous))


def delete(household_id: int) -> None:
    """Dissolve the household. Members keep every transaction, account and private goal they own."""
    with get_conn() as conn:
        conn.execute("UPDATE goals SET household_id = NULL WHERE household_id = ?", (household_id,))
        conn.execute("DELETE FROM households WHERE id = ?", (household_id,))


# ----------------------------------------------------------------------------- shared views

def spending_split(tx, member_names: dict[int, str]) -> list[dict]:
    """Who spent what over the loaded window — the question a shared ledger is actually for."""
    from . import analytics as an

    sp = an.spending_frame(tx)
    if sp.empty or "member_id" not in sp.columns:
        return []
    totals = sp.groupby("member_id")["spend_cents"].sum().sort_values(ascending=False)
    grand = int(totals.sum())
    return [{"user_id": int(uid), "name": member_names.get(int(uid), "Member"),
             "spent": an.money(int(cents)), "share_pct": an.pct(int(cents), grand)}
            for uid, cents in totals.items()]
