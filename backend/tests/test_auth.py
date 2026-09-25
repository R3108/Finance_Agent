import pytest
from fastapi.testclient import TestClient

from app import auth


@pytest.fixture()
def client(seeded):
    from app.main import app
    auth._failures.clear()
    return TestClient(app)


def signup(client, email, password="correct horse battery", **kw):
    return client.post("/api/auth/signup", json={"name": "Sam", "email": email, "password": password, **kw})


def test_password_hashing():
    h = auth.hash_password("s3cret-pass", iterations=1000)
    assert h.startswith("pbkdf2_sha256$1000$") and "s3cret-pass" not in h
    assert auth.verify_password("s3cret-pass", h)
    assert not auth.verify_password("wrong", h)
    assert not auth.verify_password("s3cret-pass", None)
    assert auth.hash_password("x" * 8, 1000) != auth.hash_password("x" * 8, 1000)  # per-user salt


def test_endpoints_require_a_session(client):
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/me", headers={"X-User-Id": "1"}).status_code == 401  # the old header no longer works
    assert client.get("/api/me", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_signup_login_logout(client):
    r = signup(client, "  New.User@Example.com ")
    assert r.status_code == 201
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    me = client.get("/api/me").json()
    assert me["email"] == "new.user@example.com" and me["plan"] == "free" and me["transactions"] == 0
    assert signup(client, "new.user@example.com").status_code == 409
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/auth/login", json={"email": "NEW.USER@example.com", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "new.user@example.com", "password": "correct horse battery"}).status_code == 200
    assert client.get("/api/me").json()["email"] == "new.user@example.com"


def test_signup_validation(client):
    assert signup(client, "not-an-email").status_code == 422
    assert signup(client, "short@example.com", password="1234567").status_code == 422


def test_users_are_isolated(client):
    signup(client, "alice@example.com", sample_data=True)
    alice_tx = client.get("/api/transactions?limit=1").json()["transactions"][0]["id"]
    other = TestClient(client.app)
    signup(other, "bob@example.com")
    assert other.get("/api/transactions").json()["total_count"] == 0
    assert other.patch(f"/api/transactions/{alice_tx}", json={"category": "Dining"}).status_code == 404


def test_demo_sandboxes_are_private(client):
    a, b = TestClient(client.app), TestClient(client.app)
    assert a.post("/api/auth/demo").status_code == 201 and b.post("/api/auth/demo").status_code == 201
    me_a, me_b = a.get("/api/me").json(), b.get("/api/me").json()
    assert me_a["id"] != me_b["id"] and me_a["is_demo"] and me_a["transactions"] > 1000
    a.put("/api/budgets", json={"category": "Travel", "limit": 123})
    assert "Travel" not in {i["category"] for i in b.get("/api/budgets").json()["items"]}


def test_login_rate_limit(client):
    signup(client, "locked@example.com")
    for _ in range(auth.MAX_FAILURES):
        assert client.post("/api/auth/login", json={"email": "locked@example.com", "password": "bad"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "locked@example.com", "password": "correct horse battery"})
    assert r.status_code == 429


def test_expired_sessions_are_rejected(client):
    from app.db import get_conn
    token = auth.create_session(1)
    with get_conn() as conn:
        conn.execute("UPDATE sessions SET expires_at = '2000-01-01 00:00:00'")
    assert auth.user_for_token(token) is None
