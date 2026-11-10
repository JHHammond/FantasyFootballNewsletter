"""The compact phone layout (John, 31 Oct).

Layered onto every stored paper when it is served, so it reaches papers
published before it existed without regenerating one. ``?layout=classic``
serves a paper without it. Nothing is stored. Every rule sits inside a
phone-width screen query, so desktop, print and the PDF are untouched.

What it does, from the 31 Oct read of Kevlarville week 7 at iPhone size:

- A one-line nameplate and a smaller front headline, so the first screen
  reaches the lead story. The dateline's three-fact summary goes; the
  "This Week" list right under it says the same.
- Game story headlines in tabloid capitals (9 Nov, John picked mockup G4:
  "it needs to pop"): Anton, under a red rule and a label that says what
  kind of game it was ("Upset · by 9.6"). Genre tags (COMEDY, DRAMA) go.
- Each game story shows its first stretch with a "Keep reading" fold. The
  lead story on the front is never folded (9 Nov, John).
- Photos keep their own shape (9 Nov): uploads are square and are shown
  whole, never cropped to a banner.
- This Week (9 Nov, John: mockup T2a without numbers, T2c's stripe): a
  black Anton bar, then per game the same label its story carries, the
  teaser in Anton capitals and one score line, with a red stripe down the
  side of every row.
- Standings (9 Nov, mockup S2 with the rank in the box): one row per team,
  its league rank in a black box (red for first), the record on the right,
  the streak and this week's points underneath. The Week in Numbers
  (mockup S3): a big red number, an Anton label, the teams under it.
- Fraud Watch and Power Rankings (9 Nov): a black bar over Fraud Watch
  (only it and This Week keep one; every other section gets a heavy rule
  and an Anton head), the stamp
  a flat red label, the case file's facts in one row of Anton numbers; the
  rankings as thin rows with a small rank, the team's photo and the verdict.
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
  /* The front headline (4 Nov, John: "it just doesn't really look like the
     main headline"; picked mockup A). Half again the size of a story
     headline, lines evened out so a score never sits alone, a red label
     above and a short rule below instead of the full-width underline. */
  .above-fold { text-align: center; }
  .above-fold .headline { font-size: clamp(26px, 8vw, 32px) !important; line-height: 1 !important;
      letter-spacing: -.3px !important; text-wrap: balance; overflow-wrap: break-word;
      border-bottom: 0 !important; padding: 0 2px !important; margin: 0 auto !important; max-width: none !important; }
  .cx-kicker { display: flex; align-items: center; justify-content: center; gap: 10px; margin: 4px 0 9px;
      font: 700 11.5px "Barlow Condensed", "Arial Narrow", Arial, sans-serif; letter-spacing: 2.6px;
      text-transform: uppercase; color: var(--accent, #c40000); }
  .cx-kicker::before, .cx-kicker::after { content: ""; width: 30px; height: 1px; background: currentColor; }
  .cx-headrule { width: 54px; height: 3px; background: var(--accent, #c40000); margin: 13px auto 14px; }
  .above-fold .dateline-bar { display: none !important; }
  /* Photos whole, in their own shape (9 Nov, John: "all photos that I add
     are square and should be fully displayed"). Overrides the 16:10 crop
     this layout used to force, the front photo's 4:3 crop, and the front's
     column-matching stretch, which do nothing useful in one column. */
  .image-wrap img, .front-col-center .image-wrap img,
  .front-page.fp-fit .front-col-center > .image-wrap > img {
      aspect-ratio: auto !important; height: auto !important; width: 100% !important;
      object-fit: contain !important; flex: none !important; }
  .front-page.fp-fit .front-col-center > .image-wrap {
      flex: none !important; min-height: 0 !important; max-height: none !important; }
  .image-wrap { float: none !important; width: auto !important; margin: 0 0 12px !important; }

  /* 2. Headlines and labels */
  .story-label { display: none !important; }
  /* Game story headlines (9 Nov, mockup G4). Anton is loaded by the
     script, phone only; Impact stands in until it arrives. */
  .story-card .story-headline, .story-headline-brief, .story-headline-small {
      font-family: "Anton", Impact, "Arial Narrow", sans-serif !important; font-weight: 400 !important;
      font-size: clamp(30px, 9.4vw, 38px) !important; line-height: 1.02 !important;
      letter-spacing: .2px !important; text-transform: uppercase !important; text-wrap: balance;
      overflow-wrap: break-word; margin: 0 0 6px !important; }
  /* The pair's two stories stack on a phone: no side rule between them, the
     same air as between any two stories (9 Nov). */
  .paired-col { border-right: 0 !important; padding-right: 0 !important; }
  .paired-col + .paired-col { margin-top: 26px !important; }
  .cx-tag { border-top: 5px solid var(--accent, #c40000); padding-top: 10px; margin: 0 0 6px;
      font: 700 12px "Barlow Condensed", "Arial Narrow", Arial, sans-serif; letter-spacing: 2.4px;
      text-transform: uppercase; color: var(--accent, #c40000); }
  .story-subhead { font-size: 12.5px !important; color: #555 !important; font-style: normal !important; }
  .col-story-teaser { color: #444 !important; font-style: normal !important; }
  .col-story-headline, .col-story-headline a { font-size: 16px !important; text-transform: none !important;
      letter-spacing: 0 !important; }
  .col-story { padding: 8px 0 !important; }

  /* This Week (9 Nov). Built by the script from the existing rows. */
  .col-section-label.cx-week-head, .section-title-full.cx-week-head { background: #111 !important; color: #fff !important; border: 0 !important;
      padding: 8px 12px !important; display: flex; justify-content: space-between; align-items: baseline;
      font-family: "Anton", Impact, "Arial Narrow", sans-serif !important; font-weight: 400 !important;
      font-size: 22px !important; letter-spacing: .4px !important; margin: 30px 0 0 !important; }
  /* The other sections' heading (9 Nov, "less repetitive"): a heavy rule
     and an Anton head. Only This Week and Fraud Watch keep a black bar. */
  .col-section-label.cx-rule-head, .section-title-full.cx-rule-head, .front-col .wk-board .wk-head {
      background: none !important; color: #111 !important; border: 0 !important; border-top: 5px solid #111 !important;
      padding: 10px 0 0 !important; margin: 34px 0 2px !important; display: flex; justify-content: space-between;
      align-items: baseline; font-family: "Anton", Impact, "Arial Narrow", sans-serif !important; font-weight: 400 !important;
      font-size: 26px !important; letter-spacing: .3px !important; text-transform: uppercase !important; text-align: left !important; }
  .cx-rule-head::after { content: attr(data-count); font: 600 12px "Barlow Condensed", "Arial Narrow", sans-serif;
      letter-spacing: 1.6px; color: #555; }
  .cx-dark .cx-rule-head, .cx-dark .front-col .wk-board .wk-head { color: #f2f4f7 !important; border-top-color: #f2f4f7 !important; }
  .cx-week-head::after { content: attr(data-count); font: 600 12px "Barlow Condensed", "Arial Narrow", sans-serif;
      letter-spacing: 1.6px; color: #bdbdbd; }
  .col-story.cx-week { padding: 0 !important; }

  .cx-tw { display: flex; gap: 12px; padding: 13px 0; color: inherit; text-decoration: none !important; }
  /* One colour for every stripe (9 Nov, John). */
  .cx-tw-bar { flex: none; width: 4px; background: var(--accent, #c40000); }
  .cx-tw-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
  .cx-tw-tag { font: 700 11.5px "Barlow Condensed", "Arial Narrow", sans-serif; letter-spacing: 2px;
      text-transform: uppercase; color: #555; }
  .cx-hot .cx-tw-tag { color: var(--accent, #c40000); }
  .cx-tw-teaser { font-family: "Anton", Impact, "Arial Narrow", sans-serif; font-size: 19px; line-height: 1.08;
      letter-spacing: .2px; text-transform: uppercase; color: #111; overflow-wrap: break-word; }
  .cx-tw-score { font-size: 13.5px; color: #555; line-height: 1.35; }
  .cx-tw-score b { color: #111; }
  .cx-dark .cx-tw-teaser, .cx-dark .cx-tw-score b { color: #f2f4f7; }
  .cx-dark .cx-tw-score, .cx-dark .cx-tw-tag { color: #b9c2cf; }

  /* Standings (9 Nov, second pass): thin rows, the rank in a ring for the
     top three (gold, silver, bronze), the streak, the record. No points:
     This Week already has them. The table stays in the page, hidden, for
     the script to read. */
  table.stats.cx-st-src { display: none !important; }
  .cx-st-row { display: flex; align-items: center; gap: 12px; padding: 9px 0; border-bottom: 1px solid #e4e0d8; }
  .cx-st-rank { flex: none; width: 30px; height: 30px; box-sizing: border-box; display: flex; align-items: center;
      justify-content: center; border-radius: 50%; border: 2px solid transparent;
      font-family: "Anton", Impact, sans-serif; font-size: 17px; color: #111; }
  .cx-st-row:nth-child(1) .cx-st-rank { border-color: #b8930b; color: #8a6d00; }
  .cx-st-row:nth-child(2) .cx-st-rank { border-color: #8a8f96; color: #5f646b; }
  .cx-st-row:nth-child(3) .cx-st-rank { border-color: #a0612d; color: #7d4a1f; }
  .cx-st-name { flex: 1; min-width: 0; font-size: 16px; font-weight: 700; white-space: nowrap; overflow: hidden;
      text-overflow: ellipsis; }
  .cx-st-streak { flex: none; width: 46px; text-align: right; font: 700 12.5px "Barlow Condensed", "Arial Narrow", sans-serif;
      letter-spacing: 1px; text-transform: uppercase; color: #555; }
  .cx-st-streak.cx-st-l { color: var(--accent, #c40000); }
  .cx-st-rec { flex: none; width: 40px; text-align: right; font-family: "Anton", Impact, sans-serif; font-size: 19px; }
  .cx-dark .cx-st-name, .cx-dark .cx-st-rec, .cx-dark .cx-st-rank { color: #f2f4f7; }
  .cx-dark .cx-st-row { border-bottom-color: #2b313c; }

  /* The Week in Numbers (9 Nov, second pass): a 2-by-2 grid, red numbers. */
  .front-col .wk-board { border: 0 !important; margin: 0 !important; }
  .front-col .wk-board .wk-cells { display: grid !important; grid-template-columns: repeat(2, minmax(0, 1fr));
      column-gap: 14px; padding: 0 !important; }
  .front-col .wk-board .wk-cell { display: flex !important; flex-direction: column; gap: 3px; text-align: left !important;
      padding: 12px 0 10px !important; border: 0 !important; border-bottom: 1px solid #e4e0d8 !important; }
  .front-col .wk-board .wk-label { order: 0; font: 700 11px "Barlow Condensed", "Arial Narrow", sans-serif !important;
      letter-spacing: 1.8px !important; text-transform: uppercase; color: #555 !important; margin: 0 !important; }
  .front-col .wk-board .wk-num { order: 1; font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important;
      font-size: 34px !important; line-height: 1 !important; color: var(--accent, #c40000) !important; margin: 0 !important; }
  .front-col .wk-board .wk-who { order: 2; font-style: normal !important; font-size: 13px !important; line-height: 1.3 !important;
      color: #3f3a33 !important; margin: 0 !important; }
  .cx-dark .front-col .wk-board .wk-label { color: #b9c2cf !important; }
  .cx-dark .front-col .wk-board .wk-who { color: #b9c2cf !important; }

  /* How You Stack Up (9 Nov): the same heading and red Anton numbers as the
     Week in Numbers, one fact per row. */
  .stack-up { margin: 0 !important; }
  .stack-up .stack-sub { font-style: normal !important; font-size: 13px !important; color: #555 !important;
      margin: 4px 0 2px !important; }
  .stack-up .stack-row { display: flex !important; align-items: center; gap: 14px; padding: 11px 0 !important;
      border-bottom: 1px solid #e4e0d8 !important; }
  .stack-up .stack-big { flex: none; width: 104px; font-family: "Anton", Impact, sans-serif !important;
      font-weight: 400 !important; font-size: 28px !important; line-height: 1 !important;
      color: var(--accent, #c40000) !important; }
  .stack-up .stack-row > span:last-child { font-size: 14px !important; line-height: 1.35; color: #3f3a33; }
  .stack-up .stack-honor { font: 700 12.5px "Barlow Condensed", "Arial Narrow", sans-serif !important;
      letter-spacing: 1.2px; text-transform: uppercase; color: #8a6d00 !important; margin: 6px 0 0 !important; }
  .cx-dark .stack-up .stack-row > span:last-child, .cx-dark .stack-up .stack-sub { color: #b9c2cf !important; }

  /* Fraud Watch (9 Nov). */
  .fraud-callout { margin: 30px 0 0 !important; padding: 0 !important; background: none !important;
      border: 2px solid #111 !important; }
  .fraud-callout .fc-head { background: #111; margin: 0 !important; padding: 8px 12px !important; border: 0 !important; }
  .fraud-callout .fc-head .fraud-callout-label { color: #fff !important; border: 0 !important; padding: 0 !important;
      margin: 0 !important; font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important;
      font-size: 22px !important; letter-spacing: .4px !important; }
  .fraud-callout .fc-stamp { transform: none !important; border: 0 !important; background: var(--accent, #c40000);
      color: #fff !important; padding: 3px 8px !important; font: 700 12px "Barlow Condensed", "Arial Narrow", sans-serif !important;
      letter-spacing: 2px !important; }
  .fraud-callout .fc-file { background: none !important; border: 0 !important; margin: 0 !important;
      padding: 16px 14px 0 !important; gap: 12px !important; }
  .fraud-callout .fc-face { width: 52px !important; height: 52px !important; }
  .fraud-callout .fc-tag { font: 700 11.5px "Barlow Condensed", "Arial Narrow", sans-serif !important; letter-spacing: 2px !important;
      color: var(--accent, #c40000) !important; }
  .fraud-callout .fc-name { font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important;
      font-size: 30px !important; line-height: 1.02 !important; text-transform: uppercase; }
  .fraud-callout .fc-facts { flex: 1 1 100% !important; display: grid !important;
      grid-template-columns: repeat(auto-fit, minmax(0, 1fr)); gap: 0 !important;
      border-top: 1px solid #111; border-bottom: 1px solid #111; }
  .fraud-callout .fc-fact { padding: 10px 6px 8px; gap: 3px; border-left: 1px solid #ddd; }
  .fraud-callout .fc-fact:first-child { border-left: 0; padding-left: 0; }
  .fraud-callout .fc-fact span { font: 700 10.5px "Barlow Condensed", "Arial Narrow", sans-serif !important;
      letter-spacing: 1.4px !important; opacity: 1 !important; color: #555; }
  .fraud-callout .fc-fact b { font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important;
      font-size: 24px !important; line-height: 1; }
  .fraud-callout .fc-fact.cx-red b { color: var(--accent, #c40000); }
  .fraud-callout .fraud-callout-body { padding: 14px 14px 16px !important; }
  .cx-dark .fraud-callout { border-color: #f2f4f7 !important; }
  .cx-dark .fraud-callout .fc-fact span { color: #b9c2cf; }

  /* Power Rankings (9 Nov): thin rows, the team's photo, a small rank. */
  .ranking-card { grid-template-columns: 22px 32px minmax(0, 1fr) !important; padding: 8px 0 !important;
      border-bottom-color: #e4e0d8 !important; }
  .ranking-card-rank { font-family: "Anton", Impact, sans-serif !important; font-weight: 400 !important;
      font-size: 20px !important; color: #111 !important; text-align: center; }
  .ranking-card-rank.cx-rank-top { color: var(--accent, #c40000) !important; }
  .ranking-card > img { width: 32px !important; height: 32px !important; border-width: 1.5px !important; }
  .ranking-card-name { font-size: 15px !important; font-weight: 700 !important; line-height: 1.2 !important; }
  .ranking-card-comment { font-style: normal !important; font-size: 12.5px !important; color: #5a554d !important; }
  .cx-dark .ranking-card-rank { color: #f2f4f7 !important; }
  .cx-dark .ranking-card-comment { color: #b9c2cf !important; }

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
  /* overflow: clip, not hidden (9 Nov). "hidden" starts a new formatting
     context, and one of those won't flow around a float, so the folded text
     of the story with the small photo sat in a narrow column beside it, and
     only wrapped under the photo once opened. "clip" crops the same way and
     leaves the wrap alone. "hidden" stays first as the fallback for a
     browser without "clip" (Safari before 16). */
  .cx-fold { max-height: var(--cx-h, 300px) !important; overflow: hidden !important;
      overflow: clip !important; position: relative;
      -webkit-mask-image: linear-gradient(to bottom, #000 calc(100% - 110px), transparent 100%);
      mask-image: linear-gradient(to bottom, #000 calc(100% - 110px), transparent 100%); }
  .cx-more { clear: both; display: flex; align-items: center; justify-content: center; gap: 8px;
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
  // Football abbreviations stay in capitals: "Dead QB Spot", not "Dead Qb Spot".
  var CAPS = /^(qb|rb|wr|te|k|dst|d\\/st|idp|ppr|td|tds|ot|ir|nfl|mvp|ol)(\\d*\\W*)$/;
  if (!document.getElementById("cx-anton")) {
    var f = document.createElement("link");
    f.id = "cx-anton"; f.rel = "stylesheet";
    f.href = "https://fonts.googleapis.com/css2?family=Anton&display=swap";
    document.head.appendChild(f);
  }
  function titleCase(t) {
    if (t !== t.toUpperCase()) return t;            // already mixed case
    return t.toLowerCase().split(/(\\s+)/).map(function (w, i) {
      if (/^\\s+$/.test(w)) return w;
      var c = w.match(CAPS);
      if (c) return c[1].toUpperCase() + c[2];
      if (i && SMALL.test(w)) return w;
      return w.replace(/[a-z]/, function (c) { return c.toUpperCase(); });
    }).join("");
  }
  // The front headline: "Week 8 · Cover story" above, a short rule below,
  // and "LAST PLACE WILL ENDS" read as a team, not a typo (4 Nov).
  var front = document.querySelector(".above-fold .headline");
  if (front && !document.querySelector(".cx-kicker")) {
    if (front.children.length === 0) {
      front.textContent = front.textContent.replace(/(^|[^A-Za-z]\s?)(\w+)\s+(place)\s+(?=[A-Za-z])/i, function (m, pre, a, b) {
        if (!/^(last|first|second|third)$/i.test(a) || /\b(in|to|of|from|into)\s*$/i.test(pre)) return m;
        return pre + a + "-" + b + " ";
      });
    }
    var week = ((document.querySelector(".above-fold .dateline-bar span") || {}).textContent || "").trim();
    var k = document.createElement("div");
    k.className = "cx-kicker";
    k.textContent = (/^Week \d+$/.test(week) ? week + " \u00b7 " : "") + "Cover story";
    front.parentNode.insertBefore(k, front);
    var r = document.createElement("div");
    r.className = "cx-headrule"; r.setAttribute("aria-hidden", "true");
    front.parentNode.insertBefore(r, front.nextSibling);
  }
  document.querySelectorAll(".story-headline").forEach(function (h) {
    if (h.children.length === 0) h.textContent = titleCase(h.textContent.trim());
  });
  // The label over each game story's headline (9 Nov): what kind of game it
  // was, read off the scorebar. Records there are AFTER the game, so one
  // result is taken back off each side to compare them at kickoff.
  function rec(el) {
    var m = ((el || {}).textContent || "").match(/(\d+)-(\d+)(?:-(\d+))?/);
    return m ? { w: +m[1], l: +m[2], t: +(m[3] || 0) } : null;
  }
  function pct(r) { var g = r.w + r.l + r.t; return g ? (r.w + r.t / 2) / g : null; }
  // One label per STORY, not per block: games 3 and 4 print as a pair inside
  // one .story-card, each in its own .paired-col (9 Nov: the second of the
  // pair had no label and so no rule between it and the story above).
  document.querySelectorAll(".story-card .story-headline").forEach(function (h) {
    var card = h.closest(".paired-col") || h.closest(".story-card");
    var bar = card && card.querySelector(".story-scorebar");
    var prev = h.previousElementSibling;
    if (!bar || (prev && prev.classList.contains("cx-tag"))) return;
    var by = parseFloat(((bar.querySelector(".sm-num") || {}).textContent || "").trim());
    var label;
    if (!(by > 0)) {
      label = "Tie";
    } else {
      var w = rec(bar.querySelector(".st-win .st-rec")), l = rec(bar.querySelector(".st-lose .st-rec"));
      var upset = false;
      if (w && l && w.w > 0 && l.l > 0) {
        var before = pct({ w: w.w - 1, l: w.l, t: w.t }), other = pct({ w: l.w, l: l.l - 1, t: l.t });
        upset = before !== null && other !== null && before < other;
      }
      var kind = upset ? "Upset" : by < 5 ? "Nail-biter" : by >= 30 ? "Blowout" : "Final";
      label = kind + " \u00b7 by " + by.toFixed(1);
    }
    var t = document.createElement("div");
    t.className = "cx-tag"; t.textContent = label;
    h.parentNode.insertBefore(t, h);
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
  // The lead story is never folded (9 Nov, John): it's the story the
  // paper is about, so it reads in full by default.
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
  // This Week (9 Nov): rebuilt from each row's own text. A row whose story
  // has a label takes that label, so an upset reads the same in both places.
  var weekRows = document.querySelectorAll(".col-story");
  weekRows.forEach(function (row) {
    var body = row.querySelector(".col-story-body"), head = row.querySelector(".col-story-headline");
    if (!body || !head || row.classList.contains("cx-week")) return;
    var sc = (body.textContent || "").replace(/\s+/g, " ")
      .match(/([\d.]+)\s*[\u2014\u2013-]\s*([\d.]+).*?([\d.]+)\s*$/);
    if (!sc) return;
    var names = (head.textContent || "").trim().split(" def. ");
    var link = head.querySelector("a");
    var by = parseFloat(sc[3]);
    var label = null, target = link && document.getElementById((link.getAttribute("href") || "").slice(1));
    var tag = target && target.previousElementSibling;
    if (tag && tag.classList.contains("cx-tag")) label = tag.textContent;
    if (!label) label = !(by > 0) ? "Tie" : (by < 5 ? "Nail-biter" : by >= 30 ? "Blowout" : "Final") + " \u00b7 by " + by.toFixed(1);
    var teaser = row.querySelector(".col-story-teaser");
    var box = document.createElement(link ? "a" : "div");
    box.className = "cx-tw";
    if (link) box.href = link.getAttribute("href");
    var bar = document.createElement("span"); bar.className = "cx-tw-bar"; bar.setAttribute("aria-hidden", "true");
    var main = document.createElement("div"); main.className = "cx-tw-main";
    var t = document.createElement("span"); t.className = "cx-tw-tag"; t.textContent = label;
    main.appendChild(t);
    if (teaser && teaser.textContent.trim()) {
      var tz = document.createElement("span"); tz.className = "cx-tw-teaser"; tz.textContent = teaser.textContent.trim();
      main.appendChild(tz);
    }
    var line = document.createElement("span"); line.className = "cx-tw-score";
    var win = document.createElement("b"); win.textContent = (names[0] || "") + " " + sc[1];
    line.appendChild(win);
    line.appendChild(document.createTextNode(" \u00b7 " + (names[1] || "") + " " + sc[2]));
    main.appendChild(line);
    box.appendChild(bar); box.appendChild(main);
    if (/^(Upset|Nail-biter|Blowout)/.test(label)) row.classList.add("cx-hot");
    row.classList.add("cx-week");
    row.textContent = "";
    row.appendChild(box);
  });
  // Standings (9 Nov): one row per team, read off the printed table.
  var weekNo = ((document.querySelector(".above-fold .dateline-bar span") || {}).textContent || "").trim();
  document.querySelectorAll(".col-section-label").forEach(function (h) {
    if (!/^\\s*standings\\s*$/i.test(h.textContent)) return;
    var table = h.nextElementSibling;
    if (!table || !table.matches("table.stats") || table.classList.contains("cx-st-src")) return;
    var list = document.createElement("div");
    list.className = "cx-st";
    table.querySelectorAll("tbody tr").forEach(function (tr) {
      var td = tr.querySelectorAll("td");
      if (td.length < 4) return;
      var name = ((tr.querySelector(".team-cell span") || td[1]).textContent || "").trim();
      var streak = tr.querySelector(".streak");
      var title = streak ? (streak.getAttribute("title") || "") : "";
      var sm = title.match(/^(Won|Lost) (\\d+)/);
      var row = document.createElement("div"); row.className = "cx-st-row";
      var rank = document.createElement("span"); rank.className = "cx-st-rank";
      rank.textContent = (td[0].textContent || "").trim();
      var nm = document.createElement("span"); nm.className = "cx-st-name"; nm.textContent = name;
      var st = document.createElement("span"); st.className = "cx-st-streak";
      if (sm) { st.textContent = sm[1] + " " + sm[2]; if (sm[1] === "Lost") st.classList.add("cx-st-l"); }
      var rc = document.createElement("span"); rc.className = "cx-st-rec"; rc.textContent = (td[2].textContent || "").trim();
      row.appendChild(rank); row.appendChild(nm); row.appendChild(st); row.appendChild(rc);
      list.appendChild(row);
    });
    if (!list.children.length) return;
    table.classList.add("cx-st-src");
    table.parentNode.insertBefore(list, table);
    h.classList.add("cx-rule-head");
    if (/^Week \\d+$/.test(weekNo)) h.setAttribute("data-count", "After " + weekNo.toLowerCase());
  });
  document.querySelectorAll(".col-section-label").forEach(function (h) {
    if (!/^\s*this week\s*$/i.test(h.textContent)) return;
    var n = document.querySelectorAll(".col-story.cx-week").length;
    h.classList.add("cx-week-head");
    if (n) h.setAttribute("data-count", n + (n === 1 ? " game" : " games"));
  });
  // Power rankings (9 Nov): the number alone, red for first, ink for the
  // rest; a black bar over the section like the others.
  document.querySelectorAll(".ranking-card-rank").forEach(function (el) {
    var n = parseInt((el.textContent || "").replace("#", ""), 10);
    if (!(n > 0)) return;
    el.textContent = String(n);
    el.classList.add(n === 1 ? "cx-rank-top" : "cx-rank-plain");
  });
  document.querySelectorAll(".stack-up .col-section-label").forEach(function (h) {
    h.classList.add("cx-rule-head");
  });
  document.querySelectorAll(".section-title-full").forEach(function (h) {
    if (!/^\\s*power rankings\\s*$/i.test(h.textContent)) return;
    h.classList.add("cx-rule-head");
    if (/^Week \\d+$/.test(weekNo)) h.setAttribute("data-count", weekNo);
  });
  // Fraud Watch (9 Nov): this week's score in red, like the mockup.
  document.querySelectorAll(".fraud-callout .fc-fact").forEach(function (f) {
    var k = ((f.querySelector("span") || {}).textContent || "").trim().toLowerCase();
    if (k === "this week") f.classList.add("cx-red");
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
