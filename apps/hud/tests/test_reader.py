# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The reader over a fake PPSSPP: what a quest's memory turns into, in a few batched reads."""

from mhfu import addresses as a
from mhfu_hud.calibration import Calibration
from mhfu_hud.reader import QUEST_MAP, MemoryReader
from mhfu_hud.state import Context
from ppsspp_debug import Client


def reads(fake) -> int:
    return sum(1 for m in fake.received if m["event"] == "memory.read")


def test_snapshot(fake, tmp_path):
    sections = Calibration(tmp_path / "cal.json").section_map(QUEST_MAP)
    reader = MemoryReader(port=fake.port, sections=sections)
    reader._client = Client.connect("127.0.0.1", fake.port, timeout=2.0)
    try:
        reader.poll()
        first = reads(fake)
        s = reader.poll()
        per_poll = reads(fake) - first
    finally:
        reader._client.close()
    assert s.context is Context.QUEST
    assert (s.area_index, s.tracked_section, s.tracked_section_source) == (99, 1, "area_index")
    assert s.quest_timer_frames == 30 * 60 * 35
    p = s.player
    assert (p.loaded, p.hp, p.hp_recov, p.hp_max, p.stamina) == (True, 100, 120, 150, 150)
    assert (p.sharpness, p.sharpness_max, p.sharpness_tier) == (80, 150, 1)
    assert (p.bag[0].item_id, p.bag[0].count) == (0x40, 1)
    tigrex, popo = s.monsters
    assert (tigrex.slot, tigrex.name, tigrex.big, tigrex.hp) == (1, "Tigrex", True, 2400)
    assert tigrex.actions == (0x2B,) * 3 and tigrex.state == (8, 3)
    assert tigrex.render_scale is not None and abs(tigrex.render_scale - 1.1) < 1e-6
    assert (popo.slot, popo.name, popo.big, popo.actions) == (2, "Popo", False, None)
    assert popo.herd and not tigrex.herd  # herd fields are small monsters'
    assert a.ENTITY.HERD_RALLY in [c.field for c in popo.cells]
    assert a.ENTITY.HERD_RALLY not in [c.field for c in tigrex.cells]
    sight = dict((c.field, c.value) for c in s.species_rows[0x4B].cells)
    assert sight[a.SPECIES.SIGHT_RADIUS] == 2700.0
    assert per_poll == 6 + len(s.monsters)  # the fixed regions, then one per monster
    assert not fake.writes
