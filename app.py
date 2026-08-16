import streamlit as st
import os
import json
import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

st.set_page_config(
    page_title="Commish — Fantasy Football Newspapers",
    page_icon="🏈",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;0,900;1,700;1,900&family=Barlow+Condensed:wght@300;400;600;700;800;900&family=Barlow:wght@300;400;500;600&display=swap');

#MainMenu, footer, header { visibility: hidden; }
* { box-sizing: border-box; margin: 0; padding: 0; }

:root {
    --cream:   #f2ede3;
    --cream2:  #e8e0d0;
    --cream3:  #ddd5c4;
    --green:   #1e3a0f;
    --green2:  #2d5016;
    --green3:  #3d6b1e;
    --brown:   #8b5e3c;
    --brown2:  #6b4423;
    --gold:    #c8a200;
    --ink:     #111111;
    --ink2:    #333333;
    --muted:   #6b6050;
    --white:   #ffffff;
    --border:  #cfc8b8;
}

.main .block-container { padding: 0 !important; max-width: 100% !important; }
html, body, .stApp { background: var(--ink) !important; font-family: 'Barlow', sans-serif; }

/* ═══════════════════════════════════════
   LANDING NAV
═══════════════════════════════════════ */
.land-nav {
    position: fixed;
    top: 0; left: 0; right: 0;
    z-index: 999;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 60px;
    height: 64px;
    background: rgba(17,17,17,0.92);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid rgba(255,255,255,0.06);
}

.land-nav-logo {
    font-family: 'Playfair Display', serif;
    font-size: 22px;
    font-weight: 900;
    color: var(--cream);
    letter-spacing: 4px;
    text-transform: uppercase;
}

.land-nav-logo em { color: var(--brown); font-style: normal; }

.land-nav-links {
    display: flex;
    align-items: center;
    gap: 32px;
}

.land-nav-link {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 2.5px;
    text-transform: uppercase;
    color: rgba(242,237,227,0.55);
    cursor: pointer;
}

.land-nav-btn {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--cream);
    background: var(--green2);
    border: none;
    padding: 10px 24px;
    cursor: pointer;
}

/* ═══════════════════════════════════════
   HERO
═══════════════════════════════════════ */
.hero {
    min-height: 100vh;
    background: var(--ink);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: 120px 48px 80px;
    position: relative;
    overflow: hidden;
    background-image:
        radial-gradient(ellipse 80% 60% at 50% 40%, rgba(45,80,22,0.35) 0%, transparent 70%),
        radial-gradient(ellipse 40% 40% at 80% 70%, rgba(139,94,60,0.12) 0%, transparent 60%);
}

/* Newspaper texture lines */
.hero::before {
    content: '';
    position: absolute;
    inset: 0;
    background-image: repeating-linear-gradient(
        0deg,
        transparent,
        transparent 28px,
        rgba(255,255,255,0.018) 28px,
        rgba(255,255,255,0.018) 29px
    );
    pointer-events: none;
}

.hero-eyebrow {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 5px;
    text-transform: uppercase;
    color: var(--brown);
    margin-bottom: 24px;
    display: flex;
    align-items: center;
    gap: 12px;
    justify-content: center;
}

.hero-eyebrow::before, .hero-eyebrow::after {
    content: '';
    width: 40px;
    height: 1px;
    background: var(--brown);
    opacity: 0.6;
}

.hero-headline {
    font-family: 'Playfair Display', serif;
    font-size: clamp(52px, 7vw, 96px);
    font-weight: 900;
    line-height: 1.0;
    color: var(--cream);
    margin-bottom: 12px;
    max-width: 900px;
}

.hero-headline em {
    color: var(--brown);
    font-style: italic;
}

.hero-sub {
    font-family: 'Barlow', sans-serif;
    font-size: 18px;
    font-weight: 300;
    color: rgba(242,237,227,0.55);
    margin-bottom: 48px;
    max-width: 520px;
    line-height: 1.7;
}

.hero-cta-row {
    display: flex;
    gap: 16px;
    justify-content: center;
    align-items: center;
    flex-wrap: wrap;
}

.hero-scroll {
    position: absolute;
    bottom: 36px;
    left: 50%;
    transform: translateX(-50%);
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: rgba(242,237,227,0.25);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
}

.hero-scroll::after {
    content: '';
    width: 1px;
    height: 40px;
    background: linear-gradient(to bottom, rgba(242,237,227,0.25), transparent);
}

/* ═══════════════════════════════════════
   NEWSPAPER PREVIEW PLACEHOLDER
═══════════════════════════════════════ */
.preview-section {
    background: var(--cream);
    padding: 100px 48px;
    text-align: center;
}

.preview-label {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 4px;
    text-transform: uppercase;
    color: var(--green2);
    margin-bottom: 16px;
}

.preview-title {
    font-family: 'Playfair Display', serif;
    font-size: 48px;
    font-weight: 900;
    color: var(--ink);
    margin-bottom: 12px;
}

.preview-sub {
    font-size: 16px;
    color: var(--muted);
    margin-bottom: 56px;
    max-width: 480px;
    margin-left: auto;
    margin-right: auto;
}

.newspaper-mockup {
    max-width: 860px;
    margin: 0 auto;
    background: var(--white);
    border: 2px solid var(--ink);
    box-shadow: 12px 12px 0 var(--ink);
    overflow: hidden;
}

.mockup-masthead {
    background: var(--green);
    padding: 20px 32px 16px;
    text-align: center;
    border-bottom: 3px solid var(--brown);
}

.mockup-paper-name {
    font-family: 'Playfair Display', serif;
    font-size: 42px;
    font-weight: 900;
    color: var(--cream);
    letter-spacing: 4px;
    text-shadow: 2px 2px 0 rgba(0,0,0,0.4);
}

.mockup-edition {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px;
    letter-spacing: 3px;
    color: rgba(242,237,227,0.6);
    text-transform: uppercase;
    margin-top: 6px;
}

.mockup-headline {
    padding: 20px 32px 16px;
    border-bottom: 2px solid var(--ink);
    text-align: center;
}

.mockup-hl-text {
    font-family: 'Playfair Display', serif;
    font-size: 28px;
    font-weight: 900;
    text-transform: uppercase;
    color: var(--ink);
    line-height: 1.1;
}

