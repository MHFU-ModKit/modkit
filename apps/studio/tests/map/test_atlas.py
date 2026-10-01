# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import ORPHAN_STAGES, Atlas, row_name, stage_name


def test_names_are_not_guessed():
    assert row_name(0) == "Pokke village & town" and row_name(7) == "row 7"
    assert stage_name(98) == "Snowy base camp" and stage_name(200) == "st200"


def test_synthetic(atlas: Atlas, game: Extracted):
    assert atlas.village.stages == [139, 98] and atlas.village.entry is not None
    assert atlas.section(98) is not None and atlas.section(98).slot == 1
    assert atlas.rows_of(98) == [(0, 1)] and atlas.section(98, row=3) is None
    assert atlas.arrivals(139) == []  # no overlays in the synthetic game
    assert atlas.twins() == {139: [98], 98: [139]}
    assert "row  0  Pokke village & town" in atlas.describe(0)


def test_rows(shipped: Extracted):
    atlas = Atlas(shipped)
    assert len(atlas.rows) == 32 and len(atlas.live_rows()) == 30
    assert atlas.village.stages == [66, 139, 13, 46, 85, 17]
    assert atlas.row(11).stages == [98, 92, 93, 94, 95, 96, 97, 99, 100, 6]
    assert atlas.row(21).stages == [107, 101, 102, 103, 104, 105, 106, 108, 109, 15]
    assert atlas.row(24).sections == [] and atlas.row(25).sections == []
    entry = atlas.row(11).entry
    assert entry is not None and entry.stage == 98 and entry.is_entry
    assert (atlas.row(11).slot_of(99), atlas.row(11).slot_of(107)) == (7, None)
    s99, s17 = atlas.section(99), atlas.section(17)
    assert s99 is not None and s99.name == "Snowy area 1" and s17 is not None and not s17.present
    assert atlas.rows_of(139) == [(0, 1)]
    orphans = atlas.unreferenced()
    assert len(orphans) == 23 and set(ORPHAN_STAGES) <= set(orphans) and 98 not in orphans
    (frm, e), *_ = atlas.arrivals(98)
    assert frm == 99 and e.target == 98
    twins = atlas.twins()
    assert 107 in twins[98] and 98 in twins[107] and 66 in twins[139] and 99 not in twins[98]
