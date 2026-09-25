"""Per-user data access that feeds the analytics layer (used by both the REST API and the agent tools).

`UserData` is also where the single-user / whole-household decision is made. Everything downstream —
`analytics`, `planning`, the agent's tools — reads the frames this class hands it and never asks who
owns a row, so widening a view to a household is a change here and in `db.load_transactions`, not a
change spread across forty functions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import cached_property

import pandas as pd

from . import analytics as an
from .db import get_conn, load_transactions, query_df


@dataclass
class UserData:
    user_id: int
    #: 'personal' = just this person. 'household' = everyone they share a ledger with, minus any
    #: account another member marked private. Ignored when the user isn't in a household.
    scope: str = field(default="personal")

    # --- who this view covers ---------------------------------------------------------------
    @cached_property
    def membership(self):
        from .household import membership_for
        return membership_for(self.user_id)

    @cached_property
    def household_scope(self) -> bool:
        """Whether this view is actually widened — asking for it without a household is a no-op."""
        return self.scope == "household" and self.membership is not None

    @cached_property
    def member_ids(self) -> list[int]:
        from .household import member_ids
        return member_ids(self.membership.household_id) if self.household_scope else [self.user_id]

    @cached_property
    def member_names(self) -> dict[int, str]:
        if not self.household_scope:
            return {}
        from .household import members
        return {m["user_id"]: m["name"] for m in members(self.membership.household_id)}

    def _scoped(self, column: str = "user_id") -> tuple[str, tuple]:
        """A `WHERE` fragment and params matching every user this view covers."""
        ids = self.member_ids
        return f"{column} IN ({','.join('?' * len(ids))})", tuple(ids)

    # --- frames -----------------------------------------------------------------------------
    @cached_property
    def tx(self) -> pd.DataFrame:
        return load_transactions(self.user_id, self.member_ids if self.household_scope else None)

    @cached_property
    def accounts(self) -> pd.DataFrame:
        where, params = self._scoped()
        # a member's private account is theirs alone, even inside the household view
        return query_df(f"SELECT * FROM accounts WHERE {where} AND (shared = 1 OR user_id = ?) ORDER BY id",
                        (*params, self.user_id))

    @cached_property
    def goals(self) -> pd.DataFrame:
        """Own goals, plus the household's shared goals when there is a household."""
        if self.membership is None:
            return query_df("SELECT * FROM goals WHERE user_id = ? ORDER BY id", (self.user_id,))
        return query_df("SELECT * FROM goals WHERE user_id = ? OR household_id = ? ORDER BY id",
                        (self.user_id, self.membership.household_id))

    @cached_property
    def budgets(self) -> dict[str, int]:
        """Monthly limits by category.

        In a household view the members' limits are summed per category, so a combined ledger is
        compared against a combined allowance rather than one person's.
        """
        where, params = self._scoped()
        with get_conn() as conn:
            rows = conn.execute(
                f"SELECT category, SUM(monthly_limit_cents) AS limit_cents FROM budgets "
                f"WHERE {where} GROUP BY category", params).fetchall()
        return {r["category"]: int(r["limit_cents"]) for r in rows}

    @cached_property
    def sub_statuses(self) -> dict[str, str]:
        where, params = self._scoped()
        with get_conn() as conn:
            rows = conn.execute(f"SELECT sub_key, status FROM subscription_actions WHERE {where}", params).fetchall()
        return {r["sub_key"]: r["status"] for r in rows}

    @cached_property
    def debts(self) -> pd.DataFrame:
        where, params = self._scoped()
        return query_df(f"SELECT * FROM debts WHERE {where} ORDER BY id", params)

    @cached_property
    def assets(self) -> pd.DataFrame:
        where, params = self._scoped()
        return query_df(f"SELECT * FROM assets WHERE {where} ORDER BY id", params)

    @cached_property
    def challenges(self) -> pd.DataFrame:
        return query_df("SELECT * FROM challenges WHERE user_id = ? ORDER BY start_date DESC, id DESC",
                        (self.user_id,))

    @cached_property
    def currency(self) -> str:
        """The user's display currency. Amounts are never converted — this only changes formatting."""
        from . import money as cur
        with get_conn() as conn:
            row = conn.execute("SELECT currency FROM users WHERE id = ?", (self.user_id,)).fetchone()
        return cur.normalize(row["currency"] if row else None)

    @cached_property
    def plan(self) -> str:
        # re-derived from subscriptions/passes on each request, so a prepaid pass ends exactly on time,
        # then widened to the household owner's plan if they're paying for Family
        from .billing import refresh_plan
        from .household import effective_plan
        return effective_plan(self.user_id, refresh_plan(self.user_id))

    @cached_property
    def as_of(self) -> date:
        return an.resolve_as_of(self.tx)

    # --- convenience wrappers --------------------------------------------------------------
    def overview(self) -> dict:
        monthly = an.monthly_summary(self.tx, 12, self.as_of)
        subs = an.subscriptions_summary(self.tx, self.sub_statuses, self.as_of)
        out = {
            "as_of": self.as_of.isoformat(),
            "scope": "household" if self.household_scope else "personal",
            "balances": an.account_balances(self.tx, self.accounts),
            "current_month": an.category_breakdown(self.tx, as_of=self.as_of),
            "monthly": monthly,
            "health": an.health_score(self.tx, self.accounts, self.budgets, self.as_of),
            "safe_to_spend": an.safe_to_spend(self.tx, as_of=self.as_of),
            "subscriptions": {k: v for k, v in subs.items() if k != "items"},
            "insights": an.insights(self.tx, self.accounts, self.budgets, self.as_of)[:8],
        }
        if self.household_scope:
            from .household import spending_split
            out["household"] = {"name": self.membership.name, "role": self.membership.role,
                                "split": spending_split(self.tx, self.member_names)}
        return out
