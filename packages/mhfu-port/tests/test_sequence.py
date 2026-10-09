# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from mhfu_port import manifest, sequence

HEAD = """
[port]
name = "z"
host_species = 75
pac = "z.bin"
[source]
model = 5339
[clips.a]
slot = 1
"""


def moves(**after: str | None) -> manifest.Manifest:
    """Own moves in the given order, each `after` the move named (None: no `after`)."""
    body = "".join(
        f'[moves.{n}]\nclip = "a"\n' + (f'after = "{a}"\n' if a else "") for n, a in after.items()
    )
    return manifest.loads(HEAD + body)


def test_chain():
    m = moves(c="d", a="b", b="c", d=None, solo=None)
    assert sequence.chain(m, "a") == ["a", "b", "c", "d"]
    assert sequence.chain(m, "c") == ["c", "d"]
    assert sequence.chain(m, "solo") == ["solo"]
    assert sequence.chain(m, "nope") == []


def test_chain_stops_at_the_first_repeat():
    m = moves(h="a", a="b", b="c", c="a")
    assert sequence.chain(m, "h") == ["h", "a", "b", "c"]
    assert sequence.loops(m, "h")
    assert not sequence.loops(moves(a="b", b=None), "a")


def test_heads():
    m = moves(c="d", a="b", b="c", d=None, solo=None)
    assert sequence.heads(m) == ["a", "solo"], "in manifest order"
    assert [sequence.head_of(m, n) for n in "abcd"] == ["a"] * 4
    assert sequence.head_of(m, "solo") == "solo"


def test_a_ring_is_headed_by_its_first_name():
    m = moves(z="y", y="x", x="z", lone=None)
    assert sequence.heads(m) == ["x", "lone"] and sequence.head_of(m, "y") == "x"
    assert sequence.chain(m, "x") == ["x", "z", "y"] and sequence.loops(m, "x")


def test_a_join_belongs_to_the_first_head():
    m = moves(p="join", q="join", join="end", end=None)
    assert sequence.heads(m) == ["p", "q"]
    assert sequence.head_of(m, "join") == "p" and sequence.head_of(m, "q") == "q"
    assert sequence.chain(m, "q") == ["q", "join", "end"]
    assert sequence.groups(m) == {"p": ["p", "join", "end"], "q": ["q"]}


def test_step_name():
    m = moves(topple="topple_2", topple_2="topple_4", topple_4=None)
    assert sequence.step_name(m, "topple") == "topple_3"
    assert sequence.step_name(m, "fresh") == "fresh_2"
