"""An empty trade market is never kept (28 Oct: adp() ran past the API's
time limit the morning the site passed 10,000 users; the empty build was
cached for 15 minutes and /market showed no prices to anybody)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from web import app as webapp, around, trade_rating  # noqa: E402


def test_an_empty_build_is_not_cached_and_a_good_one_survives_a_bad_one(monkeypatch):
    around._cache.clear(); around._retry_at.clear()
    monkeypatch.setattr(webapp, "_injury_status", lambda: {})
    monkeypatch.setattr(trade_rating, "apply_injuries", lambda built, inj: built)
    calls = {"n": 0}
    good = {"pickable": [{"k": "1"}]}

    def build(db, season, weeks, market="redraft"):
        calls["n"] += 1
        return {} if calls["n"] == 1 else good
    monkeypatch.setattr(trade_rating, "build", build)

    assert webapp._trade_rating(2026, 7) is None                    # the timeout: nothing to show...
    assert ("rating", 2026, 7) not in around._cache                 # ...and nothing kept as the answer
    around._retry_at.clear()                                        # the two-minute cool-off passes
    assert webapp._trade_rating(2026, 7) == good

    # a later empty build never replaces the good market
    monkeypatch.setattr(trade_rating, "build", lambda *a, **k: {})
    around._cache[("rating", 2026, 7)] = (0.0, good)                # expired
    monkeypatch.setattr(webapp, "_rebuild_soon", lambda key, refresh: refresh())
    assert webapp._trade_rating(2026, 7) == good
    assert around._cache[("rating", 2026, 7)][1] == good
    around._cache.clear(); around._retry_at.clear()
