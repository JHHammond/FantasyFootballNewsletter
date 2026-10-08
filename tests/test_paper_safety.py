"""
The paper is served from our own domain, so nothing a league member or the AI
writes may reach it as markup (7 Oct 2026 security audit).

Every team and manager name from the platform, and every string in the AI's
content, gets an attack payload appended. The rendered paper must contain no
element or attribute the payload created: no handler attribute, no injected
<img>/<svg>/<script>. Run in the published view and the editor view.
"""

import copy
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from tests import fixtures  # noqa: E402
from tests import test_web as T  # noqa: E402

PAYLOADS = [
    '<img src=x onerror=alert(7)>',
    '"><svg onload=alert(7)>',
    "' onmouseover='alert(7)",
    '" onmouseover="alert(7)',
    '<script>alert(7)</script>',
]


class _Finder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.bad = []
        self._tag = None

    def handle_starttag(self, tag, attrs):
        self._tag = tag
        for name, value in attrs:
            if name.startswith("on") and "alert(7)" in (value or ""):
                self.bad.append(f"<{tag} {name}=...>")
        if tag in ("svg",) or (tag == "img" and dict(attrs).get("src") == "x"):
            self.bad.append(f"<{tag}>")

    def handle_data(self, data):
        if self._tag == "script" and "alert(7)" in data:
            self.bad.append("<script>")


def _poison_names(monkeypatch, payload):
    orig = fixtures.fake_get

    def evil(url, params=None):
        text = json.dumps(orig(url, params))
        text = re.sub(r'"(team_name|display_name)": "([^"]*)"',
                      lambda m: f'"{m.group(1)}": {json.dumps(m.group(2) + payload)}', text)
        return json.loads(text)

    monkeypatch.setattr(fixtures, "fake_get", evil)


def _poison_ai(ai, payload):
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, str):
                    o[k] = v + payload
                else:
                    walk(v)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                if isinstance(v, str):
                    o[i] = v + payload
                else:
                    walk(v)
    walk(ai)
    return ai


@pytest.mark.parametrize("payload", PAYLOADS)
@pytest.mark.parametrize("editable", [False, True])
def test_no_name_or_ai_text_becomes_markup(monkeypatch, payload, editable):
    _poison_names(monkeypatch, payload)
    html = T._full_paper(_poison_ai(copy.deepcopy(T.SAMPLE_AI), payload), editable=editable)
    finder = _Finder()
    finder.feed(html)
    assert finder.bad == []


def test_ordinary_names_are_not_double_escaped():
    html = T._full_paper(dict(T.SAMPLE_AI))
    assert "&amp;#x27;" not in html and "&amp;amp;" not in html
    assert "Hank&#x27;s Heroes" in html or "Hank's Heroes" in html


def test_prose_keeps_its_formatting():
    ai = copy.deepcopy(T.SAMPLE_AI)
    ai["lead_story"] = "A **bold** claim and an *aside*."
    html = T._full_paper(ai)
    assert "<strong>bold</strong>" in html and "<em>aside</em>" in html
