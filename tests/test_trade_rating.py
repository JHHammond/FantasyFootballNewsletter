

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
    assert len(got) == 4
    # Off the board (IR): he isn't in the list, so he isn't drawn.
    got = chart_default(["a", "b", "c", "down", "up"], {"lines": lines, "last": 9}, trades)
    assert got[:3] == ["a", "up", "down"]
    del flat
