"""Proactive watchers: dedupe, plan limits, digest and the sweep."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app import automations, money, plans
from app.db import get_conn
from app.services import UserData
from tests.conftest import IN_USER


@pytest.fixture()
def watched(user_in):
    """The India persona with a fresh set of alert rules and an empty inbox."""
    with get_conn() as conn:
        conn.execute("DELETE FROM notifications WHERE user_id = ?", (IN_USER,))
        conn.execute("DELETE FROM alert_rules WHERE user_id = ?", (IN_USER,))
        conn.execute("DELETE FROM automation_state WHERE user_id = ?", (IN_USER,))
        conn.execute("UPDATE users SET plan = 'pro' WHERE id = ?", (IN_USER,))
    automations.ensure_default_rules(IN_USER)
    money.set_currency("INR")
    return UserData(IN_USER)


def test_starter_rules_are_installed_once(watched):
    with get_conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM alert_rules WHERE user_id = ?", (IN_USER,)).fetchone()[0]
    assert before > 0
    assert automations.ensure_default_rules(IN_USER) == 0, "a second call must not duplicate them"


def test_starter_rules_respect_the_plan_limit():
    """A free account is never seeded past the quota it would then be blocked from adding to."""
    with get_conn() as conn:
        conn.execute("DELETE FROM alert_rules WHERE user_id = ?", (IN_USER,))
        conn.execute("UPDATE users SET plan = 'free' WHERE id = ?", (IN_USER,))
    created = automations.ensure_default_rules(IN_USER)
    assert created == plans.limit_for("free", "alert_rules")
    with get_conn() as conn:
        conn.execute("UPDATE users SET plan = 'pro' WHERE id = ?", (IN_USER,))


def test_sweep_finds_the_planted_problems(watched):
    result = automations.run_user(IN_USER, force=True)
    assert result["created"] > 0
    titles = " ".join(i["title"] for i in result["items"])
    assert "Netflix" in titles and "Spotify" in titles


def test_sweep_is_idempotent(watched):
    """The watchers run every few hours; re-detecting a condition must not re-notify."""
    first = automations.run_user(IN_USER, force=True)
    second = automations.run_user(IN_USER, force=True)
    assert first["created"] > 0
    assert second["created"] == 0


def test_sweep_respects_its_interval(watched):
    automations.run_user(IN_USER, force=True)
    assert automations.run_user(IN_USER)["skipped"] == "not due"
    later = automations._now() + automations.SWEEP_INTERVAL + timedelta(minutes=1)
    assert "skipped" not in automations.run_user(IN_USER, now=later)


def test_alerts_are_written_in_the_users_currency(watched):
    result = automations.run_user(IN_USER, force=True)
    text = " ".join(i["body"] for i in result["items"])
    assert "₹" in text and "$" not in text


def test_a_broken_rule_does_not_silence_the_others(watched, monkeypatch):
    """One watcher raising must not cost the user every other alert in that sweep."""
    def boom(u, p):
        raise RuntimeError("bad data")

    broken = automations.RULE_TYPES["price_increase"]
    monkeypatch.setitem(automations.RULE_TYPES, "price_increase",
                        type(broken)(**{**broken.__dict__, "evaluate": boom}))
    result = automations.run_user(IN_USER, force=True)
    assert result["created"] > 0


def test_demo_sandboxes_are_not_swept():
    """Throwaway demo accounts would generate a burst of alerts nobody asked for."""
    from app.synthetic import create_demo_user
    demo_id = create_demo_user("INR")
    automations.ensure_default_rules(demo_id)
    assert demo_id not in automations.active_user_ids(automations._now())


# ----------------------------------------------------------------------------- rule parameters

def test_validate_params_fills_defaults_and_bounds():
    assert automations.validate_params("bill_due", {}) == {"days": 3}
    assert automations.validate_params("bill_due", {"days": 9999}) == {"days": 180}
    assert automations.validate_params("large_transaction", {"amount": "250.5", "days": 3}) == {
        "amount": 250.5, "days": 3}


@pytest.mark.parametrize("kind, params", [
    ("nope", {}),
    ("category_over", {"category": "Not a category", "limit": 10}),
    ("category_over", {"category": "Dining", "limit": -5}),
    ("large_transaction", {"amount": "abc"}),
])
def test_validate_params_rejects_bad_input(kind, params):
    with pytest.raises(automations.RuleError):
        automations.validate_params(kind, params)


def test_category_rule_fires_only_above_its_limit(watched):
    u = UserData(IN_USER)
    spec = automations.RULE_TYPES["category_over"]
    assert spec.evaluate(u, {"category": "Dining", "limit": 10_000_000}) == []
    fired = spec.evaluate(u, {"category": "Dining", "limit": 1})
    assert len(fired) == 1 and "Dining" in fired[0].title


# ----------------------------------------------------------------------------- digest

def test_digest_is_built_from_deterministic_analytics(watched):
    d = automations.build_digest(watched, "weekly")
    assert d["days"] == 7 and d["currency"] == "INR"
    assert d["spent"] >= 0 and 0 <= d["health_score"] <= 100
    assert d["text"]["spent"].startswith("₹")


def test_digest_narration_is_dropped_when_it_invents_a_number(watched, monkeypatch):
    """The model may only phrase the digest's own figures; anything else is not shown at all."""
    from app import agent
    digest = automations.build_digest(watched)
    monkeypatch.setattr(agent, "check_grounding", lambda answer, sources: ["₹9,99,999.00"])
    monkeypatch.setattr("app.config.settings.__class__.llm_enabled", property(lambda self: True))
    assert automations.narrate(watched, digest) is None


