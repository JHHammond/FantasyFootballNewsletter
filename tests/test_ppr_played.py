"""Who played, read from Sleeper's weekly stat lines (migration 055)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web import ppr  # noqa: E402


def test_games_and_snaps_decide_who_played():
    # The dict shape: {player_id: stats}
    raw = {
        "8205": {"pts_ppr": 0.0, "gp": 1, "off_snp": 22, "tm_off_snp": 61},   # played, scored 0
        "4034": {"pts_ppr": 30.7, "gp": 1, "off_snp": 50, "tm_off_snp": 66},
        "6904": {"gp": 0, "off_snp": 0},                                        # active, never on
        "9999": {"pts_ppr": 1.2},                                               # no games or snaps
        "KC": {"pts_ppr": 16.0, "gp": 1},                                       # a defence
    }
    got = ppr._played_from(raw)
    assert got["8205"] == {"played": True, "snaps": 22, "team_snaps": 61}
    assert got["6904"]["played"] is False
    assert "9999" not in got          # unknown: the boards fall back to points
    assert got["KC"]["played"] is True and got["KC"]["snaps"] is None


def test_the_list_shape_reads_the_same():
    raw = [{"player_id": "8205", "stats": {"pts_ppr": 0.0, "gp": 1, "st_snp": 4, "tm_st_snp": 28}},
           {"player_id": "1", "stats": {"def_snp": 0, "gp": 0}}]
    got = ppr._played_from(raw)
    assert got["8205"] == {"played": True, "snaps": 4, "team_snaps": 28}
    assert got["1"]["played"] is False
