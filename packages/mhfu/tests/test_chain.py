from mhfu import files
from mhfu.cli import main
from mhfu.em.chain import Chain, Edge, simplify
from mhfu.em.moveset import Moveset


def test_simplify():
    guards = ("phase!=3", "phase!=2", "phase==1", "+0x280==0", "phase==1")
    assert simplify(guards) == ("phase==1", "+0x280==0")


def test_edge():
    e = Edge(0, "enter", 0, None, 0, (), (), (), ())
    assert e.computed


def test_game(game):
    for species in files.EM_SPECIES:
        ch = Chain(Moveset(game.em(species)))
        assert ch.enter.function is not None and ch.pairs
        assert not ch.walker.truncated
    ch = Chain(Moveset(game.em(75)))
    succ = {t for e in ch.pairs[1, 4].next for t in e.to}
    assert succ == {(0, 3), (0, 6), (2, 2)}
    collided = next(e for e in ch.pairs[1, 4].next if e.to == ((0, 6),))
    assert collided.guards == ("phase==3", "collided")
    assert ch.enter.resolve(1, 0) == ((1, 0), (1, 1))
    assert (1, 4) in ch.predecessors()[0, 6]


def test_cli(game, capsys):
    assert main(["chain", "75", "1", "4", "--data", str(game.root)]) == 0
    assert "collided" in capsys.readouterr().out
