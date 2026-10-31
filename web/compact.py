"""The compact phone layout (John, 31 Oct).

Layered onto every stored paper when it is served, so it reaches papers
published before it existed without regenerating one. ``?layout=classic``
serves a paper without it. Nothing is stored. Every rule sits inside a
phone-width screen query, so desktop, print and the PDF are untouched.

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
  /* The fold (31 Oct, second pass): a good run of the story, about
     eleven lines, fading out into the page, then a real button. The fade
     is a mask on the text, so it works on every theme's paper, dark
     Gameday included. */
  .cx-fold { max-height: var(--cx-h, 300px) !important; overflow: hidden !important; position: relative;
      -webkit-mask-image: linear-gradient(to bottom, #000 calc(100% - 110px), transparent 100%);
      mask-image: linear-gradient(to bottom, #000 calc(100% - 110px), transparent 100%); }
  .cx-more { display: flex; align-items: center; justify-content: center; gap: 8px;
      width: 100%; min-height: 48px; margin: 4px 0 6px; padding: 12px 16px;
      background: #111; color: #fff; border: 0; border-radius: 6px; cursor: pointer;
      font: inherit; font-size: 14px; font-weight: 800; letter-spacing: 1.2px; text-transform: uppercase;
      -webkit-tap-highlight-color: transparent; }
  .cx-more:active { transform: scale(.985); }
  .lead-story + .cx-more, .lead-story .cx-more { margin-bottom: 22px; }
  /* On a dark paper (Gameday) the button flips to light. Detected by the
     script, which reads the page's own background. */
  .cx-dark .cx-more { background: #f2f4f7; color: #0b0d10; }
  .cx-dark .section-note, .cx-dark .award-desc, .cx-dark .col-story-teaser, .cx-dark .story-subhead,
  .cx-dark .obit-dates, .cx-dark .rb-who, .cx-dark .wire-body, .cx-dark .hd-sub, .cx-dark .lt-proj,
  .cx-dark .bp-note, .cx-dark .dr-note { color: #b9c2cf !important; }
  .cx-dark .section-title-full, .cx-dark .col-section-label, .cx-dark .bp-label, .cx-dark .award-title,
  .cx-dark .wk-head, .cx-dark .classifieds-title, .cx-dark .fraud-callout-label,
  .cx-dark .cx-rank-plain, .cx-dark .col-story-headline a, .cx-dark .teaser-link { color: #f2f4f7 !important; }
  .cx-dark .section-title-full, .cx-dark .col-section-label, .cx-dark .bp-label,
  .cx-dark .classifieds-title { border-top-color: #f2f4f7 !important; }
  .cx-dark .story-scorebar { border-color: #2b313c !important; }
  .cx-more .cx-arrow { font-size: 18px; line-height: 1; }
  .cx-more.cx-quiet { background: none; color: var(--accent, #c40000); border: 1.5px solid currentColor;
      min-height: 44px; font-size: 12.5px; }

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


  /* ---- General UI pass (31 Oct, second round) ---- */

  /* Alignment: one left edge for everything below the nameplate. */
  .front-page { padding-left: 13px !important; padding-right: 13px !important; }
  .back-page { margin-left: 13px !important; margin-right: 13px !important; border: 0 !important; }
  .back-page > div, .bp-records, .bp-tx, .bp-trades { padding-left: 0 !important; padding-right: 0 !important;
      border: 0 !important; }
  .front-col, .front-col-center, .feature-main, .lead-story,
  .lead-story p, .col-story, .wk-board { margin-left: 0 !important; margin-right: 0 !important;
      padding-left: 0 !important; padding-right: 0 !important; max-width: none !important; }
  .section-title-full, .col-section-label, .bp-label, .section-note, .award-head, .award-desc,
  .wk-head, .classifieds-title { text-align: left !important; }

  /* Rhythm: comfortable phone leading, one body size. */
  .story-body p, .lead-story p, .award-body p, .award-body, .obit p, .fraud-callout-body,
  .fraud-callout-body p, .classified-body { font-size: 17px !important; line-height: 1.5 !important; }
  .story-body p, .lead-story p, .award-body p { margin: 0 0 .7em !important; }

  /* A type scale of five: body 17, small 13, label 12, story head 23, front 23.
     Every label in one style: small, heavy, tracked, ink. */
  .section-title-full, .col-section-label, .bp-label, .award-title, .wk-head, .classifieds-title,
  .fraud-callout-label { font-family: inherit !important; font-size: 12px !important; font-weight: 800 !important;
      letter-spacing: 1.6px !important; text-transform: uppercase !important; font-style: normal !important;
      color: #111 !important; }
  .section-title-full, .col-section-label, .bp-label, .classifieds-title {
      background: none !important; border: 0 !important; border-top: 3px solid #111 !important;
      padding: 8px 0 0 !important; margin: 0 0 10px !important; }
  .section-note, .award-desc, .col-story-teaser, .story-subhead, .obit-dates, .rb-who, .wire-body,
  .hd-sub, .lt-proj, .bp-note, .dr-note { font-size: 13px !important; color: #3f3a33 !important;
      font-style: normal !important; }

  /* Hierarchy and restraint: ink for labels and ranks; the accent is kept
     for what can be tapped and for the one number that matters. */
  .cx-rank-plain { color: #111 !important; }
  .col-story-headline a, .teaser-link { color: #111 !important; }

  /* Proximity: tight inside a group, clear air between groups. */
  .story-headline { margin-bottom: 2px !important; }
  .full-section, .back-page > div { margin-top: 28px !important; }
  .col-story { padding: 10px 0 !important; border-bottom: 1px solid #ddd !important; }

  /* Affordance: the nav says it scrolls; the This Week rows look tappable. */
  .section-nav { -webkit-mask-image: linear-gradient(90deg, #000 82%, transparent);
      mask-image: linear-gradient(90deg, #000 82%, transparent); }
  .section-nav a { padding-top: 14px !important; padding-bottom: 14px !important; }
  .col-story-headline a { display: block; padding: 6px 24px 6px 0; text-decoration: none !important;
      border: 0 !important; position: relative; }
  .col-story-headline a::after { content: "›"; position: absolute; right: 0; top: 50%; transform: translateY(-55%);
      font-size: 26px; line-height: 1; color: var(--accent, #c40000); }

  /* Progressive disclosure: the record book and the wire show three. */
  .cx-hide { display: none !important; }

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
  var bg = getComputedStyle(document.querySelector(".page") || document.body).backgroundColor;
  var rgb = (bg.match(/\d+/g) || [255, 255, 255]).slice(0, 3).map(Number);
  if ((rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000 < 90) document.body.classList.add("cx-dark");
  // Fold a story at about `lines` lines, with a fade and a button. Only
  // when there is enough left below the fold to be worth a tap.
  function fold(el, lines, label) {
    var lh = parseFloat(getComputedStyle(el.querySelector("p") || el).lineHeight) || 26;
    var h = Math.round(lh * lines);
    if (el.scrollHeight < h + lh * 4) return;
    el.style.setProperty("--cx-h", h + "px");
    el.classList.add("cx-fold");
    var words = (el.textContent || "").trim().split(/\s+/).length;
    var mins = Math.max(1, Math.round(words / 230));
    var b = document.createElement("button");
    b.type = "button"; b.className = "cx-more";
    b.setAttribute("aria-expanded", "false");
    b.innerHTML = label + ' <span class="cx-arrow" aria-hidden="true">\u2193</span>';
    if (words > 120) b.setAttribute("aria-label", label + ", " + mins + " minute read");
    b.addEventListener("click", function () {
      el.classList.remove("cx-fold"); el.style.removeProperty("--cx-h"); b.remove();
    });
    el.parentNode.insertBefore(b, el.nextSibling);
  }
  document.querySelectorAll(".lead-story").forEach(function (lead) {
    fold(lead, 9, "Read the full story");
  });
  document.querySelectorAll(".story-card .story-body").forEach(function (body) {
    fold(body, 11, "Keep reading");
  });

  // One format for a result, everywhere: "141.8–126.4 · by 15.4".
  document.querySelectorAll(".col-story-body").forEach(function (el) {
    el.textContent = el.textContent.replace(/\s+/g, " ").trim()
      .replace(/^([\d.]+)\s*[—–-]\s*([\d.]+)\s*\|\s*margin:\s*([\d.]+)$/, "$1–$2 · by $3");
  });
  document.querySelectorAll(".story-subhead").forEach(function (el) {
    el.textContent = el.textContent.replace(/\s*\|\s*([\d.]+)\s*-\s*([\d.]+)\s*$/, ", $1–$2");
  });
  // Medal colours for the top three ranks; ink for the rest.
  document.querySelectorAll(".ranking-card-rank").forEach(function (el) {
    var n = parseInt((el.textContent || "").replace("#", ""), 10);
    if (n > 3) el.classList.add("cx-rank-plain");
  });
  // Three rows of the record book and the wire, then "Show all".
  function showThree(rows, label) {
    if (rows.length <= 3) return;
    for (var i = 3; i < rows.length; i++) rows[i].classList.add("cx-hide");
    var b = document.createElement("button");
    b.type = "button"; b.className = "cx-more cx-quiet";
    b.textContent = label.replace("N", rows.length) + " ↓";
    b.addEventListener("click", function () {
      rows.forEach(function (r) { r.classList.remove("cx-hide"); }); b.remove();
    });
    rows[rows.length - 1].parentNode.insertBefore(b, rows[rows.length - 1].nextSibling);
  }
  document.querySelectorAll(".bp-records").forEach(function (box) {
    showThree(Array.prototype.slice.call(box.querySelectorAll(".rb-row")), "All N records");
  });
  document.querySelectorAll(".wire").forEach(function (box) {
    showThree(Array.prototype.slice.call(box.querySelectorAll(".wire-row")), "All N moves");
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
