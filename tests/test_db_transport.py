"""The Supabase connection survives a connection the server already closed.

Production traceback this is written against: httpcore.RemoteProtocolError:
Server disconnected, from db.save_manager, on the lore page.
"""
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web import db  # noqa: E402


@pytest.fixture
def flaky(monkeypatch):
    calls = {"n": 0}

    def parent(self, request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected")
        return httpx.Response(200, json=[], request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", parent)
    return calls


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
def test_a_safe_request_is_retried_once_on_a_dead_connection(flaky, method):
    client = httpx.Client(transport=db._RetryStaleConnection())
    r = client.request(method, "https://x.supabase.co/rest/v1/managers")
    assert r.status_code == 200
    assert flaky["n"] == 2


def test_an_insert_is_never_sent_twice(flaky):
    client = httpx.Client(transport=db._RetryStaleConnection())
    with pytest.raises(httpx.RemoteProtocolError):
        client.post("https://x.supabase.co/rest/v1/managers", json={})
    assert flaky["n"] == 1


def test_a_second_failure_is_not_swallowed(monkeypatch):
    def always(self, request):
        raise httpx.RemoteProtocolError("Server disconnected")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", always)
    client = httpx.Client(transport=db._RetryStaleConnection())
    with pytest.raises(httpx.RemoteProtocolError):
        client.get("https://x.supabase.co/rest/v1/managers")


def test_the_supabase_client_actually_uses_this_transport(monkeypatch):
    """Without this, the retry could be perfect and never run."""
    seen = []

    def record(self, request):
        seen.append((type(self).__name__, request.method, str(request.url)))
        return httpx.Response(200, json=[], request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", record)
    c = db._create("https://abc.supabase.co", "sb_secret_" + "x" * 30)
    c.table("managers").update({"notes": "n"}).eq("handle", "h").execute()
    assert seen and seen[0][0] == "_RetryStaleConnection"
    assert seen[0][1] == "PATCH"
    assert seen[0][2].startswith("https://abc.supabase.co/rest/v1/managers")


def test_http2_is_off():
    """HTTP/2's single shared connection is what went stale."""
    pool = db._http_client()._transport._pool
    assert pool._http2 is False
