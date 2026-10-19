# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu.memory import Image
from mhfu.writes import STALE, Plan, Pushed, Refused, Write, apply, failing

BASE = 0x1000


class Lossy(Image):
    """Loses the writes to `drop`; counts reads."""

    def __init__(self, data: bytes, drop: int = -1) -> None:
        super().__init__(data, BASE)
        self.drop = drop
        self.reads = 0

    def read(self, address: int, size: int) -> bytes:
        self.reads += 1
        return super().read(address, size)

    def write(self, address: int, data: bytes) -> None:
        if address != self.drop:
            super().write(address, data)


def mem(drop: int = -1) -> Lossy:
    """Guard bytes AA at +0 and BB at +4; zeroes elsewhere."""
    m = Lossy(bytes(0x20), drop)
    m.write(BASE, b"\xaa")
    m.write(BASE + 4, b"\xbb")
    return m


GUARDS = ((BASE, b"\xaa"), (BASE + 4, b"\xbb"))


def plan(*guards: tuple[int, bytes], notes: tuple[str, ...] = ()) -> Plan:
    one = Write("one", BASE + 0x10, ((b"\1\2", "a"), (b"\3", "b")), guards)
    two = Write("two", BASE + 0x14, ((b"\4", ""),), guards[-1:])
    return Plan((one, two), notes)


def test_size_and_data() -> None:
    p = plan()
    assert p.writes[0].data == b"\1\2\3" and p.size == 4


def test_apply() -> None:
    m = mem()
    done = apply(m, plan(*GUARDS), "here")
    assert m.read(BASE + 0x10, 5) == b"\1\2\3\0\4" and not done.mismatched
    assert done.describe().startswith("pushed 2 write(s), 4 bytes, into here in ")
    assert done.describe().endswith("read back matches")


def test_notes() -> None:
    done = apply(mem(), plan(*GUARDS, notes=("left out",)))
    assert "into the game in " in done.describe() and done.describe().endswith("; left out")


def test_refused_writes_nothing() -> None:
    m = mem()
    m.write(BASE + 4, b"\0")
    with pytest.raises(Refused):
        apply(m, plan(*GUARDS))
    assert m.read(BASE + 0x10, 5) == bytes(5)


def test_first_failing_guard() -> None:
    m = Lossy(bytes(0x20))
    assert failing(m, plan(*GUARDS).writes) == ("one: 0x00001000 reads 00, the plan expects AA")
    with pytest.raises(Refused, match=r"hold what this plan was built against \(one: 0x00001000"):
        apply(m, plan(*GUARDS))


def test_refusal_text() -> None:
    with pytest.raises(Refused) as e:
        apply(Lossy(bytes(0x20)), plan(*GUARDS), refusal="stale: {why}.")
    assert str(e.value) == "stale: one: 0x00001000 reads 00, the plan expects AA."
    assert "{why}" in STALE


def test_unmapped_guard() -> None:
    p = Plan((Write("far", BASE, ((b"\1", ""),), ((BASE + 0x100, b"\xaa"),)),))
    assert failing(mem(), p.writes) == "far: 0x00001100 reads nothing, the plan expects AA"
    with pytest.raises(Refused, match="reads nothing"):
        apply(mem(), p)


def test_guard_read_once() -> None:
    m = mem()
    p = Plan(tuple(Write(f"w{i}", BASE + 0x10 + i, ((b"\1", ""),), GUARDS[:1]) for i in range(3)))
    assert failing(m, p.writes) is None
    assert m.reads == 1


def test_mismatch() -> None:
    p = plan(*GUARDS)
    done = apply(mem(drop=BASE + 0x14), p, "here")
    assert done.mismatched == ["two"]
    assert "read back DIFFERS at two" in done.describe()


def test_pushed_defaults() -> None:
    assert Pushed(Plan(()), 0.0).describe().startswith("pushed 0 write(s), 0 bytes, into the game")
