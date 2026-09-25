"""Trials, coupons, referrals, operator metrics and the privacy endpoints."""
from __future__ import annotations

import dataclasses
import re
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import admin, growth, privacy
from app.db import get_conn

PW = "correct horse battery"


@pytest.fixture()
def sent(monkeypatch):
    out: list[dict] = []
    from app import mailer
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: out.append(
        {"to": to, "subject": subject, "text": text}))
    return out


@pytest.fixture()
def api(seeded, sent, tmp_path, monkeypatch):
    from app import auth, config
    from app.main import app
    monkeypatch.setattr(config, "settings",
                        dataclasses.replace(config.settings, receipts_dir=tmp_path / "receipts"))
    auth._failures.clear()
    return lambda: TestClient(app)


def signup(client, *, ref: str | None = None, sample: bool = False) -> str:
    email = f"g-{uuid.uuid4().hex[:8]}@example.com"
    body = {"name": "Sam", "email": email, "password": PW, "sample_data": sample, "currency": "INR"}
    if ref:
        body["ref"] = ref
    assert client.post("/api/auth/signup", json=body).status_code == 201
    return email


def user_id_for(email: str) -> int:
    with get_conn() as conn:
        return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]


def verify(client, sent, email: str) -> dict:
    mail = next(m for m in reversed(sent) if m["to"] == email and "Confirm" in m["subject"])
    token = re.search(r"verify-email\?token=([\w-]+)", mail["text"]).group(1)
    return client.post("/api/auth/verify", json={"token": token}).json()


# ----------------------------------------------------------------------------- trials

def test_trial_grants_pro_and_only_once(api):
    c = api()
    signup(c)
    assert c.get("/api/me").json()["plan"] == "free"

    assert c.post("/api/rewards/trial").status_code == 201
    assert c.get("/api/me").json()["plan"] == "pro"
    assert c.post("/api/rewards/trial").status_code == 409, "a second trial must be refused"


def test_trial_status_reports_days_left(api):
    c = api()
    signup(c)
    assert c.get("/api/rewards").json()["trial"]["available"] is True
    c.post("/api/rewards/trial")
    trial = c.get("/api/rewards").json()["trial"]
    assert trial["available"] is False and trial["active"] is True
    assert trial["days_left"] in (growth.TRIAL_DAYS - 1, growth.TRIAL_DAYS)


def test_trial_expiry_drops_back_to_free(api):
    c = api()
    email = signup(c)
    c.post("/api/rewards/trial")
    with get_conn() as conn:   # wind the granted pass into the past
        conn.execute("UPDATE billing_passes SET starts_at = starts_at - 2592000, ends_at = ends_at - 2592000 "
                     "WHERE user_id = ?", (user_id_for(email),))
    assert c.get("/api/me").json()["plan"] == "free"


def test_demo_sandboxes_cannot_start_a_trial(api):
    c = api()
    c.post("/api/auth/demo?currency=INR")
    assert c.post("/api/rewards/trial").status_code == 409


# ----------------------------------------------------------------------------- granted time stacks

def test_granted_time_stacks_rather_than_overwrites(api):
    c = api()
    email = signup(c)
    uid = user_id_for(email)
    first = growth.grant_days(uid, 10, "pro", "grant")
    second = growth.grant_days(uid, 5, "pro", "grant")
    assert second["starts_at"] == first["ends_at"], "the second grant must begin where the first ends"
    assert growth.granted_access(uid)["days_left"] >= 9


def test_a_better_tier_starts_immediately_rather_than_queueing(api):
    """Granting Family mid-Pro-trial must upgrade now, not in two weeks' time."""
    c = api()
    email = signup(c)
    uid = user_id_for(email)
    growth.grant_days(uid, 30, "pro", "trial")
    family = growth.grant_days(uid, 365, "family", "grant")
    assert family["starts_at"] <= growth._now() + 1
    assert c.get("/api/me").json()["plan"] == "family"


