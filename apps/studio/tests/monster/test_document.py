# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu_port import manifest
from mhfu_port.manifest import Clip, Hurtbox, ManifestError
from mhfu_studio.monster.document import PortDocument


def test_edit_undo_redo(doc):
    d = doc()
    assert not d.dirty and not d.can_undo()
    d.edit(lambda m: m.clips.__setitem__("idle", Clip(1)))
    assert d.dirty and "idle" in d.manifest.clips
    d.undo()
    assert not d.dirty and "idle" not in d.manifest.clips
    d.redo()
    assert d.dirty and d.manifest.clips["idle"].slot == 1


def test_refused_edit(doc):
    d = doc()
    with pytest.raises(ManifestError, match="part 9"):
        d.edit(lambda m: m.hurtboxes.append(Hurtbox(1, 10.0, part=9)))
    assert not d.dirty and not d.manifest.hurtboxes


def test_save_and_revert(doc):
    d = doc()
    d.edit(lambda m: m.clips.__setitem__("idle", Clip(1, 120, True)))
    path = d.save()
    assert not d.dirty and manifest.load(path).clips["idle"].frames == 120
    assert d.saved_manifest.clips["idle"].frames == 120
    d.edit(lambda m: m.clips.pop("idle"))
    d.revert()
    assert not d.dirty and "idle" in d.manifest.clips
    d.undo()
    assert d.dirty and "idle" not in d.manifest.clips


def test_no_path(make):
    d = PortDocument(make())
    with pytest.raises(ManifestError, match="no path"):
        d.save()


def test_findings(doc):
    d = doc('\n[clips.idle]\nslot = 1\n\n[moves.m]\nmain = 0\nsub = 1\nclip = "idle"\n')
    codes = {f.code for f in d.findings()}
    assert {"PAC_ABSENT", "INTEL_ABSENT"} <= codes
