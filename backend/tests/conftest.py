import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# every TestClient shares one address, so per-IP limits would trip across unrelated tests;
# tests that exercise them switch them back on with monkeypatch
os.environ["IP_RATE_LIMITS"] = "off"

from app import db  # noqa: E402

AS_OF = date(2026, 9, 19)


#: User 1 holds the US persona, user 2 the India persona. Each dataset plants its own edge cases, so
#: assertions are written against one or the other rather than against "the" demo data.
US_USER, IN_USER = 1, 2


@pytest.fixture(scope="session")
def seeded(tmp_path_factory):
    import dataclasses
    from app import config
    path = tmp_path_factory.mktemp("data") / "test.db"
    # dev-mode emails from tests go to a temp outbox, never the real backend/data/outbox
    # and tests never read the developer's real backend/.env (e.g. their Google keys)
    config.settings = dataclasses.replace(config.settings, outbox_dir=path.parent / "outbox",
                                          env_file=path.parent / "test.env")
    db.set_db_path(path)
    db.init_db()
    from app.synthetic import seed_demo
    seed_demo(US_USER, end=AS_OF, currency="USD")
    seed_demo(IN_USER, end=AS_OF, currency="INR")
    return path


def _user(user_id: int):
    from app import money
    from app.services import UserData
    u = UserData(user_id)
    money.set_currency(u.currency)   # narrative copy formats from this, as it does per-request in the API
    return u


@pytest.fixture()
def user(seeded):
    return _user(US_USER)


@pytest.fixture()
def user_in(seeded):
    return _user(IN_USER)
