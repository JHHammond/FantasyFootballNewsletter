

def test_the_chart_opens_on_number_one_and_the_movers():
    """John, 25 Oct: the top five bunched up at the top of the chart."""
    from web.trade_rating import chart_default
    flat = [[d, 0] for d in range(10)]
    lines = {"a": [[d, 1] for d in range(10)], "b": [[d, 2] for d in range(10)],
             "c": [[d, 3] for d in range(10)],
             "up": [[d, 40 - 2 * d] for d in range(10)],      # 40 -> 22
             "down": [[d, 20 + 3 * d] for d in range(10)],    # 20 -> 47
             "hurt": [[d, 10 + 20 * d] for d in range(10)]}
    trades = {k: 50 for k in lines}
    got = chart_default(["a", "b", "c", "hurt", "down", "up"],
                        {"lines": lines, "last": 9}, trades)
    assert got[0] == "a" and set(got[1:3]) == {"up", "hurt"}
    assert len(got) == 3            # three lines since 26 Oct
    # Off the board (IR): he isn't in the list, so he isn't drawn.
    got = chart_default(["a", "b", "c", "down", "up"], {"lines": lines, "last": 9}, trades)
    assert got[:3] == ["a", "up", "down"]
    del flat


def test_the_opening_lines_finish_apart():
    """John, 27 Oct: three volatile players who all end near #100 make the
    chart look strange. The opening set skips one that would finish right
    beside a line already chosen."""
    from web.trade_rating import spread_apart
    now = {"a": 94, "b": 100, "c": 114, "d": 40, "e": 160}
    got = spread_apart(["a", "b", "c", "d", "e"], now, 3)
    assert got == ["a", "d", "e"]
    # A riser and a faller when both are on offer, still apart.
    got = spread_apart(["a", "b", "c", "d", "e"], now, 3, nets={"a": 9, "b": 8, "c": -7, "d": 5, "e": 4})
    assert "c" in got and "b" not in got


def test_a_quiet_week_still_gets_its_lines():
    from web.trade_rating import spread_apart
    now = {"a": 50, "b": 51, "c": 52}
    assert spread_apart(["a", "b", "c"], now, 3) == ["a", "b", "c"]
