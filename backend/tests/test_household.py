"""Households: scoping, private accounts, invites, roles and plan inheritance."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth, household, mailer
from app.db import get_conn
from app.services import UserData

PW = "correct horse battery"


@pytest.fixture()
def sent(monkeypatch):
    out: list[dict] = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: out.append(
        {"to": to, "subject": subject, "text": text}))
    return out


@pytest.fixture()
def api(seeded, sent):
    from app.main import app
    auth._failures.clear()
    return lambda: TestClient(app)


def make_user(client: TestClient, *, sample: bool = False) -> str:
    """Sign a fresh account up on `client` and return its email."""
    email = f"hh-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/signup", json={"name": email.split("@")[0], "email": email,
                                              "password": PW, "sample_data": sample, "currency": "INR"})
    assert r.status_code == 201, r.text
    with get_conn() as conn:   # invites require a verified address
        conn.execute("UPDATE users SET email_verified_at = datetime('now') WHERE email = ?", (email,))
    return email


def user_id_for(email: str) -> int:
    with get_conn() as conn:
        return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]


def set_plan(email: str, plan: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET plan = ? WHERE email = ?", (plan, email))


def invite_token(sent: list[dict], to: str) -> str:
    mail = next(m for m in reversed(sent) if m["to"] == to)
    return mail["text"].split("token=")[1].split()[0]


def join(api, sent, owner_email: str, *, role: str = "member", sample: bool = False):
    """Invite a brand-new account into the owner's household and accept it. Returns (client, email)."""
    owner = api()
    owner.post("/api/auth/login", json={"email": owner_email, "password": PW})
    guest = api()
    guest_email = make_user(guest, sample=sample)
    assert owner.post("/api/household/invites", json={"email": guest_email, "role": role}).status_code == 201
    token = invite_token(sent, guest_email)
    assert guest.post(f"/api/household/invites/accept?token={token}").status_code == 200
    return guest, guest_email


@pytest.fixture()
def family(api, sent):
    """A Family-plan owner with sample data, and one member who also has sample data."""
    owner = api()
    owner_email = make_user(owner, sample=True)
    set_plan(owner_email, "family")
    assert owner.post("/api/household", json={"name": "Sharma Household"}).status_code == 201
    member, member_email = join(api, sent, owner_email, sample=True)
    return {"owner": owner, "owner_email": owner_email, "member": member, "member_email": member_email}


# ----------------------------------------------------------------------------- creation & plan

def test_household_needs_the_family_plan(api):
    c = api()
    make_user(c)
    r = c.post("/api/household", json={"name": "Nope"})
    assert r.status_code == 402 and r.json()["feature"] == "household"


def test_members_inherit_the_owners_family_plan(family):
    """The commercial point of the tier: the owner pays once and everyone gets the features."""
    assert family["member"].get("/api/me").json()["plan"] == "family"
    with get_conn() as conn:   # the member's own row is untouched — billing knows nothing of households
        row = conn.execute("SELECT plan FROM users WHERE email = ?", (family["member_email"],)).fetchone()
    assert row["plan"] == "free"


def test_a_member_keeps_a_better_plan_of_their_own(api, sent, family):
    """Joining a household must never downgrade someone who already pays for themselves."""
    set_plan(family["owner_email"], "pro")   # owner is no longer on Family
    set_plan(family["member_email"], "pro")
    assert family["member"].get("/api/me").json()["plan"] == "pro"
    set_plan(family["owner_email"], "family")


def test_me_reports_household_membership(api, family):
    """The sidebar shows its Mine/Household switch from this flag alone, so it has to be right."""
    assert family["member"].get("/api/me").json()["in_household"] is True
    solo = api()
    make_user(solo)
    assert solo.get("/api/me").json()["in_household"] is False


def test_one_household_per_person(family):
    r = family["member"].post("/api/household", json={"name": "Second"})
    assert r.status_code == 409 and "already in a household" in r.json()["detail"]


# ----------------------------------------------------------------------------- scoping

def test_household_scope_combines_both_ledgers(family):
    personal = family["owner"].get("/api/overview").json()
    shared = family["owner"].get("/api/overview?scope=household").json()
    assert personal["scope"] == "personal" and shared["scope"] == "household"
    assert shared["current_month"]["total"] > personal["current_month"]["total"]
    assert {s["name"] for s in shared["household"]["split"]}


