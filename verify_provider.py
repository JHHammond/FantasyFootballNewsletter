"""
Live smoke test against a real Sleeper league.

The offline test suite (tests/) covers logic against fixtures. This one hits the
real API, so run it locally where you have network:

    python verify_provider.py                       # defaults below
    python verify_provider.py 1252396303246176256 2025 1

It fetches nothing destructive and writes nothing but the cache.
"""

import sys

from providers import (
    ProviderError,
    available_providers,
    load_week,
    week_to_legacy_games,
)

DEFAULT_LEAGUE_ID = "1252396303246176256"
DEFAULT_SEASON = 2025
DEFAULT_WEEK = 1


def main():
    league_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LEAGUE_ID
    season = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_SEASON
    week = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_WEEK

    print("Providers registered:")
    for p in available_providers():
        mark = "✓" if p["implemented"] else "·"
        print(f"  {mark} {p['display_name']:10s} ({p['name']})")
    print()

    print(f"Fetching league {league_id}, {season} week {week}...")
    try:
        week_data = load_week("sleeper", league_id, season, week)
    except ProviderError as exc:
        print(f"FAILED: {exc}")
        return 1

    league = week_data.league
    print(f"\n{league.name}  —  {league.team_count} teams, {league.scoring_type.upper()}")
    print(f"Starting slots: {', '.join(league.starting_slots)}")
    print(f"Matchups: {len(week_data.matchups)}   Byes: {len(week_data.byes)}")

    print(f"\n{'MATCHUP':<52} {'SCORE':>16}")
    print("-" * 70)
    for m in week_data.matchups:
        a, b = m.teams
        label = f"{a.team_name} ({a.record}) vs {b.team_name} ({b.record})"
        print(f"{label[:52]:<52} {a.points:>7.2f}-{b.points:<7.2f}")

    print(f"\n{'TEAM':<24} {'PTS':>7} {'OPTIMAL':>8} {'LEFT ON BENCH':>14}")
    print("-" * 70)
    for team in sorted(week_data.teams, key=lambda t: t.points, reverse=True):
        print(f"{team.team_name[:24]:<24} {team.points:>7.2f} "
              f"{team.optimal_points:>8.2f} {team.lineup_gap:>14.2f}")

    print("\nSanity checks:")
    problems = []

    for team in week_data.teams:
        lineup_total = sum(p.points for p in team.lineup)
        if abs(lineup_total - team.points) > 1.0:
            problems.append(
                f"  ✗ {team.team_name}: starters sum to {lineup_total:.2f} "
                f"but the platform reports {team.points:.2f}"
            )
        if team.optimal_points < team.points - 0.01:
            problems.append(
                f"  ✗ {team.team_name}: optimal ({team.optimal_points}) "
                f"below actual ({team.points})"
            )
        if any(p.points is None for p in team.all_players):
            problems.append(f"  ✗ {team.team_name}: a player has None points")
        if team.games_played != week - 1:
            problems.append(
                f"  ✗ {team.team_name}: record is {team.record} entering week "
                f"{week} — expected {week - 1} games played"
            )

    if problems:
        print("\n".join(problems))
    else:
        print("  ✓ starter points reconcile with reported team totals")
        print("  ✓ optimal lineup never below actual")
        print("  ✓ no null player points")
        print(f"  ✓ every record reflects {week - 1} games (excludes week {week})")

    print("\nSpot check — biggest bust vs lowest scorer:")
    for team in list(week_data.teams)[:3]:
        bust, low = team.biggest_bust, team.low_scorer
        if bust and low:
            print(f"  {team.team_name[:20]:<20} "
                  f"bust: {bust.name} ({bust.vs_projection:+.1f} vs proj)   "
                  f"lowest: {low.name} ({low.points:.1f})")

    games = week_to_legacy_games(week_data)
    print(f"\nCompat layer produced {len(games)} games in the legacy shape.")

    try:
        from storylines import get_weekly_storylines
        summary = get_weekly_storylines(games)
        print(f"  ✓ storylines.py ran: closest game margin "
              f"{summary['closest_game']['margin']}, "
              f"bench blunder {summary['bench_blunder']['lineup_gap']}")
    except Exception as exc:
        print(f"  ✗ storylines.py failed: {exc}")
        return 1

    print("\nAll good. The provider layer is wired up correctly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
