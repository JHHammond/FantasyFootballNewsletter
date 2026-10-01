"""Shared by every test: nothing reaches Sleeper for standardized scores.
A test that wants them builds a web.ppr.Context and passes it in."""

import pytest


@pytest.fixture(autouse=True)
def _no_ppr_network(monkeypatch):
    try:
        from web import ppr
    except Exception:  # noqa: BLE001
        yield
        return
    monkeypatch.setattr(ppr, "context", lambda season, week: None)
    yield