.mockup-dateline {
    display: flex;
    justify-content: space-between;
    padding: 6px 32px;
    border-bottom: 1px solid #ccc;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px;
    letter-spacing: 1px;
    color: #666;
    text-transform: uppercase;
}

.mockup-body {
    display: grid;
    grid-template-columns: 1fr 2fr 1fr;
    gap: 0;
    padding: 20px 32px;
    min-height: 200px;
}

.mockup-col {
    padding: 0 16px;
    border-right: 1px solid #e0e0e0;
}

.mockup-col:first-child { padding-left: 0; }
.mockup-col:last-child { border-right: none; padding-right: 0; }

.mockup-col-label {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 9px;
    font-weight: 800;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--green2);
    border-bottom: 1px solid var(--ink);
    padding-bottom: 4px;
    margin-bottom: 10px;
}

.mockup-story-title {
    font-family: 'Playfair Display', serif;
    font-size: 13px;
    font-weight: 700;
    color: var(--ink);
    line-height: 1.2;
    margin-bottom: 4px;
    text-transform: uppercase;
}

.mockup-story-teaser {
    font-size: 11px;
    color: var(--brown);
    font-style: italic;
    margin-bottom: 4px;
    line-height: 1.3;
}

.mockup-story-score {
    font-size: 10px;
    color: var(--muted);
}

.mockup-center-img {
    background: linear-gradient(135deg, #2d5016 0%, #1e3a0f 100%);
    height: 120px;
    display: flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 12px;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 2px;
    color: rgba(242,237,227,0.4);
    text-transform: uppercase;
}

.mockup-lead-text {
    font-size: 11px;
    line-height: 1.6;
    color: var(--ink2);
}

.mockup-standings-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 0;
    border-bottom: 1px solid #f0f0f0;
    font-size: 10px;
}

.mockup-standings-name {
    font-weight: 600;
    color: var(--ink);
}

.mockup-standings-pts {
    color: var(--green2);
    font-weight: 700;
    font-family: 'Barlow Condensed', sans-serif;
}

.mockup-watermark {
    text-align: center;
    padding: 12px;
    background: var(--cream2);
    border-top: 1px solid var(--border);
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 10px;
    letter-spacing: 2px;
    color: var(--muted);
    text-transform: uppercase;
}

/* ═══════════════════════════════════════
   HOW IT WORKS
═══════════════════════════════════════ */
.how-section {
    background: var(--ink);
    padding: 100px 48px;
    position: relative;
}

.how-section::before {
    content: '';
    position: absolute;
    inset: 0;
    background-image: repeating-linear-gradient(
        0deg, transparent, transparent 28px,
        rgba(255,255,255,0.015) 28px,
        rgba(255,255,255,0.015) 29px
    );
    pointer-events: none;
}

.section-eyebrow {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 4px;
    text-transform: uppercase;
    color: var(--brown);
    text-align: center;
    margin-bottom: 16px;
}

.section-title-dark {
    font-family: 'Playfair Display', serif;
    font-size: 48px;
    font-weight: 900;
    color: var(--cream);
    text-align: center;
    margin-bottom: 12px;
    line-height: 1.1;
}

.section-sub-dark {
    font-size: 16px;
    color: rgba(242,237,227,0.45);
    text-align: center;
    margin-bottom: 72px;
    max-width: 480px;
    margin-left: auto;
    margin-right: auto;
}

.steps-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 2px;
    max-width: 900px;
    margin: 0 auto;
}

.step-card {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.07);
    padding: 40px 32px;
    position: relative;
}

.step-num {
    font-family: 'Playfair Display', serif;
    font-size: 72px;
    font-weight: 900;
    color: rgba(45,80,22,0.4);
    line-height: 1;
    margin-bottom: 20px;
}

.step-title {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: var(--cream);
    margin-bottom: 12px;
}

.step-text {
    font-size: 14px;
    line-height: 1.7;
    color: rgba(242,237,227,0.45);
}

/* ═══════════════════════════════════════
   FEATURES
═══════════════════════════════════════ */
.features-section {
    background: var(--green);
    padding: 100px 48px;
}

.features-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 20px;
    max-width: 1000px;
    margin: 56px auto 0;
}

.feature-card {
    background: rgba(0,0,0,0.2);
    border: 1px solid rgba(255,255,255,0.08);
    padding: 32px 28px;
}

.feature-icon {
    font-size: 28px;
    margin-bottom: 16px;
}

.feature-title {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 16px;
    font-weight: 800;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: var(--cream);
    margin-bottom: 10px;
}

.feature-text {
    font-size: 13px;
    line-height: 1.65;
    color: rgba(242,237,227,0.5);
}

/* ═══════════════════════════════════════
   BOTTOM CTA
═══════════════════════════════════════ */
.cta-section {
    background: var(--cream);
    padding: 120px 48px;
    text-align: center;
    border-top: 4px solid var(--ink);
}

.cta-title {
    font-family: 'Playfair Display', serif;
    font-size: clamp(40px, 5vw, 72px);
    font-weight: 900;
    color: var(--ink);
    line-height: 1.05;
    margin-bottom: 16px;
}

.cta-title em { color: var(--brown); font-style: italic; }

.cta-sub {
    font-size: 16px;
    color: var(--muted);
    margin-bottom: 48px;
    max-width: 400px;
    margin-left: auto;
    margin-right: auto;
}

/* ═══════════════════════════════════════
   FOOTER
═══════════════════════════════════════ */
.site-footer {
    background: var(--ink);
    padding: 32px 60px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-top: 1px solid rgba(255,255,255,0.06);
}

.footer-logo {
    font-family: 'Playfair Display', serif;
    font-size: 16px;
    font-weight: 900;
    color: rgba(242,237,227,0.3);
    letter-spacing: 3px;
}

.footer-logo em { color: var(--brown); font-style: normal; opacity: 0.5; }

.footer-copy {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px;
    letter-spacing: 1.5px;
    color: rgba(242,237,227,0.2);
    text-transform: uppercase;
}

/* ═══════════════════════════════════════
   MODAL OVERLAY
═══════════════════════════════════════ */
.modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(10,10,10,0.85);
    backdrop-filter: blur(8px);
    z-index: 1000;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
}

