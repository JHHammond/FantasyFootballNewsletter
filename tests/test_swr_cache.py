"""The homepage cache under load (5 Oct: dozens of statement timeouts a
minute, in bursts as the ten-minute cache expired).
Run with: python -m pytest tests/test_swr_cache.py -q
"""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web import around  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    around._locks.clear()
    around._retry_at.clear()
    yield


def test_a_failed_refresh_keeps_the_last_good_value_and_backs_off():
    store = {"k": (time.time() - 10_000, "old")}      # expired
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("canceling statement due to statement timeout")

    assert around.swr(store, "k", boom, ttl=60) == "old"
    # Within the back-off, nobody else hits the database.
    assert around.swr(store, "k", boom, ttl=60) == "old"
    assert around.swr(store, "k", boom, ttl=60) == "old"
    assert len(calls) == 1


def test_with_nothing_stored_a_failure_raises_then_waits():
    store, calls = {}, []

    def boom():
        calls.append(1)
        raise RuntimeError("timeout")

    with pytest.raises(RuntimeError):
        around.swr(store, "k", boom)
    with pytest.raises(RuntimeError):
        around.swr(store, "k", boom)
    assert len(calls) == 1


def test_only_one_request_refreshes_an_expired_entry():
    store = {"k": (time.time() - 10_000, "old")}
    started, release, calls = threading.Event(), threading.Event(), []

    def slow():
        calls.append(1)
        started.set()
        release.wait(5)
        return "new"

    out = {}
    t = threading.Thread(target=lambda: out.setdefault("first", around.swr(store, "k", slow, ttl=60)))
    t.start()
    started.wait(5)
    # While the first refresh is running, everyone else gets the old value at once.
    assert [around.swr(store, "k", slow, ttl=60) for _ in range(20)] == ["old"] * 20
    release.set()
    t.join(5)
    assert out["first"] == "new" and len(calls) == 1
    assert around.swr(store, "k", slow, ttl=60) == "new"


def test_a_fresh_value_is_served_without_calling():
    store = {"k": (time.time(), "v")}
    assert around.swr(store, "k", lambda: pytest.fail("should not run"), ttl=60) == "v"