def test_a_lesser_tier_still_queues_so_its_days_are_not_wasted(api):
    c = api()
    email = signup(c)
    uid = user_id_for(email)
    family = growth.grant_days(uid, 30, "family", "grant")
    pro = growth.grant_days(uid, 30, "pro", "coupon:X")
    assert pro["starts_at"] == family["ends_at"]
    assert c.get("/api/me").json()["plan"] == "family"


def test_grant_rejects_nonsense():
    with pytest.raises(growth.GrowthError):
        growth.grant_days(1, 0)
    with pytest.raises(growth.GrowthError):
        growth.grant_days(1, 10, tier="free")


# ----------------------------------------------------------------------------- coupons

@pytest.fixture()
def coupon():
    code = f"TEST{uuid.uuid4().hex[:6].upper()}"
    growth.create_coupon(code, days=30, tier="pro", max_redemptions=2)
    return code


def test_coupon_grants_access(api, coupon):
    c = api()
    signup(c)
    assert c.post("/api/rewards/coupon", json={"code": coupon.lower()}).status_code == 200
    assert c.get("/api/me").json()["plan"] == "pro"


def test_coupon_cannot_be_used_twice_by_one_person(api, coupon):
    c = api()
    signup(c)
    c.post("/api/rewards/coupon", json={"code": coupon})
    again = c.post("/api/rewards/coupon", json={"code": coupon})
    assert again.status_code == 409 and "already used" in again.json()["detail"]


def test_coupon_redemption_cap_is_enforced(api, coupon):
    for _ in range(2):
        c = api()
        signup(c)
        assert c.post("/api/rewards/coupon", json={"code": coupon}).status_code == 200
    third = api()
    signup(third)
    r = third.post("/api/rewards/coupon", json={"code": coupon})
    assert r.status_code == 409 and "fully used" in r.json()["detail"]


def test_expired_and_unknown_coupons_are_refused(api):
    code = f"OLD{uuid.uuid4().hex[:6].upper()}"
    growth.create_coupon(code, days=30, expires_at=date.today() - timedelta(days=1))
    c = api()
    signup(c)
    assert "expired" in c.post("/api/rewards/coupon", json={"code": code}).json()["detail"]
    assert c.post("/api/rewards/coupon", json={"code": "NOPE123"}).status_code == 409


def test_duplicate_coupon_codes_are_refused(coupon):
    with pytest.raises(growth.GrowthError):
        growth.create_coupon(coupon, days=5)


@pytest.mark.parametrize("code", ["ab", "has space", "x" * 40])
def test_invalid_coupon_codes_are_refused(code):
    with pytest.raises(growth.GrowthError):
        growth.create_coupon(code, days=5)


# ----------------------------------------------------------------------------- referrals

def test_referral_pays_both_sides_only_after_verification(api, sent):
    referrer = api()
    referrer_email = signup(referrer)
    code = referrer.get("/api/rewards").json()["referral"]["code"]

    referee = api()
    referee_email = signup(referee, ref=code)
    # attributed, but nothing paid yet
    assert referee.get("/api/me").json()["plan"] == "free"
    assert referrer.get("/api/rewards").json()["referral"]["pending"] == 1

    result = verify(referee, sent, referee_email)
    assert result["referral_reward_days"] == growth.REFERRAL_DAYS
    assert referee.get("/api/me").json()["plan"] == "pro"
    assert referrer.get("/api/me").json()["plan"] == "pro"

    stats = referrer.get("/api/rewards").json()["referral"]
    assert stats["rewarded"] == 1 and stats["days_earned"] == growth.REFERRAL_DAYS
    assert user_id_for(referrer_email) != user_id_for(referee_email)


def test_referral_is_paid_once_even_if_completion_runs_again(api, sent):
    referrer = api()
    signup(referrer)
    code = referrer.get("/api/rewards").json()["referral"]["code"]
    referee = api()
    referee_email = signup(referee, ref=code)
    verify(referee, sent, referee_email)

    before = growth.granted_access(user_id_for(referee_email))["ends_at"]
    assert growth.complete_referral(user_id_for(referee_email)) is None
    assert growth.granted_access(user_id_for(referee_email))["ends_at"] == before


