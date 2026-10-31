"""The compact phone layout, as a test (John, 31 Oct).

Layered onto any stored paper when it is served with ``?layout=compact``,
so the new design can be judged against real leagues' papers without
regenerating one. Nothing is stored and nothing changes for anyone who
doesn't ask for it. Every rule sits inside a phone-width screen query, so
desktop, print and the PDF are untouched.

What it does, from the 31 Oct read of Kevlarville week 7 at iPhone size:

- A one-line nameplate and a smaller front headline, so the first screen
  reaches the lead story. The dateline's three-fact summary goes; the
  "This Week" list right under it says the same.
- Story headlines in title case at a size that fits two or three lines,
  instead of four lines of capitals. Genre tags (COMEDY, DRAMA) go.
- Each game story shows its first paragraph with a "Keep reading" fold.
- Rules and white space instead of boxes: the scorebar, the award cards
  and the ranking cards lose their frames. Fraud Watch keeps its box; it
  is meant to stand apart.
- Power rankings as a list, one row per team.
- Honor Roll and Detention as sideways swipe rows.
- Fewer accent colours: decks and teasers in ink, not red italic.
"""

from __future__ import annotations

COMPACT_CSS = """
<style id="compact-layout">
@media screen and (max-width: 760px) {
  /* 1. The first screen */
  .masthead { padding: 10px 12px 8px !important; }
  .masthead .paper-name { font-size: 25px !important; line-height: 1 !important;
      white-space: nowrap; letter-spacing: 0 !important; }
  .masthead .edition-line { font-size: 10px !important; margin-top: 4px !important; padding-top: 4px !important; }
  .above-fold { padding-top: 12px !important; }
  .above-fold .headline { font-size: 23px !important; line-height: 1.1 !important; letter-spacing: 0 !important;
      margin-bottom: 8px !important; }
  .above-fold .dateline-bar { display: none !important; }
  .image-wrap img { aspect-ratio: 16 / 10 !important; }
  .image-wrap { float: none !important; width: auto !important; margin: 0 0 12px !important; }

  /* 2. Headlines and labels */
  .story-label { display: none !important; }
  .story-headline { font-size: 23px !important; line-height: 1.14 !important; letter-spacing: -.2px !important;
      text-transform: none !important; margin-bottom: 4px !important; }
  .story-headline-brief, .story-headline-small { font-size: 20px !important; }
  .story-subhead { font-size: 12.5px !important; color: #555 !important; font-style: normal !important; }
  .col-story-teaser { color: #444 !important; font-style: normal !important; }
  .col-story-headline, .col-story-headline a { font-size: 16px !important; text-transform: none !important;
      letter-spacing: 0 !important; }
  .col-story { padding: 8px 0 !important; }

  /* 3. Rules, not boxes */
  .story-scorebar { background: none !important; border: 0 !important; border-top: 1px solid #111 !important;
      border-bottom: 1px solid #111 !important; border-radius: 0 !important; padding: 8px 0 !important;
      margin: 8px 0 12px !important; }
  .story-card { padding-bottom: 18px !important; margin-bottom: 18px !important; border-bottom: 3px double #111 !important; }
  .award-card { border: 0 !important; border-top: 2px solid #111 !important; border-radius: 0 !important;
      box-shadow: none !important; background: none !important; padding: 12px 0 4px !important; }
  .award-card .award-title { font-size: 15px !important; }

  /* 4. The fold */
  .cx-folded > :not(:first-child) { display: none !important; }
  .cx-clamp { display: -webkit-box !important; -webkit-line-clamp: 4; -webkit-box-orient: vertical;
      overflow: hidden !important; }
  .cx-more { display: block; width: 100%; margin: 6px 0 0; padding: 10px 0; background: none;
      border: 0; border-top: 1px solid #ccc; font: inherit; font-size: 12px; font-weight: 800;
      letter-spacing: 1.4px; text-transform: uppercase; color: var(--accent, #c40000); text-align: left;
      cursor: pointer; }

  /* 5. Power rankings as a list */
  .rankings-grid, .rankings-row { display: block !important; }
  .ranking-card { display: grid !important; grid-template-columns: 34px 32px minmax(0, 1fr);
      column-gap: 10px; align-items: center; text-align: left !important; border: 0 !important;
      border-bottom: 1px solid #ddd !important; border-radius: 0 !important; background: none !important;
      box-shadow: none !important; padding: 9px 0 !important; margin: 0 !important; }
  .ranking-card-rank { grid-row: 1 / span 2; font-size: 20px !important; margin: 0 !important; }
  .ranking-card > img { grid-row: 1 / span 2; width: 30px !important; height: 30px !important; margin: 0 !important; }
  .ranking-card-name { grid-column: 3; font-size: 15px !important; margin: 0 !important; }
  .ranking-card-comment { grid-column: 3; font-size: 12.5px !important; line-height: 1.35 !important; margin: 0 !important; }

  /* 6. Honor Roll and Detention swipe sideways */
  .hd-grid { display: flex !important; overflow-x: auto; scroll-snap-type: x mandatory; gap: 10px !important;
      padding-bottom: 6px !important; -webkit-overflow-scrolling: touch; }
  .hd-grid > .hd-card { flex: 0 0 44% !important; scroll-snap-align: start; margin: 0 !important; }
}
</style>
"""

COMPACT_JS = """
<script id="compact-layout-js">
(function () {
  if (!window.matchMedia || !matchMedia("screen and (max-width: 760px)").matches) return;
  var SMALL = /^(a|an|and|as|at|but|by|for|in|of|on|or|the|to|vs\\.?|def\\.?)$/;
  function titleCase(t) {
    if (t !== t.toUpperCase()) return t;            // already mixed case
    return t.toLowerCase().split(/(\\s+)/).map(function (w, i) {
      if (/^\\s+$/.test(w) || (i && SMALL.test(w))) return w;
      return w.replace(/[a-z]/, function (c) { return c.toUpperCase(); });
    }).join("");
  }
  document.querySelectorAll(".story-headline").forEach(function (h) {
    if (h.children.length === 0) h.textContent = titleCase(h.textContent.trim());
  });
  function fold(el, cls) {
    el.classList.add(cls);
    var b = document.createElement("button");
    b.type = "button"; b.className = "cx-more"; b.textContent = "Keep reading \\u2193";
    b.addEventListener("click", function () {
      el.classList.remove(cls); b.remove();
      el.parentNode.querySelectorAll("p[hidden]").forEach(function (q) { q.hidden = false; });
    });
    el.parentNode.insertBefore(b, el.nextSibling);
  }
  document.querySelectorAll(".lead-story").forEach(function (lead) {
    var first = lead.querySelector("p") || lead;
    if ((lead.textContent || "").length > 320) fold(first, "cx-clamp");
    lead.querySelectorAll(":scope > p ~ p").forEach(function (q) { q.hidden = true; });
  });
  document.querySelectorAll(".story-card .story-body").forEach(function (body) {
    if (body.querySelectorAll(":scope > p").length > 1) fold(body, "cx-folded");
  });
})();
</script>
"""


def apply(html: str) -> str:
    """The paper with the compact layout layered on. Idempotent."""
    if 'id="compact-layout"' in html:
        return html
    html = html.replace("</head>", COMPACT_CSS + "</head>", 1)
    if "</body>" in html:
        html = html.replace("</body>", COMPACT_JS + "</body>", 1)
    else:
        html += COMPACT_JS
    return html