def test_scope_is_ignored_without_a_household(api):
    c = api()
    make_user(c, sample=True)
    assert c.get("/api/overview?scope=household").json()["scope"] == "personal"


def test_private_accounts_never_reach_the_household_ledger(family):
    """Compared by transaction id, not account name: both personas seed the same account names.

    Restricted to a recent window so a single page holds every matching row.
    """
    from datetime import date, timedelta

    as_of = date.fromisoformat(family["member"].get("/api/me").json()["as_of"])
    window = f"start={(as_of - timedelta(days=20)).isoformat()}&limit=500"

    private = family["member"].get("/api/accounts").json()[0]
    own = family["member"].get(f"/api/transactions?{window}").json()
    private_txn_ids = {t["id"] for t in own["transactions"] if t["account"] == private["name"]}
    assert private_txn_ids and own["total_count"] <= 500

    before = family["owner"].get(f"/api/transactions?scope=household&{window}").json()
    assert private_txn_ids <= {t["id"] for t in before["transactions"]}

    assert family["member"].put(f"/api/accounts/{private['id']}/sharing",
                                json={"shared": False}).status_code == 200

    after = family["owner"].get(f"/api/transactions?scope=household&{window}").json()
    assert not private_txn_ids & {t["id"] for t in after["transactions"]}
    assert after["total_count"] == before["total_count"] - len(private_txn_ids)

    # …but its owner still sees every one of them in their own view
    still_own = family["member"].get(f"/api/transactions?{window}").json()["transactions"]
    assert private_txn_ids <= {t["id"] for t in still_own}


def test_only_the_account_owner_can_change_its_sharing(family):
    member_account = family["member"].get("/api/accounts").json()[0]
    r = family["owner"].put(f"/api/accounts/{member_account['id']}/sharing", json={"shared": False})
    assert r.status_code == 404


def test_household_budgets_are_summed_across_members(family):
    owner_id = user_id_for(family["owner_email"])
    personal = UserData(owner_id).budgets
    shared = UserData(owner_id, scope="household").budgets
    assert personal and shared
    assert all(shared[c] >= personal[c] for c in personal)
    assert any(shared[c] > personal[c] for c in personal)


# ----------------------------------------------------------------------------- invites

def test_invite_link_is_emailed_and_never_returned(api, sent, family):
    r = family["owner"].post("/api/household/invites", json={"email": "guest@example.com"})
    assert r.status_code == 201
    assert "token" not in r.text, "returning the link would let any caller mint themselves access"
    assert any("token=" in m["text"] for m in sent if m["to"] == "guest@example.com")


def test_invite_can_only_be_accepted_by_its_addressee(api, sent, family):
    family["owner"].post("/api/household/invites", json={"email": "intended@example.com"})
    token = invite_token(sent, "intended@example.com")
    other = api()
    make_user(other)
    r = other.post(f"/api/household/invites/accept?token={token}")
    assert r.status_code == 409 and "intended@example.com" in r.json()["detail"]


def test_invite_is_single_use(api, sent, family):
    guest, guest_email = join(api, sent, family["owner_email"])
    token = invite_token(sent, guest_email)
    assert guest.post(f"/api/household/invites/accept?token={token}").status_code == 409


def test_invite_preview_does_not_redeem_it(api, sent, family):
    family["owner"].post("/api/household/invites", json={"email": "peek@example.com", "role": "viewer"})
    token = invite_token(sent, "peek@example.com")
    preview = api().get(f"/api/household/invites/preview?token={token}").json()
    assert preview["household"] == "Sharma Household" and preview["role"] == "viewer"
    assert household.pending_invites(
        household.membership_for(user_id_for(family["owner_email"])).household_id)


def test_household_is_capped(api, sent, family):
    for _ in range(household.MAX_MEMBERS - 2):     # owner + first member already in
        join(api, sent, family["owner_email"])
    r = family["owner"].post("/api/household/invites", json={"email": "one-too-many@example.com"})
    assert r.status_code == 409 and "up to" in r.json()["detail"]


