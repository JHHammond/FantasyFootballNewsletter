"""
Diff a real ESPN response against what this codebase assumes.

    python -m tests.espn_diff captured.json

Prints three things:

  MISSING   — a path providers/espn.py reads that the real payload does not
              have. Each one is a concrete bug with a known location.
  PRESENT   — the load-bearing paths that check out.
  EXTRA     — top-level keys ESPN sent that the fixture does not model. Usually
              harmless, occasionally the thing you needed.

Then it runs the real payload through ESPNProvider and prints the parsed week,
because a response can satisfy every path check and still produce a paper with
everybody on zero points.

Written before any real response existed, so that validating this adapter is
running one command rather than reading four hundred lines next to a browser
tab.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import fixtures_espn  # noqa: E402


def _walk(payload, path: str):
    """Resolve a dotted path, where [] means "any item in this list".

    ANY, not "the first". The first version took item zero and reported
    schedule[].home.rosterForCurrentScoringPeriod.entries as missing from a
    payload that had it — because schedule[0] is week 1, which carries no
    roster, and the roster lives on the week being reported. A diagnostic that
    cries wolf on its own first run is worse than no diagnostic.

    Returns (found, sample).
    """
    parts = path.split(".")

    def descend(node, remaining):
        if not remaining:
            return True, node

        part, rest = remaining[0], remaining[1:]
        listy = part.endswith("[]")
        key = part[:-2] if listy else part

        if key:
            if isinstance(node, list):
                # A path written entries[].x applied to a bare list.
                for item in node:
                    ok, sample = descend(item, remaining)
                    if ok:
                        return True, sample
                return False, None
            if not isinstance(node, dict) or key not in node:
                return False, None
            node = node[key]

        if listy:
            if not isinstance(node, list):
                return False, None
            for item in node:
                ok, sample = descend(item, rest)
                if ok:
                    return True, sample
            return False, None

        return descend(node, rest)

    return descend(payload, parts)


def _entry_paths(payload):
    """The entry-level paths need a roster to resolve against.

    They are written as entries[].x rather than the full thirty-character path
    because they are read in two different places.
    """
    for row in payload.get("schedule") or []:
        for side in ("home", "away"):
            entries = (((row.get(side) or {})
                        .get("rosterForCurrentScoringPeriod") or {})
                       .get("entries"))
            if entries:
                return {"entries": entries}
    for team in payload.get("teams") or []:
        entries = (team.get("roster") or {}).get("entries")
        if entries:
            return {"entries": entries}
    return None


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2

    raw = json.loads(Path(argv[1]).read_text())
    if isinstance(raw, list):
        print(f"note: response was a list of {len(raw)}; using the first entry")
        raw = raw[0]

    roster_root = _entry_paths(raw)

    missing, present = [], []
    for path in fixtures_espn.LOAD_BEARING_PATHS:
        root = roster_root if path.startswith("entries[]") else raw
        if root is None:
            missing.append((path, "no roster entries found anywhere"))
            continue
        ok, sample = _walk(root, path)
        (present if ok else missing).append(
            (path, sample if ok else "absent"))

    print(f"\n{'=' * 70}\nMISSING — each of these is a bug in providers/espn.py"
          f"\n{'=' * 70}")
    if not missing:
        print("  (none — every path this adapter reads is present)")
    for path, why in missing:
        print(f"  ✗ {path}\n      {why}")

    print(f"\n{'=' * 70}\nPRESENT\n{'=' * 70}")
    for path, sample in present:
        text = repr(sample)
        print(f"  ✓ {path:<62} {text[:40]}")

    extra = set(raw) - fixtures_espn.EXPECTED_TOP_LEVEL
    print(f"\n{'=' * 70}\nEXTRA top-level keys the fixture does not model"
          f"\n{'=' * 70}")
    print("  " + (", ".join(sorted(extra)) if extra else "(none)"))

    # ---- and now actually parse it -------------------------------------
    print(f"\n{'=' * 70}\nPARSED\n{'=' * 70}")
    from providers.espn import ESPNProvider

    provider = ESPNProvider()
    provider._get = lambda lid, season, views, params=None: raw

    week = int(raw.get("scoringPeriodId") or 1)
    try:
        league = provider.get_league(str(raw.get("id") or "x"),
                                     raw.get("seasonId"))
        print(f"  league      {league.name!r} ({league.season}), "
              f"{league.team_count} teams, {league.scoring_type}")
        print(f"  slots       {league.roster_slots}")
        print(f"  status      {league.status}")
    except Exception as exc:  # noqa: BLE001 — this is a diagnostic
        print(f"  get_league FAILED: {type(exc).__name__}: {exc}")
        return 1

    try:
        data = provider.get_week(str(raw.get("id") or "x"),
                                 league.season, week)
    except Exception as exc:  # noqa: BLE001
        print(f"  get_week FAILED: {type(exc).__name__}: {exc}")
        return 1

    print(f"  week        {data.week}, {len(data.matchups)} matchups, "
          f"{len(data.byes)} byes")
    for matchup in data.matchups:
        for team in matchup.teams:
            starters = len(team.lineup)
            scored = sum(1 for p in team.lineup if p.points)
            projected = sum(1 for p in team.lineup if p.projected is not None)
            print(f"    {team.team_name[:28]:<28} {team.points:>7.1f}  "
                  f"({team.record})  {starters} starters, "
                  f"{scored} scored, {projected} projected")
            if not team.lineup:
                print("      ⚠ NO LINEUP — the roster path is wrong")
            elif scored == 0:
                print("      ⚠ EVERY STARTER ON ZERO — appliedStatTotal is wrong")
            elif projected == 0:
                print("      ⚠ NO PROJECTIONS — the stats filter is wrong")

    print("\nIf every line above looks right, flip ESPNProvider.implemented "
          "to True.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
