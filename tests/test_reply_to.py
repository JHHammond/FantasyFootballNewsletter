"""Replies have to land somewhere a human reads."""
from web import emailer


class _Resp:
    status_code = 200
    text = "{}"


def _capture(monkeypatch):
    sent = {}
    def post(url, json=None, headers=None, timeout=None):
        sent.update(json)
        return _Resp()
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setattr(emailer.requests, "post", post)
    return sent


def test_replies_go_to_the_contact_inbox_by_default(monkeypatch):
    monkeypatch.delenv("REPLY_TO", raising=False)
    sent = _capture(monkeypatch)
    emailer._send("a@b.com", "Hi", "<p>hi</p>")
    assert sent["reply_to"] == "commissionersdesk@gmail.com"


def test_reply_to_can_be_overridden_or_turned_off(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setenv("REPLY_TO", "john@commissionersdesk.com")
    emailer._send("a@b.com", "Hi", "<p>hi</p>")
    assert sent["reply_to"] == "john@commissionersdesk.com"
    sent.clear()
    monkeypatch.setenv("REPLY_TO", "none")
    emailer._send("a@b.com", "Hi", "<p>hi</p>")
    assert "reply_to" not in sent
