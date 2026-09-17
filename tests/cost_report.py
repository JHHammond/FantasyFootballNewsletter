"""Where does the money in one paper actually go?

    python -m tests.cost_report          # priced on the current big model
    BIG=sonnet5 python -m tests.cost_report

Run it after ANY change to a prompt or to the model routing. The numbers that
shaped the current setup: output is ~60% of the bill, the matchup recaps are
53% of the output, and the headline call was sending more input than the recap
it titles.

Stands a recorder in front of the Anthropic client and runs a real
generation against a real week, counting what each call sends and what it is
allowed to send back. No API key and no network: the point is the SHAPE of
the bill — which calls are big, what is duplicated across them, and how much
of the input is the cached system block — not the cent.

Token counts are estimated from characters. English prose runs about 3.9
characters per token; the absolute numbers are therefore approximate and the
PROPORTIONS, which is what any decision here turns on, are not.
"""
from __future__ import annotations

import sys
from collections import defaultdict

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

CHARS_PER_TOKEN = 3.9

# Published per-million rates. Cache writes bill at 1.25x the base input rate
# and cache reads at 0.1x.
PRICES = {
    "sonnet": {"in": 3.00, "out": 15.00},     # Sonnet 4.6, what the writer used
    "sonnet5": {"in": 2.00, "out": 10.00},    # Sonnet 5 — newer AND cheaper
    "haiku": {"in": 1.00, "out": 5.00},
}


def tokens(text: str) -> int:
    return int(len(text or "") / CHARS_PER_TOKEN)


class Recorder:
    """Stands in for anthropic.Anthropic()."""

    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, *, model, max_tokens, system, messages, **kw):
        system_text = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in (system if isinstance(system, list) else [system or ""]))
        cached = any(isinstance(b, dict) and b.get("cache_control")
                     for b in (system if isinstance(system, list) else []))
        prompt = messages[0]["content"]

        self.calls.append({
            "model": model,
            "max_tokens": max_tokens,
            "system_tokens": tokens(system_text),
            "cached_system": cached,
            "prompt_tokens": tokens(prompt),
            "prompt": prompt,
        })

        class Block:
            text = ("A sentence of plausible newspaper prose about the week. "
                    * 3)

        class Message:
            content = [Block()]

        return Message()


def a_real_week():
    """The ESPN fixture, run through the real pipeline."""
    from providers.espn import ESPNProvider
    from providers import week_to_legacy_games
    from storylines import get_weekly_storylines
    from tests import fixtures_espn as f

    provider = ESPNProvider()
    provider._get = (lambda lid, season, views, params=None, fantasy_filter=None:
                     f.fake_get(lid, season, views, params, fantasy_filter))
    week = provider.get_week(f.LEAGUE_ID, f.SEASON, 2)
    games = week_to_legacy_games(week)
    return games, get_weekly_storylines(games)


def main():
    import writer

    recorder = Recorder()
    writer.client = recorder

    games, summary = a_real_week()
    writer.generate_full_newspaper_content(
        league_name="The Kevlarville Times", week=2, games=games,
        summary=summary, commissioner_name="johnhenryhammond",
        inside_jokes="Last place has to get a tattoo.", tone="standard")

    calls = recorder.calls
    print(f"\n{'=' * 78}")
    print(f"{len(calls)} calls")
    print(f"{'=' * 78}\n")

    # --- what is duplicated across calls? --------------------------------
    system_sizes = {c["system_tokens"] for c in calls}
    cached = {c["cached_system"] for c in calls}
    print(f"  system block: {sorted(system_sizes)} tokens, "
          f"cache_control set: {cached}")

    first_system = calls[0]["system_tokens"]
    total_prompt = sum(c["prompt_tokens"] for c in calls)
    print(f"  system, billed once at 1.25x then 0.1x: "
          f"{first_system:,} tok")
    print(f"  per-call prompts, all billed in full:   {total_prompt:,} tok\n")

    # Duplicate content ACROSS prompts is the thing worth finding: the same
    # paragraph of league data sent eleven times is eleven times the price.
    fragments = defaultdict(int)
    for call in calls:
        for line in set(call["prompt"].splitlines()):
            line = line.strip()
            if len(line) > 40:
                fragments[line] += 1
    repeated = {line: n for line, n in fragments.items() if n > 1}
    repeated_tokens = sum(tokens(line) * (n - 1) for line, n in repeated.items())
    print(f"  lines appearing in more than one prompt: {len(repeated)}")
    print(f"  tokens spent re-sending them:            {repeated_tokens:,} "
          f"({100 * repeated_tokens / max(total_prompt, 1):.0f}% of prompt spend)\n")

    if repeated:
        print("  the biggest repeats:")
        worst = sorted(repeated.items(),
                       key=lambda kv: -tokens(kv[0]) * (kv[1] - 1))[:6]
        for line, n in worst:
            print(f"    x{n:<3} {tokens(line):>4} tok  {line[:64]}")

    # --- per call --------------------------------------------------------
    print(f"\n{'call':<26}{'prompt':>8}{'max out':>9}{'model':>22}")
    print("  " + "-" * 62)
    for call in sorted(calls, key=lambda c: -c["prompt_tokens"]):
        label = call["prompt"].strip().splitlines()[0][:24]
        print(f"  {label:<24}{call['prompt_tokens']:>8,}"
              f"{call['max_tokens']:>9,}{call['model']:>22}")

    # --- the bill --------------------------------------------------------
    #
    # Priced PER MODEL, and scaled to a real league. The ESPN fixture carries
    # one matchup; a ten-team league plays five, so the per-game calls happen
    # five times and everything else once. Pricing the fixture as-is would
    # report a paper a fifth of the real size and flatter every change made
    # to the per-game path, which is the expensive path.
    GAMES = 5

    import os
    big = os.getenv("BIG", "sonnet")

    def rate(model):
        return PRICES["haiku" if "haiku" in model else big]

    per_game = [c for c in calls if "MATCHUP HEADLINE" in c["prompt"]
                or "recap of this" in c["prompt"]]
    once = [c for c in calls if c not in per_game]
    scaled = once + per_game * GAMES

    print(f"\n  scaled to a {GAMES}-matchup league: {len(scaled)} calls")

    system_write = first_system * 1.25 * PRICES["sonnet"]["in"] / 1e6
    system_reads = sum(first_system * 0.1 * rate(c["model"])["in"] / 1e6
                       for c in scaled[1:])
    prompts = sum(c["prompt_tokens"] * rate(c["model"])["in"] / 1e6
                  for c in scaled)
    in_cost = system_write + system_reads + prompts

    print(f"\n  INPUT   ~${in_cost:.4f}")
    for fill, label in ((1.0, "every call maxed out"), (0.6, "at 60% fill")):
        out_cost = sum(c["max_tokens"] * fill * rate(c["model"])["out"] / 1e6
                       for c in scaled)
        print(f"  OUTPUT  ~${out_cost:.4f}   ({label})")
        print(f"  TOTAL   ~${in_cost + out_cost:.4f}")

    # What it would have cost with everything on the expensive model, for the
    # same prompts — the honest before/after, since the prompts also shrank.
    all_big = sum(c["max_tokens"] * 0.6 * PRICES[big]["out"] / 1e6
                  for c in scaled)
    big_in = (system_write
              + sum(first_system * 0.1 * PRICES[big]["in"] / 1e6
                    for c in scaled[1:])
              + sum(c["prompt_tokens"] * PRICES[big]["in"] / 1e6
                    for c in scaled))
    print(f"\n  same prompts, all on the big model: ~${big_in + all_big:.4f}")
    print()


if __name__ == "__main__":
    main()