def test_self_referral_is_ignored(api):
    c = api()
    signup(c)
    code = c.get("/api/rewards").json()["referral"]["code"]
    assert growth.attribute_referral(user_id_for_code(code), code) is False


def user_id_for_code(code: str) -> int:
    with get_conn() as conn:
        return conn.execute("SELECT id FROM users WHERE referral_code = ?", (code,)).fetchone()["id"]


def test_unknown_referral_code_is_ignored(api):
    c = api()
    email = signup(c, ref="NOTACODE")
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM referrals WHERE referee_id = ?",
                            (user_id_for(email),)).fetchone() is None


def test_referral_code_is_stable(api):
    c = api()
    signup(c)
    first = c.get("/api/rewards").json()["referral"]["code"]
    assert c.get("/api/rewards").json()["referral"]["code"] == first
    assert first in c.get("/api/rewards").json()["referral"]["link"]


# ----------------------------------------------------------------------------- admin

@pytest.fixture()
def as_admin(api, monkeypatch):
    """A signed-in account whose email is in the ADMIN_EMAILS allow-list."""
    c = api()
    email = signup(c, sample=True)
    monkeypatch.setattr(admin, "admin_emails", lambda: {email})
    return c


def test_me_reports_admin_status(api, as_admin):
    """The sidebar shows the Operations link from this flag; the routes re-check server-side."""
    assert as_admin.get("/api/me").json()["is_admin"] is True
    ordinary = api()
    signup(ordinary)
    assert ordinary.get("/api/me").json()["is_admin"] is False


def test_metrics_need_an_allow_listed_email(api):
    c = api()
    signup(c)
    assert c.get("/api/admin/metrics").status_code == 403


def test_no_admins_configured_means_no_access(api, monkeypatch):
    monkeypatch.setattr(admin, "admin_emails", lambda: set())
    c = api()
    signup(c)
    assert c.get("/api/admin/metrics").status_code == 403


def test_metrics_dashboard_reports_the_business(as_admin):
    m = as_admin.get("/api/admin/metrics").json()
    assert m["users"]["total"] > 0
    assert {"mrr_total", "arr_projected", "collected_lifetime"} <= set(m["revenue"])
    assert [s["step"] for s in m["activation"]["steps"]][0] == "signed_up"
    assert m["activation"]["steps"][0]["share_pct"] == 100.0
    assert "trials_started" in m["growth"] and "churn_pct" in m["churn"]
    assert isinstance(m["plan_mix"], list) and isinstance(m["retention"], list)


def test_activation_funnel_never_grows_at_the_first_step(as_admin):
    steps = as_admin.get("/api/admin/metrics").json()["activation"]["steps"]
    assert steps[0]["users"] >= steps[1]["users"]


def test_admin_can_mint_and_revoke_coupons(as_admin):
    code = f"LAUNCH{uuid.uuid4().hex[:5].upper()}"
    created = as_admin.post("/api/admin/coupons", json={"code": code, "days": 60, "tier": "pro"})
    assert created.status_code == 201 and created.json()["days"] == 60
    assert code in {c["code"] for c in as_admin.get("/api/admin/coupons").json()}
    assert as_admin.delete(f"/api/admin/coupons/{code}").status_code == 200
    assert as_admin.delete(f"/api/admin/coupons/{code}").status_code == 404


def test_free_time_is_counted_separately_from_purchases(as_admin):
    """Granting a trial must move the 'on free time' count and leave revenue untouched.

    Measured as a delta rather than an absolute: the metrics are global, and other tests in this
    suite create real subscriptions in the same database.
    """
    before = as_admin.get("/api/admin/metrics").json()
    free_before = next(m for m in before["plan_mix"] if m["plan"] == "(on free time)")["users"]

    as_admin.post("/api/rewards/trial")

    after = as_admin.get("/api/admin/metrics").json()
    free_after = next(m for m in after["plan_mix"] if m["plan"] == "(on free time)")["users"]
    assert free_after == free_before + 1
    assert after["revenue"]["mrr_subscriptions"] == before["revenue"]["mrr_subscriptions"]
    assert after["revenue"]["collected_lifetime"] == before["revenue"]["collected_lifetime"]