.modal-box {
    background: var(--cream);
    width: 100%;
    max-width: 420px;
    border-top: 4px solid var(--brown);
    padding: 40px 36px 36px;
    position: relative;
    box-shadow: 0 32px 80px rgba(0,0,0,0.6);
}

.modal-logo {
    font-family: 'Playfair Display', serif;
    font-size: 28px;
    font-weight: 900;
    color: var(--ink);
    letter-spacing: 4px;
    text-align: center;
    margin-bottom: 4px;
}

.modal-logo em { color: var(--brown); font-style: normal; }

.modal-tagline {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--muted);
    text-align: center;
    margin-bottom: 28px;
}

.modal-rule {
    height: 1px;
    background: var(--border);
    margin-bottom: 24px;
}

.google-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    background: var(--white);
    border: 1.5px solid var(--border);
    padding: 13px 20px;
    font-family: 'Barlow', sans-serif;
    font-size: 14px;
    font-weight: 600;
    color: var(--ink);
    text-decoration: none;
    width: 100%;
    margin-bottom: 18px;
    transition: all 0.15s;
}

.google-btn:hover {
    border-color: var(--green2);
    background: var(--cream2);
}

.divider-or {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 18px;
}

.divider-line { flex: 1; height: 1px; background: var(--border); }
.divider-text {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 2px;
    color: var(--muted);
    text-transform: uppercase;
}

/* ═══════════════════════════════════════
   APP PAGES (post-login)
═══════════════════════════════════════ */
.app-wrap { background: var(--cream) !important; min-height: 100vh; }

.app-nav {
    background: var(--green);
    height: 60px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 48px;
    border-bottom: 3px solid var(--brown);
}

.app-nav-logo {
    font-family: 'Playfair Display', serif;
    font-size: 20px;
    font-weight: 900;
    color: var(--cream);
    letter-spacing: 4px;
}

.app-nav-logo em { color: var(--brown); font-style: normal; }

.app-nav-user {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: rgba(242,237,227,0.5);
}

.page-hdr {
    background: var(--white);
    border-bottom: 2px solid var(--border);
    padding: 32px 48px 24px;
    margin-bottom: 36px;
}

.page-hdr-eyebrow {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--green2);
    margin-bottom: 6px;
}

.page-hdr-title {
    font-family: 'Playfair Display', serif;
    font-size: 40px;
    font-weight: 900;
    color: var(--ink);
    line-height: 1;
    margin-bottom: 6px;
}

.page-hdr-sub { font-size: 14px; color: var(--muted); }

.cw { max-width: 960px; margin: 0 auto; padding: 0 48px 64px; }

.league-card {
    background: var(--white);
    border: 1.5px solid var(--border);
    border-left: 5px solid var(--green2);
    padding: 22px 26px 18px;
    margin-bottom: 6px;
    transition: box-shadow 0.2s, border-left-color 0.2s;
}

.league-card:hover {
    box-shadow: 0 4px 24px rgba(45,80,22,0.10);
    border-left-color: var(--brown);
}

.league-card-name {
    font-family: 'Playfair Display', serif;
    font-size: 22px;
    font-weight: 700;
    color: var(--ink);
    margin-bottom: 4px;
}

.league-card-meta {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: var(--muted);
}

.sec-label {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--green2);
    padding-bottom: 8px;
    border-bottom: 2px solid var(--ink);
    margin-bottom: 18px;
}

.joke-pill {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: var(--cream2);
    border: 1px solid var(--border);
    padding: 7px 14px;
    margin: 4px 6px 4px 0;
    font-size: 13px;
    color: var(--ink2);
}

.empty-state {
    text-align: center;
    padding: 72px 24px;
    background: var(--white);
    border: 1.5px dashed var(--border);
}

.empty-icon { font-size: 48px; margin-bottom: 16px; }

.empty-title {
    font-family: 'Playfair Display', serif;
    font-size: 24px;
    color: var(--ink);
    margin-bottom: 10px;
}

.empty-text { font-size: 14px; color: var(--muted); max-width: 320px; margin: 0 auto 24px; line-height: 1.6; }

.hrule { border: none; border-top: 1px solid var(--border); margin: 24px 0; }

/* ── Streamlit overrides ── */
.stButton > button {
    font-family: 'Barlow Condensed', sans-serif !important;
    font-size: 13px !important;
    font-weight: 800 !important;
    letter-spacing: 2px !important;
    text-transform: uppercase !important;
    border-radius: 0 !important;
}

.stButton > button[kind="primary"] {
    background: var(--green2) !important;
    border: none !important;
    color: var(--cream) !important;
}

.stButton > button[kind="primary"]:hover { background: var(--green3) !important; }

.stButton > button[kind="secondary"] {
    background: transparent !important;
    border: 1.5px solid var(--border) !important;
    color: var(--ink) !important;
}

.stButton > button[kind="secondary"]:hover {
    border-color: var(--green2) !important;
    color: var(--green2) !important;
}

.stTextInput > div > div > input,
.stTextArea > div > div > textarea,
.stNumberInput > div > div > input {
    border-radius: 0 !important;
    border-color: var(--border) !important;
    font-family: 'Barlow', sans-serif !important;
    background: var(--white) !important;
}

.stTextInput > div > div > input:focus,
.stTextArea > div > div > textarea:focus {
    border-color: var(--green2) !important;
    box-shadow: 0 0 0 1px var(--green2) !important;
}

.stTabs [data-baseweb="tab"] {
    font-family: 'Barlow Condensed', sans-serif !important;
    font-weight: 800 !important;
    font-size: 12px !important;
    letter-spacing: 2px !important;
    text-transform: uppercase !important;
}

