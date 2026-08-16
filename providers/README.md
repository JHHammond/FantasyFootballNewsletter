# Provider layer

Everything upstream of this package speaks one normalized data model. Adding a
fantasy platform means writing one adapter — no changes to `storylines.py`,
`writer.py`, or `newspaper.py`.

## Using it

```python
from providers import load_week, week_to_legacy_games

week_data = load_week("sleeper", league_id, season=2025, week=1)
games = week_to_legacy_games(week_data)   # feeds the existing pipeline
```

`load_week` fetches, normalizes, and runs the lineup optimizer in one call —
replacing the six `fetch_data` calls plus `pair_matchups`,
`add_lineup_gap_to_games`, and `enrich_games_with_player_stats`.

## Files

| File | What it does |
|---|---|
| `models.py` | The normalized types: `League`, `Team`, `Manager`, `PlayerLine`, `Matchup`, `WeekData`. Plus canonical slot names and eligibility rules. |
| `base.py` | `FantasyProvider` — the two methods every adapter must implement — and the error types. |
| `sleeper.py` | The Sleeper adapter. Complete. |
| `espn.py` | Stub. Endpoints, slot maps, and the cookie situation are documented at the top. |
| `optimizer.py` | Optimal-lineup solver as maximum-weight bipartite matching. |
| `cache.py` | TTL disk cache. Keeps the 5MB player index from being re-downloaded every run. |
| `compat.py` | Flattens `WeekData` into the legacy dict shape so the existing pipeline keeps working. |

## Adding a platform

1. Subclass `FantasyProvider`, implement `get_league` and `get_week`.
2. Map the platform's slot names into the canonical ones in `models.SLOT_ELIGIBILITY`.
   Numeric or platform-specific slot IDs must not escape your adapter.
3. Emit player IDs through `self.namespaced_id()` so IDs can't collide across platforms.
4. Register the class in `PROVIDERS` in `__init__.py`.
5. Add fixtures and run the same tests — most of `tests/test_providers.py`
   applies to any adapter with only the fixture swapped.

Contract points that are easy to get wrong:

- **Records must be as of *entering* the week**, not including it. Most platforms
  report the post-week record; `sleeper.py::_records_entering_week` shows how to
  replay prior weeks instead.
- **Points are floats, never `None`.** A player who didn't play scored `0.0`.
- **Keep empty starting slots in `lineup`** as a `PlayerLine` with an empty
  `player_id`, so `empty_slots` and the optimizer can both see the hole.

## What changed vs. the old `fetch_data.py`

- The player index is cached 24h instead of downloaded on every generation.
  Sleeper asks for once-per-day, max.
- Records are replayed from matchup history rather than read from
  `roster["settings"]["wins"]`, which already includes the week being reported
  on. This is what made upset detection dead code.
- Null player points are coerced to `0.0` instead of flowing downstream as
  `None` and raising on the first comparison.
- The optimizer solves a matching problem instead of walking slots left to
  right, so leagues that list `FLEX` or `SUPER_FLEX` before the dedicated slots
  get correct answers.
- `bottom_performer` is now the biggest projection miss rather than the lowest
  raw score. See the comment in `compat.py` — it's a deliberate change, and
  a one-line revert.
