# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import tomllib

import pytest
from mhfu import points as P

WALL = P.Point("wall", 97, (11000.0, 360.0, 5000.0), note="Tigrex gets stuck")
LEDGE = P.Point("ledge", 99, (15400.0, 0.0, 7950.0), "climb", 157.0)


def test_round_trip():
    assert P.parse(P.dump([WALL, LEDGE])) == [WALL, LEDGE]
    assert "kind" not in P.dump([WALL])[0]


def test_load(tmp_path):
    (tmp_path / "map.toml").write_text(
        '[map]\nname = "snow"\n\n[[point]]\nname = "ledge"\nstage = 99\n'
        'at = [15400, 0, 7950]\nkind = "climb"\nheading = 157\n'
    )
    assert P.load(tmp_path) == [LEDGE]
    assert P.load(tmp_path / "map.toml")[0].xz == (15400.0, 7950.0)


def test_no_points(tmp_path):
    (tmp_path / "map.toml").write_text('[map]\nname = "x"\n')
    assert P.load(tmp_path) == []


@pytest.mark.parametrize(
    ("table", "match"),
    [
        ({"stage": 97, "at": [0, 0, 0]}, "name"),
        ({"name": "a", "stage": True, "at": [0, 0, 0]}, "stage"),
        ({"name": "a", "stage": 999, "at": [0, 0, 0]}, "no stage"),
        ({"name": "a", "stage": 97, "at": [0, 0]}, "at"),
        ({"name": "a", "stage": 97, "at": [0, 0, 0], "kind": "door"}, "kind"),
        ({"name": "a", "stage": 97, "at": [0, 0, 0], "kind": "climb"}, "heading"),
        ({"name": "a", "stage": 97, "at": [0, 0, 0], "heading": 3}, "heading"),
        ({"name": "a", "stage": 97, "at": [0, 0, 0], "colour": 1}, "unknown"),
        ({"name": " a", "stage": 97, "at": [0, 0, 0]}, "padded"),
    ],
)
def test_bad_tables(table, match):
    with pytest.raises(P.PointError, match=match):
        P.parse([table])


def test_names_are_unique():
    with pytest.raises(P.PointError, match="twice: wall"):
        P.parse(P.dump([WALL, WALL]))


def test_bad_toml(tmp_path):
    (tmp_path / "map.toml").write_text("[map\n")
    with pytest.raises(P.PointError, match="map.toml"):
        P.load(tmp_path)


def test_find():
    assert P.find([WALL, LEDGE], "ledge") is LEDGE
    with pytest.raises(KeyError, match="known: wall"):
        P.find([WALL], "door")


def test_parse_reads_toml():
    data = tomllib.loads('[[point]]\nname = "p"\nstage = 98\nat = [1, 2.5, 3]\n')
    assert P.parse(data["point"]) == [P.Point("p", 98, (1.0, 2.5, 3.0))]


def test_on_stage(monkeypatch):
    frames = {97: ("snow",), 106: ("snow",), 98: ("camp",)}
    monkeypatch.setattr(P, "frame", lambda game, stage: frames[stage])
    night = P.Point("n", 106, (0.0, 0.0, 0.0))
    camp = P.Point("c", 98, (0.0, 0.0, 0.0))
    assert P.on_stage([WALL, night, camp], 97, game=object()) == [WALL, night]
    assert P.on_stage([WALL, night, camp], 97, game=None) == [WALL]


def test_frame_of_the_snowy_twins():
    game = pytest.importorskip("mhfu.files").Extracted
    try:
        g = game.find()
    except FileNotFoundError:
        pytest.skip("MHFU_DATA is not set")
    assert P.frame(g, 97) == P.frame(g, 106) != P.frame(g, 98)
