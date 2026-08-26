"""
HTML sanitizer for prose the commissioner edits.

WHY THIS EXISTS

Inline editing saves `innerHTML` straight into ai_cache, and the renderer drops
that back into a page served from our own domain at a public URL. Without this,
a commissioner could paste a <script> tag into a story and it would run in the
browser of every single person who opens that week's paper. While it's just one
person editing their own league that's merely bad; the moment strangers use the
product it's a real vulnerability.

APPROACH

Strict allowlist, stdlib only. Anything not explicitly permitted has its tag
stripped while its text is kept, so an edit is never silently emptied — a
commissioner who pastes formatted text from a document gets their words,
minus the markup we won't render.

Deliberately NOT a blocklist. Blocklists lose: there is always another way to
smuggle script in (svg handlers, data: URLs, mutation XSS via nested tags), and
being one entry behind an attacker is the normal state of a blocklist.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser

#: Tags a paper actually renders. Everything else has its tag dropped.
ALLOWED_TAGS = frozenset({
    "p", "br", "strong", "b", "em", "i", "u", "s", "span",
    "ul", "ol", "li", "blockquote", "h3", "h4",
    "a",
})

#: Attributes kept, per tag. Nothing global: no class, no id, and above all no
#: style (which carries expression()/url() tricks) and no on* handlers.
ALLOWED_ATTRS = {
    "a": frozenset({"href", "title"}),
}

#: Tags whose *content* is dropped too. Keeping the text of a <script> would
#: leave raw JS sitting in the page as visible garbage.
DROP_CONTENT = frozenset({
    "script", "style", "iframe", "object", "embed", "template", "noscript",
    "svg", "math", "form", "input", "button", "textarea", "select",
})

VOID_TAGS = frozenset({"br", "img", "hr"})

SAFE_URL_SCHEMES = ("http://", "https://", "mailto:")


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open_tags: list[str] = []
        self.suppress_depth = 0

    # -- helpers -----------------------------------------------------------

    def _safe_href(self, value: str) -> str | None:
        candidate = (value or "").strip()
        # Strip control characters, which are how "java\tscript:" gets past
        # naive prefix checks.
        candidate = "".join(ch for ch in candidate if ord(ch) > 32 or ch == " ")
        lowered = candidate.lower()
        if lowered.startswith("/") or lowered.startswith("#"):
            return candidate
        if any(lowered.startswith(scheme) for scheme in SAFE_URL_SCHEMES):
            return candidate
        return None

    # -- parser hooks ------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag in DROP_CONTENT:
            self.suppress_depth += 1
            return
        if self.suppress_depth:
            return
        if tag not in ALLOWED_TAGS:
            return  # drop the tag, keep whatever text is inside it

        kept = []
        for name, value in attrs:
            name = (name or "").lower()
            if name not in ALLOWED_ATTRS.get(tag, frozenset()):
                continue
            if name == "href":
                value = self._safe_href(value or "")
                if value is None:
                    continue
            kept.append(f' {name}="{escape(value or "", quote=True)}"')

        # Links out of a paper open in a new tab, and must not hand the opener
        # to whatever they point at.
        if tag == "a":
            kept.append(' rel="nofollow noopener noreferrer" target="_blank"')

        if tag in VOID_TAGS:
            self.out.append(f"<{tag}{''.join(kept)} />")
        else:
            self.out.append(f"<{tag}{''.join(kept)}>")
            self.open_tags.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.suppress_depth or tag in DROP_CONTENT:
            return
        if tag in ALLOWED_TAGS and tag in VOID_TAGS:
            self.out.append(f"<{tag} />")

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in DROP_CONTENT:
            self.suppress_depth = max(0, self.suppress_depth - 1)
            return
        if self.suppress_depth:
            return
        if tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return

        # Close only if it's genuinely open, so stray end tags can't unbalance
        # the surrounding page.
        if tag in self.open_tags:
            while self.open_tags:
                open_tag = self.open_tags.pop()
                self.out.append(f"</{open_tag}>")
                if open_tag == tag:
                    break

    def handle_data(self, data):
        if self.suppress_depth:
            return
        self.out.append(escape(data, quote=False))

    def handle_comment(self, data):
        pass  # comments are how conditional-comment tricks get in

    def result(self) -> str:
        while self.open_tags:
            self.out.append(f"</{self.open_tags.pop()}>")
        return "".join(self.out)


def clean_html(value: str, max_length: int = 20000) -> str:
    """Sanitize a fragment of edited prose. Always returns a safe string."""
    if not value:
        return ""
    text = str(value)[:max_length]
    parser = _Sanitizer()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        # A parser blow-up must never mean unsanitized output reaches a reader.
        return escape(text, quote=False)
    return parser.result().strip()


def clean_text(value: str, max_length: int = 500) -> str:
    """Plain text only — for headlines and award titles, which render inline."""
    if not value:
        return ""
    parser = _Sanitizer()
    try:
        parser.feed(str(value)[:max_length * 4])
        parser.close()
        rendered = parser.result()
    except Exception:
        rendered = str(value)

    # Strip every remaining tag; a headline is one line of words.
    stripped = _Stripper()
    try:
        stripped.feed(rendered)
        stripped.close()
    except Exception:
        return escape(str(value)[:max_length], quote=False)
    return " ".join(stripped.text.split())[:max_length]


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = ""

    def handle_data(self, data):
        self.text += data


def clean_image_url(value: str) -> str | None:
    """Only http(s) or a same-origin path may become an <img src>."""
    candidate = (value or "").strip()
    candidate = "".join(ch for ch in candidate if ord(ch) > 32 or ch == " ")
    lowered = candidate.lower()
    if lowered.startswith("/") and not lowered.startswith("//"):
        return candidate[:600]
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return candidate[:600]
    return None