# ----------------------------------------------------------------------------- privacy

def test_export_contains_the_users_data_and_no_secrets(api):
    c = api()
    signup(c, sample=True)
    res = c.get("/api/privacy/export.json")
    assert res.status_code == 200
    payload = res.json()
    assert payload["data"]["users"] and payload["data"]["transactions"]
    assert "password_hash" not in payload["data"]["users"][0]
    body = res.text
    assert "token_hash" not in body and "pbkdf2" not in body


def test_export_walks_the_live_schema(api):
    """A table added later must appear automatically rather than needing a list update."""
    tables = {t for t, _ in privacy._user_tables()}
    assert {"users", "transactions", "accounts", "alert_rules", "tax_tags", "receipts"} <= tables
    assert "billing_events" not in tables, "operator tables aren't the user's data"


def test_delete_needs_explicit_confirmation(api):
    c = api()
    signup(c)
    assert c.post("/api/privacy/delete", json={"confirm": "yes"}).status_code == 422
    assert c.get("/api/me").status_code == 200


def test_delete_removes_everything(api, tmp_path):
    c = api()
    email = signup(c, sample=True)
    uid = user_id_for(email)
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM transactions WHERE user_id = ?", (uid,)).fetchone()[0] > 0

    result = c.post("/api/privacy/delete", json={"confirm": "DELETE"}).json()
    assert result["deleted"] is True
    assert result["cleaned_without_cascade"] == {}, "every user table should cascade on its own"

    with get_conn() as conn:
        for table in ("users", "transactions", "accounts", "alert_rules", "sessions"):
            column = "id" if table == "users" else "user_id"
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (uid,)).fetchone()[0] == 0


def test_deleting_leaves_other_accounts_alone(api):
    keeper = api()
    keeper_email = signup(keeper, sample=True)
    goer = api()
    signup(goer, sample=True)
    goer.post("/api/privacy/delete", json={"confirm": "DELETE"})
    assert keeper.get("/api/me").json()["email"] == keeper_email
    assert keeper.get("/api/overview").json()["current_month"]["total"] > 0


def test_household_owner_cannot_delete_while_others_depend_on_them(api, sent):
    owner = api()
    owner_email = signup(owner)
    with get_conn() as conn:
        conn.execute("UPDATE users SET plan = 'family', email_verified_at = datetime('now') WHERE email = ?",
                     (owner_email,))
    owner.post("/api/household", json={"name": "Shared"})

    member = api()
    member_email = signup(member)
    with get_conn() as conn:
        conn.execute("UPDATE users SET email_verified_at = datetime('now') WHERE email = ?", (member_email,))
    owner.post("/api/household/invites", json={"email": member_email})
    token = re.search(r"join\?token=([\w-]+)",
                      next(m for m in reversed(sent) if m["to"] == member_email)["text"]).group(1)
    member.post(f"/api/household/invites/accept?token={token}")

    blocked = owner.post("/api/privacy/delete", json={"confirm": "DELETE"})
    assert blocked.status_code == 409 and "household" in blocked.json()["detail"]


# ----------------------------------------------------------------------------- analytics events

def test_events_are_recorded_for_key_actions(api):
    c = api()
    email = signup(c)
    c.post("/api/rewards/trial")
    with get_conn() as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM analytics_events WHERE user_id = ?", (user_id_for(email),))}
    assert {"signed_up", "trial_started"} <= names


def test_tracking_never_breaks_a_request(monkeypatch):
    """Analytics is best-effort: a failure there must not surface to the user."""
    import app.growth as g
    monkeypatch.setattr(g, "get_conn", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    g.track(1, "anything")   # must not raise