.stTabs [aria-selected="true"] { color: var(--green2) !important; }
.stTabs [data-baseweb="tab-highlight"] { background: var(--green2) !important; }
div[data-testid="stForm"] { border: none !important; padding: 0 !important; }
.stProgress > div > div { background: var(--green2) !important; }
label[data-testid="stWidgetLabel"] p {
    font-family: 'Barlow Condensed', sans-serif !important;
    font-size: 10px !important;
    font-weight: 800 !important;
    letter-spacing: 2px !important;
    text-transform: uppercase !important;
    color: var(--ink2) !important;
}
.stDownloadButton > button {
    background: var(--brown) !important;
    color: var(--white) !important;
    border: none !important;
    font-family: 'Barlow Condensed', sans-serif !important;
    font-weight: 800 !important;
    letter-spacing: 2px !important;
    text-transform: uppercase !important;
    border-radius: 0 !important;
}
</style>
""", unsafe_allow_html=True)


# ─── Session ───────────────────────────────────────────────
def get_user(): return st.session_state.get("user")
def is_logged_in(): return get_user() is not None

def set_auth(res):
    st.session_state["user"] = res.user
    st.session_state["session"] = res.session
    if res.session:
        supabase.auth.set_session(res.session.access_token, res.session.refresh_token)

def sign_out():
    try: supabase.auth.sign_out()
    except Exception: pass
    st.session_state.clear()
    st.rerun()


# ─── Auth ──────────────────────────────────────────────────
def sign_up(email, password):
    try:
        res = supabase.auth.sign_up({"email": email, "password": password})
        if res.user:
            set_auth(res)
            return True, None
        return False, "Sign up failed."
    except Exception as e:
        return False, str(e)

def sign_in(email, password):
    try:
        res = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if res.user:
            set_auth(res)
            return True, None
        return False, "Invalid email or password."
    except Exception:
        return False, "Invalid email or password."

def get_google_oauth_url():
    try:
        res = supabase.auth.sign_in_with_oauth({
            "provider": "google",
            "options": {"redirect_to": "http://localhost:8501"}
        })
        return res.url
    except Exception:
        return None

def handle_oauth_callback():
    params = st.query_params
    at = params.get("access_token")
    rt = params.get("refresh_token")
    if at and rt:
        try:
            res = supabase.auth.set_session(at, rt)
            if res.user:
                st.session_state["user"] = res.user
                st.session_state["session"] = res.session
                st.query_params.clear()
                return True
        except Exception:
            pass
    return False


# ─── DB ────────────────────────────────────────────────────
def get_user_leagues():
    user = get_user()
    if not user: return []
    try:
        return supabase.table("leagues").select("*").eq("user_id", user.id).execute().data or []
    except Exception: return []

def save_league(platform_league_id, league_name, paper_name, commissioner, season,
                provider="sleeper"):
    user = get_user()
    if not user: return None
    row = {
        "user_id": user.id,
        # Kept for backwards compatibility with rows written before the
        # provider layer existed. See migrations/001_add_provider.sql.
        "sleeper_league_id": platform_league_id,
        "platform_league_id": platform_league_id,
        "provider": provider,
        "league_name": league_name,
        "paper_name": paper_name or f"The {league_name} Times",
        "commissioner_name": commissioner,
        "season": season,
    }
    try:
        res = supabase.table("leagues").insert(row).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        # If the migration hasn't been run yet, retry without the new columns
        # so the app keeps working instead of hard-failing on Add League.
        if "provider" in str(e) or "platform_league_id" in str(e):
            legacy = {k: v for k, v in row.items()
                      if k not in ("provider", "platform_league_id")}
            try:
                res = supabase.table("leagues").insert(legacy).execute()
                st.warning(
                    "Saved, but your `leagues` table is missing the `provider` "
                    "and `platform_league_id` columns. Run "
                    "migrations/001_add_provider.sql to enable other platforms."
                )
                return res.data[0] if res.data else None
            except Exception as inner:
                st.error(f"Error saving league: {inner}")
                return None
        st.error(f"Error saving league: {e}")
        return None

def get_jokes(league_id):
    try:
        return supabase.table("inside_jokes").select("*")\
            .eq("league_id", league_id).eq("active", True)\
            .order("created_at").execute().data or []
    except Exception: return []

def add_joke(league_id, text):
    try:
        supabase.table("inside_jokes").insert({"league_id": league_id, "joke": text}).execute()
        return True
    except Exception: return False

def delete_joke(joke_id):
    try:
        supabase.table("inside_jokes").update({"active": False}).eq("id", joke_id).execute()
        return True
    except Exception: return False

def save_newspaper(league_id, week, season, html, ai_cache):
    try:
        existing = supabase.table("newspapers").select("id")\
            .eq("league_id", league_id).eq("week", week).eq("season", season).execute()
        if existing.data:
            supabase.table("newspapers").update({
                "html_content": html, "ai_cache": ai_cache,
            }).eq("id", existing.data[0]["id"]).execute()
        else:
            supabase.table("newspapers").insert({
                "league_id": league_id, "week": week, "season": season,
                "html_content": html,
                "ai_cache": json.dumps(ai_cache) if ai_cache else None,
            }).execute()
        return True
    except Exception as e:
        st.error(f"Error saving: {e}"); return False

def get_saved_newspapers(league_id):
    try:
        return supabase.table("newspapers").select("id, week, season, generated_at")\
            .eq("league_id", league_id).order("week", desc=True).execute().data or []
    except Exception: return []


# ─── Landing page ──────────────────────────────────────────
def page_landing():
    google_url = get_google_oauth_url()
    show_modal = st.session_state.get("show_modal", False)

    # NAV
    st.markdown("""
    <div class="land-nav">
        <div class="land-nav-logo"><em>C</em>OMMISH</div>
        <div class="land-nav-links">
            <div class="land-nav-link">How It Works</div>
            <div class="land-nav-link">Features</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Sign in button in nav — using Streamlit button positioned via columns
    # We use a hack: render a floating button using HTML + a Streamlit trigger
    _, _, nav_col = st.columns([6, 2, 1])
    with nav_col:
        if st.button("Sign In", key="nav_signin", type="primary"):
            st.session_state["show_modal"] = True
            st.rerun()

    # HERO
    st.markdown("""
    <div class="hero">
        <div class="hero-eyebrow">Introducing Commish</div>
        <div class="hero-headline">
            Your fantasy league<br>deserves a <em>newspaper.</em>
        </div>
        <div class="hero-sub">
            Weekly AI-written newspapers for your fantasy football league.
            Brutal recaps, power rankings, inside jokes, and more — generated in seconds.
        </div>
        <div class="hero-scroll">Scroll</div>
    </div>
    """, unsafe_allow_html=True)

    # CTA button under hero
    _, c, _ = st.columns([2, 1, 2])
    with c:
        if st.button("🗞️  Get Started — It's Free", key="hero_cta", type="primary", use_container_width=True):
            st.session_state["show_modal"] = True
            st.rerun()

    # NEWSPAPER MOCKUP
    st.markdown("""
    <div class="preview-section">
        <div class="preview-label">See What You Get</div>
        <div class="preview-title">A real newspaper. Every week.</div>
        <div class="preview-sub">AI-written recaps that sound like they were written by a deranged sports columnist who knows your league personally.</div>

        <div class="newspaper-mockup">
            <div class="mockup-masthead">
                <div class="mockup-paper-name">THE KEVLARVILLE TIMES</div>
                <div class="mockup-edition">Week 1 Edition &nbsp;•&nbsp; Kevlarville &nbsp;•&nbsp; September 7, 2025</div>
            </div>
            <div class="mockup-headline">
                <div class="mockup-hl-text">KEVLARVILLE IS BACK AND SOMEBODY ALREADY NEEDS JESUS</div>
            </div>
            <div class="mockup-dateline">
                <span>Week 1</span>
                <span>washeduphasbeen44 survived by 1.9 • johnhenryhammond won by 12.6 • HankStocke led with 135.2</span>
                <span>September 7, 2025</span>
            </div>
            <div class="mockup-body">
                <div class="mockup-col">
                    <div class="mockup-col-label">This Week</div>
                    <div class="mockup-story-title">johnhenryhammond def. champayyy</div>
                    <div class="mockup-story-teaser">The brilliant Commissioner crushes Satan in a holy reckoning</div>
                    <div class="mockup-story-score">120.4 — 107.8 | margin: 12.6</div>
                    <br/>
                    <div class="mockup-story-title">HankStocke def. mikevidan3</div>
                    <div class="mockup-story-teaser">Hank draws blood in Week One</div>
                    <div class="mockup-story-score">135.2 — 126.4 | margin: 8.7</div>
                    <br/>
                    <div class="mockup-story-title">Jagan34 def. Audobo</div>
                    <div class="mockup-story-teaser">Josh Allen commits grand larceny</div>
                    <div class="mockup-story-score">133.0 — 122.7 | margin: 10.2</div>
                </div>
                <div class="mockup-col">
                    <div class="mockup-center-img">Your Meme Here</div>
                    <div class="mockup-lead-text">Welcome to Week 1 of the Kevlarville season, a week that began with hope, ended in tears, and somewhere in the middle featured a 1.9-point margin of victory that should be classified as a federal hate crime. HankStocke led the league with a 135.16 — a number so obscene it should require a permit — while champayyy, allegedly a person who prepared for this draft, limped across the finish line at 107.78 points...</div>
                </div>
                <div class="mockup-col">
                    <div class="mockup-col-label">Standings</div>
                    <div class="mockup-standings-row"><span class="mockup-standings-name">HankStocke</span><span class="mockup-standings-pts">135.2</span></div>
                    <div class="mockup-standings-row"><span class="mockup-standings-name">Jagan34</span><span class="mockup-standings-pts">133.0</span></div>
                    <div class="mockup-standings-row"><span class="mockup-standings-name">superchaser</span><span class="mockup-standings-pts">132.8</span></div>
                    <div class="mockup-standings-row"><span class="mockup-standings-name">mikevidan3</span><span class="mockup-standings-pts">126.4</span></div>
                    <div class="mockup-standings-row"><span class="mockup-standings-name">Audobo</span><span class="mockup-standings-pts">122.7</span></div>
                    <div class="mockup-standings-row"><span class="mockup-standings-name">nickarrowood</span><span class="mockup-standings-pts">122.3</span></div>
                    <div class="mockup-standings-row"><span class="mockup-standings-name">johnhenry</span><span class="mockup-standings-pts">120.4</span></div>
                    <div class="mockup-standings-row"><span class="mockup-standings-name">washedup</span><span class="mockup-standings-pts">111.4</span></div>
                </div>
            </div>
            <div class="mockup-watermark">Generated by Commish &nbsp;•&nbsp; commish.app</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # HOW IT WORKS
    st.markdown("""
    <div class="how-section">
        <div class="section-eyebrow">How It Works</div>
        <div class="section-title-dark">Up and running in minutes.</div>
        <div class="section-sub-dark">No spreadsheets. No setup. Just your league ID and a willingness to roast your friends.</div>
        <div class="steps-grid">
            <div class="step-card">
                <div class="step-num">01</div>
                <div class="step-title">Connect Your League</div>
                <div class="step-text">Paste your Sleeper League ID. Commish pulls your rosters, scores, and matchups automatically. Takes 10 seconds.</div>
            </div>
            <div class="step-card">
                <div class="step-num">02</div>
                <div class="step-title">Add Inside Jokes</div>
                <div class="step-text">Tell Commish about your league's lore — who always chokes, who the commissioner is, what the running jokes are. These become permanent.</div>
            </div>
            <div class="step-card">
                <div class="step-num">03</div>
                <div class="step-title">Generate Every Week</div>
                <div class="step-text">Hit generate after Sunday's games. Get a full newspaper in about 20 seconds. Download it, share it, destroy friendships with it.</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # FEATURES
    st.markdown("""
    <div class="features-section">
        <div class="section-eyebrow" style="color:rgba(242,237,227,0.4);">What's Inside</div>
        <div class="section-title-dark">Everything your league needs.</div>
        <div class="features-grid">
            <div class="feature-card">
                <div class="feature-icon">📰</div>
                <div class="feature-title">Full Newspaper Layout</div>
                <div class="feature-text">Front page with standings, game stories, honor roll, power rankings, fraud watch, and weekly awards.</div>
            </div>
            <div class="feature-card">
                <div class="feature-icon">🤖</div>
                <div class="feature-title">AI Writing</div>
                <div class="feature-text">Written in the voice of a brutal, football-obsessed columnist. Every story is different, every insult is earned.</div>
            </div>
            <div class="feature-card">
                <div class="feature-icon">💬</div>
                <div class="feature-title">Inside Jokes</div>
                <div class="feature-text">Save your league's lore and it gets woven into every edition. Running gags that build all season long.</div>
            </div>
            <div class="feature-card">
                <div class="feature-icon">🏆</div>
                <div class="feature-title">Weekly Awards</div>
                <div class="feature-text">Gardner Minshew, Joe Burrow, Kyle Pitts, and Jerry Jones awards given out to the deserving every week.</div>
            </div>
            <div class="feature-card">
                <div class="feature-icon">📊</div>
                <div class="feature-title">Honor Roll & Detention</div>
                <div class="feature-text">Top performers get celebrated. Biggest projection misses get buried. With player headshots from Sleeper.</div>
            </div>
            <div class="feature-card">
                <div class="feature-icon">🗂️</div>
                <div class="feature-title">Full Archive</div>
                <div class="feature-text">Every newspaper saved automatically. Go back and revisit the week your kicker cost you the championship.</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # BOTTOM CTA
    st.markdown("""
    <div class="cta-section">
        <div class="cta-title">Ready to roast<br>your <em>league?</em></div>
        <div class="cta-sub">Free to start. No credit card. Just your league ID and a sense of humor.</div>
    </div>
    """, unsafe_allow_html=True)

    _, c2, _ = st.columns([2, 1, 2])
    with c2:
        if st.button("🗞️  Start For Free", key="bottom_cta", type="primary", use_container_width=True):
            st.session_state["show_modal"] = True
            st.rerun()

    # FOOTER
    st.markdown("""
    <div class="site-footer">
        <div class="footer-logo"><em>C</em>OMMISH</div>
        <div class="footer-copy">© 2025 Commish &nbsp;•&nbsp; All rights reserved</div>
    </div>
    """, unsafe_allow_html=True)

    # ── MODAL ──
    if show_modal:
        st.markdown('<div class="modal-overlay">', unsafe_allow_html=True)
        st.markdown("""
        <div class="modal-box">
            <div class="modal-logo"><em>C</em>OMMISH</div>
            <div class="modal-tagline">Fantasy Football Newspapers</div>
            <div class="modal-rule"></div>
        </div>
        """, unsafe_allow_html=True)

        if google_url:
            st.markdown(f"""
            <a href="{google_url}" target="_self" class="google-btn">
                <img src="https://www.google.com/favicon.ico" width="16" />
                Continue with Google
            </a>
            <div class="divider-or">
                <div class="divider-line"></div>
                <div class="divider-text">or</div>
                <div class="divider-line"></div>
            </div>
            """, unsafe_allow_html=True)

        tab_in, tab_up = st.tabs(["Sign In", "Sign Up"])

        with tab_in:
            with st.form("modal_signin"):
                email = st.text_input("Email", placeholder="you@example.com", key="ms_email")
                pw = st.text_input("Password", type="password", key="ms_pw")
                c1, c2 = st.columns(2)
                with c1:
                    if st.form_submit_button("Sign In", use_container_width=True, type="primary"):
                        if email and pw:
                            ok, err = sign_in(email, pw)
                            if ok:
                                st.session_state["show_modal"] = False
                                st.session_state["page"] = "dashboard"
                                st.rerun()
                            else:
                                st.error(err)
                        else:
                            st.error("Please fill in all fields.")
                with c2:
                    if st.form_submit_button("Cancel", use_container_width=True):
                        st.session_state["show_modal"] = False
                        st.rerun()

        with tab_up:
            with st.form("modal_signup"):
                email = st.text_input("Email", placeholder="you@example.com", key="msu_e")
                pw = st.text_input("Password", type="password", placeholder="Min. 6 characters", key="msu_p")
                pw2 = st.text_input("Confirm Password", type="password", key="msu_p2")
                c1, c2 = st.columns(2)
                with c1:
                    if st.form_submit_button("Create Account", use_container_width=True, type="primary"):
                        if not email or not pw:
                            st.error("Please fill in all fields.")
                        elif pw != pw2:
                            st.error("Passwords don't match.")
                        elif len(pw) < 6:
                            st.error("Min. 6 characters.")
                        else:
                            ok, err = sign_up(email, pw)
                            if ok:
                                st.session_state["show_modal"] = False
                                st.session_state["page"] = "dashboard"
                                st.rerun()
                            else:
                                st.error(err)
                with c2:
                    if st.form_submit_button("Cancel", use_container_width=True):
                        st.session_state["show_modal"] = False
                        st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)


# ─── App pages (post-login) ────────────────────────────────
def render_app_nav(back_page=None, back_label="Dashboard"):
    user = get_user()
    email = user.email if user else ""
    short = (email[:20] + "…") if len(email) > 20 else email
    st.markdown(f"""
    <div class="app-nav">
        <div class="app-nav-logo"><em>C</em>OMMISH</div>
        <div class="app-nav-user">{short}</div>
    </div>
    """, unsafe_allow_html=True)
    if back_page:
        if st.button(f"← {back_label}", key="back_btn"):
            st.session_state["page"] = back_page
            st.rerun()


def page_dashboard():
    render_app_nav()
    leagues = get_user_leagues()

    st.markdown("""
    <div class="page-hdr">
        <div class="page-hdr-eyebrow">Dashboard</div>
        <div class="page-hdr-title">Your Leagues</div>
        <div class="page-hdr-sub">Generate newspapers, manage inside jokes, and browse past editions</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="cw">', unsafe_allow_html=True)

    _, c2 = st.columns([5, 1])
    with c2:
        if st.button("+ Add League", type="primary", use_container_width=True):
            st.session_state["page"] = "add_league"; st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    if not leagues:
        st.markdown("""
        <div class="empty-state">
            <div class="empty-icon">🏈</div>
            <div class="empty-title">No leagues yet</div>
            <div class="empty-text">Connect your Sleeper fantasy league to start generating weekly newspapers.</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        for league in leagues:
            paper = league.get("paper_name") or f"The {league.get('league_name')} Times"
            meta = f"{league.get('league_name')}  ·  {league.get('season')} Season"
            st.markdown(f"""
            <div class="league-card">
                <div class="league-card-name">{paper}</div>
                <div class="league-card-meta">{meta}</div>
            </div>
            """, unsafe_allow_html=True)
            c1, c2, c3 = st.columns(3)
            with c1:
                if st.button("📰  Generate", key=f"g_{league['id']}", use_container_width=True, type="primary"):
                    st.session_state["active_league"] = league
                    st.session_state["page"] = "generate"; st.rerun()
            with c2:
                if st.button("⚙️  Settings & Jokes", key=f"s_{league['id']}", use_container_width=True):
                    st.session_state["active_league"] = league
                    st.session_state["page"] = "league_settings"; st.rerun()
            with c3:
                saved = get_saved_newspapers(league["id"])
                lbl = f"📁  History ({len(saved)})" if saved else "📁  History"
                if st.button(lbl, key=f"h_{league['id']}", use_container_width=True):
                    st.session_state["active_league"] = league
                    st.session_state["page"] = "history"; st.rerun()
            st.markdown("<div class='hrule'></div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("<br>")
    _, c, _ = st.columns([4, 1, 4])
    with c:
        if st.button("Sign Out", use_container_width=True):
            sign_out()


def page_add_league():
    render_app_nav(back_page="dashboard")
    st.markdown("""
    <div class="page-hdr">
        <div class="page-hdr-eyebrow">Setup</div>
        <div class="page-hdr-title">Connect a League</div>
        <div class="page-hdr-sub">Find your Sleeper League ID in the Sleeper app under League → Settings → League Info</div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown('<div class="cw">', unsafe_allow_html=True)

    from providers import available_providers, get_provider

    platforms = [p for p in available_providers() if p["implemented"]]
    platform_labels = {p["display_name"]: p["name"] for p in platforms}

    with st.form("add_league"):
        st.markdown('<div class="sec-label">League Info</div>', unsafe_allow_html=True)

        if len(platform_labels) > 1:
            platform_label = st.selectbox("Platform", list(platform_labels))
        else:
            platform_label = next(iter(platform_labels))
        provider_name = platform_labels[platform_label]

        platform_id = st.text_input(
            f"{platform_label} League ID", placeholder="e.g. 1252396303246176256"
        )
        commissioner = st.text_input(f"Commissioner's {platform_label} Username")
        c1, c2 = st.columns(2)
        with c1:
            season = st.number_input("Season Year", min_value=2020, max_value=2030, value=2025)
        with c2:
            custom_paper = st.text_input("Custom Paper Name", placeholder="e.g. The Kevlarville Times")
        st.markdown("<br>", unsafe_allow_html=True)
        if st.form_submit_button("Connect League", use_container_width=True, type="primary"):
            if not platform_id or not commissioner:
                st.error("League ID and Commissioner username are required.")
            else:
                with st.spinner(f"Connecting to {platform_label}..."):
                    try:
                        provider = get_provider(provider_name)
                        name = provider.verify_league(platform_id.strip(), int(season))
                        if not name:
                            st.error("League not found. Check your League ID.")
                        else:
                            paper = custom_paper.strip() or f"The {name} Times"
                            saved = save_league(
                                platform_id.strip(), name, paper, commissioner,
                                int(season), provider=provider_name,
                            )
                            if saved:
                                st.success(f"✅ Connected **{name}**!")
                                st.session_state["page"] = "dashboard"; st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

    st.markdown("</div>", unsafe_allow_html=True)


def page_league_settings():
    league = st.session_state.get("active_league")
    if not league:
        st.session_state["page"] = "dashboard"; st.rerun(); return

    render_app_nav(back_page="dashboard")
    paper = league.get("paper_name") or league.get("league_name")

    st.markdown(f"""
    <div class="page-hdr">
        <div class="page-hdr-eyebrow">Settings</div>
        <div class="page-hdr-title">{paper}</div>
        <div class="page-hdr-sub">Inside jokes and league preferences</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="cw">', unsafe_allow_html=True)
    st.markdown('<div class="sec-label">Inside Jokes</div>', unsafe_allow_html=True)
    st.caption("Injected into the AI prompt every time you generate. They become recurring jokes across the season.")

    jokes = get_jokes(league["id"])
    if jokes:
        for joke in jokes:
            c1, c2 = st.columns([7, 1])
            with c1:
                st.markdown(f'<div class="joke-pill">💬 {joke["joke"]}</div>', unsafe_allow_html=True)
            with c2:
                if st.button("Remove", key=f"d_{joke['id']}"):
                    delete_joke(joke["id"]); st.rerun()
        st.markdown("<br>", unsafe_allow_html=True)
    else:
        st.info("No inside jokes yet. Add your first one below.")

    with st.form("add_joke"):
        new_joke = st.text_area("New inside joke",
            placeholder="Be specific:\n• Nick always leaves his best player on the bench\n• Champ Hammond sold his soul for a slightly above average roster",
            height=100)
        if st.form_submit_button("Add Joke", use_container_width=True):
            if new_joke.strip():
                add_joke(league["id"], new_joke.strip()); st.rerun()
            else:
                st.error("Please enter a joke first.")

    st.markdown("<div class='hrule'></div>", unsafe_allow_html=True)
    st.markdown('<div class="sec-label">Paper Settings</div>', unsafe_allow_html=True)

    with st.form("league_settings"):
        pname = st.text_input("Paper Name", value=league.get("paper_name") or "")
        comm = st.text_input("Commissioner Username", value=league.get("commissioner_name") or "")
        if st.form_submit_button("Save Settings", use_container_width=True):
            try:
                supabase.table("leagues").update({
                    "paper_name": pname, "commissioner_name": comm,
                }).eq("id", league["id"]).execute()
                st.success("✅ Saved!")
                st.session_state["active_league"]["paper_name"] = pname
                st.session_state["active_league"]["commissioner_name"] = comm
            except Exception as e:
                st.error(f"Error: {e}")

    st.markdown("</div>", unsafe_allow_html=True)


def page_generate():
    league = st.session_state.get("active_league")
    if not league:
        st.session_state["page"] = "dashboard"; st.rerun(); return

    render_app_nav(back_page="dashboard")
    paper = league.get("paper_name") or f"The {league.get('league_name')} Times"

    st.markdown(f"""
    <div class="page-hdr">
        <div class="page-hdr-eyebrow">Generate</div>
        <div class="page-hdr-title">{paper}</div>
        <div class="page-hdr-sub">Pick a week and generate this week's newspaper</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="cw">', unsafe_allow_html=True)
    jokes = get_jokes(league["id"])

    if jokes:
        st.markdown(f"""
        <div style="background:var(--white);border:1.5px solid var(--border);border-left:4px solid var(--green2);
                    padding:14px 18px;margin-bottom:24px;font-size:14px;color:var(--ink2);">
            ✓ &nbsp;<strong>{len(jokes)} inside joke(s)</strong> will be included
        </div>
        """, unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        week = st.number_input("NFL Week", min_value=1, max_value=18, value=1)
    with c2:
        season = st.number_input("Season Year", min_value=2020, max_value=2030,
                                  value=league.get("season", 2025))

    st.markdown("<br>", unsafe_allow_html=True)

    if st.button("🗞️  Generate This Week's Newspaper", type="primary", use_container_width=True):
        _run_generation(league, int(week), int(season), jokes)

    st.markdown("</div>", unsafe_allow_html=True)


def _run_generation(league, week, season, jokes):
    import sys
    project_dir = os.path.dirname(os.path.abspath(__file__))
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)

    from providers import ProviderError, load_week, week_to_legacy_games
    from storylines import get_weekly_storylines
    from writer import generate_full_newspaper_content
    from newspaper import build_power_rankings_from_matchups, build_edition, render_html

    provider_name = league.get("provider") or "sleeper"
    league_id = league.get("platform_league_id") or league["sleeper_league_id"]
    commissioner = league.get("commissioner_name", "")
    paper_name = league.get("paper_name") or f"The {league['league_name']} Times"
    jokes_text = "\n".join(f"- {j['joke']}" for j in jokes) if jokes else ""

    prog = st.progress(0, text=f"Fetching league data from {provider_name.title()}...")

    try:
        # The provider layer handles fetching, pairing, projections and the
        # lineup optimizer. Swapping platforms is a one-word change here.
        week_data = load_week(provider_name, league_id, season, week)
        prog.progress(30, text="Reading the box scores...")

        games = week_to_legacy_games(week_data)
        summary = get_weekly_storylines(games)
        prog.progress(40, text="Writing with AI — ~20 seconds...")

        ai_content = generate_full_newspaper_content(
            league_name=paper_name, week=week, games=games,
            summary=summary, commissioner_name=commissioner,
            inside_jokes=jokes_text,
        )
        prog.progress(85, text="Rendering newspaper...")

        power_rankings = build_power_rankings_from_matchups(games)
        edition = build_edition(paper_name, week, summary, games, power_rankings, ai_content)
        edition["paper_name"] = paper_name
        html = render_html(edition)

        save_newspaper(league["id"], week, season, html, ai_content)
        prog.progress(100, text="Done!")
        st.success("✅ Newspaper generated!")
        st.markdown("<br>", unsafe_allow_html=True)
        st.download_button("⬇️  Download HTML", data=html,
            file_name=f"{paper_name.replace(' ','_')}_Week_{week}.html",
            mime="text/html", use_container_width=True)
        with st.expander("👁️  Preview", expanded=True):
            st.components.v1.html(html, height=900, scrolling=True)

    except ProviderError as e:
        # Expected, explainable failures: bad league ID, private league,
        # week hasn't been played yet. No stack trace needed.
        st.error(f"Couldn't load that week: {e}")
    except Exception as e:
        st.error(f"Generation failed: {e}")
        st.exception(e)


def page_history():
    league = st.session_state.get("active_league")
    if not league:
        st.session_state["page"] = "dashboard"; st.rerun(); return

    render_app_nav(back_page="dashboard")
    paper = league.get("paper_name") or league.get("league_name")

    st.markdown(f"""
    <div class="page-hdr">
        <div class="page-hdr-eyebrow">Archive</div>
        <div class="page-hdr-title">Past Newspapers</div>
        <div class="page-hdr-sub">{paper}</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="cw">', unsafe_allow_html=True)
    papers = get_saved_newspapers(league["id"])

    if not papers:
        st.markdown("""
        <div class="empty-state">
            <div class="empty-icon">📁</div>
            <div class="empty-title">No newspapers yet</div>
            <div class="empty-text">Generated newspapers will be saved here automatically.</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        for rec in papers:
            from datetime import datetime
            try:
                dt = datetime.fromisoformat(rec["generated_at"].replace("Z", "+00:00"))
                date_str = dt.strftime("%B %d, %Y")
            except Exception:
                date_str = ""
            c1, c2 = st.columns([5, 1])
            with c1:
                st.markdown(f"""
                <div style="padding:16px 20px;background:var(--white);border:1.5px solid var(--border);margin-bottom:6px;">
                    <div style="font-family:'Playfair Display',serif;font-size:20px;font-weight:700;">
                        Week {rec['week']} &nbsp;—&nbsp; {rec['season']} Season
                    </div>
                    <div style="font-size:13px;color:var(--muted);margin-top:2px;">Generated {date_str}</div>
                </div>
                """, unsafe_allow_html=True)
            with c2:
                if st.button("View", key=f"v_{rec['id']}", use_container_width=True):
                    try:
                        res = supabase.table("newspapers").select("html_content")\
                            .eq("id", rec["id"]).execute()
                        if res.data:
                            st.components.v1.html(res.data[0]["html_content"], height=900, scrolling=True)
                    except Exception as e:
                        st.error(f"Error: {e}")

    st.markdown("</div>", unsafe_allow_html=True)


# ─── Router ────────────────────────────────────────────────
def main():
    session = st.session_state.get("session")
    if session:
        try:
            supabase.auth.set_session(session.access_token, session.refresh_token)
        except Exception:
            pass

    if not is_logged_in():
        if handle_oauth_callback():
            st.session_state["page"] = "dashboard"
            st.rerun()
            return
        page_landing()
        return

    {
        "dashboard":       page_dashboard,
        "add_league":      page_add_league,
        "league_settings": page_league_settings,
        "generate":        page_generate,
        "history":         page_history,
    }.get(st.session_state.get("page", "dashboard"), page_dashboard)()


if __name__ == "__main__":
    main()