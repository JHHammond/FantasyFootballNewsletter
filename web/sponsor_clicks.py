"""Counting presses on the sponsor's links in a paper (4 Nov).

John: "how many times the PrizePicks buttons on papers is clicked", on the
staff stats page. Layered on at serve time, like the compact layout and the
house footer, so every paper counts, old ones included, and nothing stored
changes.

The links themselves are untouched: they still go straight to the address
the sponsor gave. The contract says a placement links "only to Company's
website ... as specified by Company" and that landing pages "cannot redirect",
so a hop through our own domain was ruled out. Instead the browser reports
the press to POST /e on its way out (sendBeacon survives the page unloading)
and the link opens as it always did.

Which placement was pressed is read from the box the link sits in. A press
is a press, not a person: two taps by one reader count twice.
"""

MARK = 'id="sponsor-clicks"'

SCRIPT = """
<script id="sponsor-clicks">(function () {
  function where(a) {
    if (a.classList.contains('promo-link')) return 'promo';
    if (a.closest('.numbers-box')) return 'numbers';
    if (a.closest('.award-sponsored')) return 'award';
    if (a.closest('.bp-preview.sponsored')) return 'lines';
    return 'other';
  }
  function send(e) {
    if (e.type === 'auxclick' && e.button !== 1) return;
    var t = e.target, a = t && t.closest ? t.closest('a[rel~="sponsored"]') : null;
    if (!a) return;
    try {
      var f = new FormData();
      f.append('n', 'sponsor_click');
      f.append('where', where(a));
      f.append('paper', location.pathname);
      if (navigator.sendBeacon && navigator.sendBeacon('/e', f)) return;
      fetch('/e', {method: 'POST', body: f, keepalive: true});
    } catch (_) {}
  }
  document.addEventListener('click', send, true);
  document.addEventListener('auxclick', send, true);
})();</script>
"""


def apply(html: str) -> str:
    """The paper with the counter. Idempotent; never moves or changes a link."""
    if MARK in html:
        return html
    if "</body>" in html:
        i = html.rfind("</body>")
        return html[:i] + SCRIPT + html[i:]
    return html + SCRIPT
