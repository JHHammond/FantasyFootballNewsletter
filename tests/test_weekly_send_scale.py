"""The Tuesday job's league lookup at real scale (3 Nov 2026).

Week 8's run crashed with "Bad Request": every paying owner's id went into
one `user_id=in.(...)` filter in the URL, and enough owners made it too long.
It also read each query in one request, and the API stops at 1,000 rows.
This fake API enforces both limits.
"""
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")

from web import db  # noqa: E402

MAX_IN = 100        # longer filters 400, like the gateway
MAX_ROWS = 1000     # the API's per-request cap


class Res:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, rows, log):
        self.rows, self.log, self.filters, self.rng = rows, log, [], None
        self.not_ = self

    def select(self, _cols):
        return self

    def in_(self, col, values):
        if len(values) > MAX_IN:
            raise RuntimeError("Bad Request")       # what the gateway did
        self.filters.append(lambda r: r.get(col) in set(values))
        return self

    def is_(self, col, _null):
        negate = self is self.not_ and getattr(self, "_negate_next", False)
        self.filters.append(lambda r: r.get(col) is None)
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def execute(self):
        rows = [r for r in self.rows if all(f(r) for f in self.filters)]
        a, b = self.rng or (0, MAX_ROWS - 1)
        b = min(b, a + MAX_ROWS - 1)
        self.log.append(len(rows))
        return Res(rows[a:b + 1])


class Client:
    def __init__(self, tables):
        self.tables, self.log = tables, []

    def table(self, name):
        q = Query(self.tables[name], self.log)
        if name == "leagues":
            # .is_("user_id","null").not_.is_("owner_email","null"): handle
            # the pair as "no user, has an email".
            def is_(col, _null, q=q):
                if col == "user_id":
                    q.filters.append(lambda r: r.get("user_id") is None)
                else:
                    q.filters.append(lambda r: r.get("owner_email") is not None)
                return q
            q.is_ = is_
        return q


def test_hundreds_of_paying_owners_and_thousands_of_leagues(monkeypatch):
    owners = [{"id": str(uuid.uuid4()), "email": f"o{i}@x.com", "plan": "paid",
               "plan_status": "active"} for i in range(350)]
    leagues = []
    for i in range(2500):
        o = owners[i % len(owners)]
        leagues.append({"id": str(uuid.uuid4()), "user_id": o["id"], "owner_email": None,
                        "season": 2026, "provider": "sleeper",
                        "platform_league_id": str(i), "league_name": f"L{i}"})
    # 30 leagues never linked to an account, found by the owner's email
    for i in range(30):
        leagues.append({"id": str(uuid.uuid4()), "user_id": None,
                        "owner_email": owners[i]["email"], "season": 2026,
                        "provider": "sleeper", "platform_league_id": f"u{i}",
                        "league_name": f"U{i}"})
    fake = Client({"users": owners, "leagues": leagues})
    monkeypatch.setattr(db, "client", lambda: fake)

    out = db.leagues_for_weekly_send()
    assert len(out) == 2530, "every league, none dropped at 1,000 rows"
    assert {l["platform_league_id"] for l in out} >= {"0", "2499", "u0", "u29"}
