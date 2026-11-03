"""The way out of a paper and into the rest of the site (2 Nov).

John: "a button to the bottom of the paper that encourages people to check
out the rest of the site". Layered on at serve time, like the compact layout,
so every paper gets it, old ones included, and nothing stored changes.
Screen only: the PDF and a printed copy end where they always did.

Links only go to sections that are open to the public. Each one waits on the
same switch on Render that opens its page, so a reader never lands on a
staff-only page.
"""

import os

MARK = 'id="house-footer"'

# (switch, path, link label, name mid-sentence). None means always open.
SECTIONS = (
    (None, "/market", "The trade market", "the trade market"),
    ("LUCK_PUBLIC", "/luck", "The Luck Index", "the Luck Index"),
    ("RANKINGS_PUBLIC", "/rankings", "National rankings", "national power rankings"),
    ("AROUND_PUBLIC", "/around", "Around the Leagues", "the week across every league"),
)


def open_sections():
    return [s for s in SECTIONS if s[0] is None or os.getenv(s[0], "").strip() == "1"]


CSS = """
<style id="house-footer-css">
.house-footer { margin: 24px auto 8px; padding: 22px 20px 30px; max-width: 760px; text-align: center;
    font-family: Georgia, "Times New Roman", serif; }
.hf-end { font: 700 13px "Barlow Condensed", "Helvetica Neue", Arial, sans-serif; letter-spacing: 6px; opacity: .55; margin-bottom: 14px; }
.hf-head { font-size: 26px; font-weight: 700; line-height: 1.2; margin: 0 0 8px; }
.hf-sub { font-size: 16px; line-height: 1.5; opacity: .82; margin: 0 auto 20px; max-width: 560px; }
.hf-button { display: inline-flex; align-items: center; gap: 10px; background: var(--accent, #c40000); color: #fff !important;
    font: 800 15px "Barlow Condensed", "Helvetica Neue", Arial, sans-serif; letter-spacing: 1.5px; text-transform: uppercase;
    padding: 15px 28px; border-radius: 3px; text-decoration: none !important; transition: filter .15s, transform .15s; }
.hf-button:hover { filter: brightness(1.1); transform: translateY(-1px); }
.hf-links { display: flex; flex-wrap: wrap; justify-content: center; gap: 6px 22px; margin-top: 18px;
    font: 700 13px "Barlow Condensed", "Helvetica Neue", Arial, sans-serif; letter-spacing: 1.2px; text-transform: uppercase; }
.hf-links a { color: inherit !important; text-decoration: none; border-bottom: 1.5px solid currentColor; padding-bottom: 1px; opacity: .8; }
.hf-links a:hover { opacity: 1; }
@media screen and (max-width: 600px) {
    .house-footer { margin-top: 16px; padding: 18px 16px 26px; }
    .hf-head { font-size: 22px; }
    .hf-sub { font-size: 15px; }
    .hf-button { display: flex; justify-content: center; }
}
@media print { .house-footer { display: none !important; } }
</style>
"""


def _sub(sections):
    names = [said for _, _, _, said in sections]
    if not names:
        return "Papers like this one for every kind of league. Free to look around."
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    listed = listed[0].upper() + listed[1:]
    return f"{listed}, plus a paper like this one for any league. Free to look around, no sign-in."


def block() -> str:
    sections = open_sections()
    links = "".join(f'<a href="{path}?from=paper" target="_top">{label}</a>'
                    for _, path, label, _ in sections)
    return f"""
<section class="house-footer" {MARK} aria-label="More from The Commissioner's Desk">
    <div class="hf-end" aria-hidden="true">&mdash; 30 &mdash;</div>
    <h2 class="hf-head">That&rsquo;s the paper. There&rsquo;s more at The Commissioner&rsquo;s Desk.</h2>
    <p class="hf-sub">{_sub(sections)}</p>
    <a class="hf-button" href="/?from=paper" target="_top">Explore the site <span aria-hidden="true">&rarr;</span></a>
    {f'<nav class="hf-links">{links}</nav>' if links else ''}
</section>
"""


def apply(html: str) -> str:
    """The paper with the footer at the end. Idempotent."""
    if MARK in html:
        return html
    html = html.replace("</head>", CSS + "</head>", 1)
    if "</main>" in html:
        i = html.rfind("</main>")
        return html[:i] + block() + html[i:]
    if "</body>" in html:
        i = html.rfind("</body>")
        return html[:i] + block() + html[i:]
    return html + block()