def test_revoked_invite_stops_working(api, sent, family):
    family["owner"].post("/api/household/invites", json={"email": "revoked@example.com"})
    token = invite_token(sent, "revoked@example.com")
    assert family["owner"].request("DELETE", "/api/household/invites",
                                   params={"email": "revoked@example.com"}).json()["revoked"] == 1
    guest = api()
    with get_conn() as conn:
        conn.execute("INSERT INTO users (name, email, password_hash, email_verified_at) "
                     "VALUES ('R', 'revoked@example.com', ?, datetime('now'))", (auth.hash_password(PW),))
    guest.post("/api/auth/login", json={"email": "revoked@example.com", "password": PW})
    assert guest.post(f"/api/household/invites/accept?token={token}").status_code == 409


# ----------------------------------------------------------------------------- roles

def test_viewers_cannot_change_the_household(api, sent, family):
    viewer, _ = join(api, sent, family["owner_email"], role="viewer")
    assert viewer.get("/api/overview?scope=household").json()["scope"] == "household"
    assert viewer.patch("/api/household", json={"name": "Renamed"}).status_code == 403
    assert viewer.post("/api/household/invites", json={"email": "x@example.com"}).status_code == 403


def test_only_the_owner_manages_people(api, sent, family):
    member_id = user_id_for(family["member_email"])
    assert family["member"].put(f"/api/household/members/{member_id}",
                                json={"role": "viewer"}).status_code == 403
    assert family["owner"].put(f"/api/household/members/{member_id}",
                               json={"role": "viewer"}).status_code == 200


def test_the_owner_cannot_be_removed_or_demoted(family):
    owner_id = user_id_for(family["owner_email"])
    assert family["owner"].delete(f"/api/household/members/{owner_id}").status_code == 409
    assert family["owner"].put(f"/api/household/members/{owner_id}", json={"role": "viewer"}).status_code == 409


def test_a_member_can_remove_themselves(family):
    member_id = user_id_for(family["member_email"])
    assert family["member"].delete(f"/api/household/members/{member_id}").status_code == 200
    assert family["member"].get("/api/household").json()["household"] is None
    # leaving costs them nothing of their own
    assert family["member"].get("/api/overview").json()["current_month"]["total"] > 0


def test_ownership_transfer_swaps_the_roles(family):
    member_id = user_id_for(family["member_email"])
    assert family["owner"].post(f"/api/household/transfer/{member_id}").status_code == 200
    assert family["member"].get("/api/household").json()["household"]["role"] == "owner"
    assert family["owner"].get("/api/household").json()["household"]["role"] == "member"


# ----------------------------------------------------------------------------- shared goals

def test_shared_goals_are_visible_and_fundable_by_any_member(family):
    r = family["owner"].post("/api/goals", json={"name": "House deposit", "target": 2000000,
                                                 "saved": 0, "target_date": "2027-12-31", "shared": True})
    assert r.status_code == 200
    goal = next(g for g in family["member"].get("/api/goals").json() if g["name"] == "House deposit")
    assert family["member"].post(f"/api/goals/{goal['id']}/contribute", json={"amount": 5000}).status_code == 200
    refreshed = next(g for g in family["owner"].get("/api/goals").json() if g["name"] == "House deposit")
    assert refreshed["saved"] == 5000


def test_a_private_goal_stays_private(family):
    family["owner"].post("/api/goals", json={"name": "Secret", "target": 1000, "saved": 0,
                                             "target_date": "2027-12-31"})
    assert "Secret" not in {g["name"] for g in family["member"].get("/api/goals").json()}


def test_shared_goal_needs_a_household(api):
    c = api()
    make_user(c)
    r = c.post("/api/goals", json={"name": "x", "target": 10, "saved": 0,
                                   "target_date": "2027-01-01", "shared": True})
    assert r.status_code == 409


def test_dissolving_the_household_keeps_everyones_data(family):
    family["owner"].post("/api/goals", json={"name": "Joint", "target": 100, "saved": 0,
                                             "target_date": "2027-01-01", "shared": True})
    assert family["owner"].delete("/api/household").json()["deleted"] is True
    assert family["owner"].get("/api/household").json()["household"] is None
    assert family["member"].get("/api/household").json()["household"] is None
    # the shared goal becomes its creator's private goal rather than vanishing
    assert "Joint" in {g["name"] for g in family["owner"].get("/api/goals").json()}
    assert family["member"].get("/api/overview").json()["current_month"]["total"] > 0