def test_digest_period_is_validated(watched):
    automations.set_digest_period(IN_USER, "monthly")
    with get_conn() as conn:
        row = conn.execute("SELECT digest_period FROM automation_state WHERE user_id = ?", (IN_USER,)).fetchone()
    assert row["digest_period"] == "monthly"
    with pytest.raises(automations.RuleError):
        automations.set_digest_period(IN_USER, "hourly")


# ----------------------------------------------------------------------------- inbox

def test_inbox_unread_count_and_mark_read(watched):
    automations.run_user(IN_USER, force=True)
    inbox = automations.list_notifications(IN_USER)
    assert inbox["unread"] == len(inbox["items"]) > 0
    one = inbox["items"][0]["id"]
    assert automations.mark_read(IN_USER, one) == 1
    assert automations.list_notifications(IN_USER)["unread"] == inbox["unread"] - 1
    automations.mark_read(IN_USER)
    assert automations.list_notifications(IN_USER)["unread"] == 0


# ----------------------------------------------------------------------------- HTTP surface

@pytest.fixture()
def client(seeded, monkeypatch):
    """A signed-in free account on a rupee ledger. The email is unique per test, because the
    database is shared for the whole session and a repeated address would 409 on signup."""
    import uuid

    from fastapi.testclient import TestClient

    from app import auth, mailer
    from app.main import app
    monkeypatch.setattr(mailer, "send", lambda *a, **k: None)
    auth._failures.clear()
    c = TestClient(app)
    c.signup_email = f"automations-{uuid.uuid4().hex[:8]}@example.com"
    c.post("/api/auth/signup", json={"name": "Riya", "email": c.signup_email,
                                     "password": "correct horse battery", "sample_data": True,
                                     "currency": "INR"})
    return c


def _go_pro(client) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET plan = 'pro' WHERE email = ?", (client.signup_email,))


def test_digest_route_is_not_swallowed_by_the_rule_id_route(client):
    """`/automations/digest` must not be parsed as `/automations/{rule_id}` — declaration order matters."""
    assert client.put("/api/automations/digest", json={"period": "off"}).status_code == 200
    assert client.put("/api/automations/digest", json={"period": "nope"}).status_code == 422


def test_digest_is_a_paid_feature(client):
    assert client.put("/api/automations/digest", json={"period": "weekly"}).status_code == 402
    assert client.get("/api/automations/digest/preview").status_code == 402
    _go_pro(client)
    assert client.put("/api/automations/digest", json={"period": "weekly"}).status_code == 200
    assert client.get("/api/automations/digest/preview").status_code == 200


def test_free_plan_rule_quota_is_enforced(client):
    listed = client.get("/api/automations").json()
    assert len(listed["rules"]) == listed["limits"]["rules"]
    over = client.post("/api/automations", json={"kind": "bill_due", "params": {}})
    assert over.status_code == 402 and over.json()["feature"] == "unlimited_alerts"


def test_bad_rule_input_is_422_not_402(client):
    """Validation must be reported as validation, even though the quota check runs nearby."""
    listed = client.get("/api/automations").json()
    client.delete(f"/api/automations/{listed['rules'][0]['id']}")     # free a slot
    assert client.post("/api/automations", json={"kind": "nope", "params": {}}).status_code == 422
    assert client.post("/api/automations", json={
        "kind": "category_over", "params": {"category": "Not real", "limit": 5}}).status_code == 422
    assert client.post("/api/automations", json={
        "kind": "category_over", "params": {"category": "Dining", "limit": 8000}}).status_code == 201


def test_email_delivery_needs_pro(client):
    listed = client.get("/api/automations").json()
    rule = listed["rules"][0]
    body = {"kind": rule["kind"], "params": rule["params"], "channels": ["inapp", "email"], "active": True}
    assert client.put(f"/api/automations/{rule['id']}", json=body).status_code == 402
    _go_pro(client)
    assert client.put(f"/api/automations/{rule['id']}", json=body).status_code == 200


def test_unknown_rule_id_is_404(client):
    _go_pro(client)
    assert client.put("/api/automations/999999", json={
        "kind": "bill_due", "params": {}, "channels": ["inapp"], "active": True}).status_code == 404
