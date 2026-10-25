"""Every inline <script> in the templates must at least parse (25 Oct 2026:
one stray quote in compare.html stopped the whole Compare players page).
Uses `node --check`; skipped where node isn't installed."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parent.parent / "web" / "templates"
SCRIPT = re.compile(r"<script(?![^>]*application/json)[^>]*>(.*?)</script>", re.S)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_inline_scripts_parse(tmp_path):
    bad = []
    for page in sorted(TEMPLATES.glob("*.html")):
        for i, m in enumerate(SCRIPT.finditer(page.read_text(encoding="utf-8"))):
            js = re.sub(r"\{%.*?%\}", "", re.sub(r"\{\{.*?\}\}", "0", m.group(1), flags=re.S), flags=re.S)
            if not js.strip():
                continue
            f = tmp_path / f"{page.stem}_{i}.js"
            f.write_text(js, encoding="utf-8")
            r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
            if r.returncode:
                bad.append(f"{page.name} script {i}: {r.stderr.strip().splitlines()[-1]}")
    assert not bad, "\n".join(bad)
