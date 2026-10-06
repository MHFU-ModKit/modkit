# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import shutil
import subprocess
import sys

import numpy as np
import pytest
from mhfu_port import mesh
from mhfu_port.mesh import Part, Skinned
from mhfu_port.model import MHFU, MHP3RD, Model, ModelError
from mhp_formats import Channel, Clip, Keyframe, Pac, Tmh, TmhImage, Track, fu
from mhp_formats.anim import quantize
from mhp_formats.skeleton import Bone, Skeleton

BUNDLED = ("model", "fk", "motion", "mesh", "records", "constraints")
"""The modules a Blender extension imports: mhfu's standard-library modules (`mhfu.entries`)
but not its live chain, whose native wheels the extension cannot bundle."""
LIVE = ("ppsspp_debug", "psutil", "rabbitizer", "elftools")


def synthetic(skeleton_at: int = 0) -> bytes:
    """3 joints in two parts (0, 0, 1), one textured group of 3 vertices, a 2x2 texture; slot 1
    plays on both parts and turns joint 1 by 90 degrees about Y by frame 10, slot 2 only on
    part 1."""
    bones = [
        Bone(parent=-1, position=(0.0, 0.0, 0.0), stream=0),
        Bone(parent=0, position=(0.0, 100.0, 0.0), stream=0),
        Bone(parent=1, position=(0.0, 0.0, 50.0), stream=1),
    ]
    verts = [(0.0, 100.0, 0.0), (0.0, 100.0, 50.0), (10.0, 100.0, 0.0)]
    uvs = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    part = Part(verts, [], uvs, [], [(0, 1, 2)], [[(1, 1.0)], [(2, 1.0)], [(1, 1.0)]], 0)
    model = mesh.build([Skinned(part, part.influences)], (256.0, 256.0, 256.0))
    turn = [Keyframe(0, 0), Keyframe(quantize("rot", 1.5707963), 10)]
    still = Track([Channel(0x008, [Keyframe(0, 0), Keyframe(0, 10)])])
    body = Clip([still, Track([Channel(0x010, turn)])], 1)
    head = Clip([Track([Channel(0x008, [Keyframe(0, 0), Keyframe(0, 6)])])])
    anim = fu.Anim([[None, body, None], [None, None, None], [None, head, head]])
    entries = [
        Skeleton(bones, [0, 3]).to_bytes(),
        model.to_bytes(),
        Tmh([TmhImage(3, 2, 2, bytes(range(16)))]).to_bytes(),
        anim.to_bytes(),
    ]
    entries[0], entries[skeleton_at] = entries[skeleton_at], entries[0]
    return Pac(entries).to_bytes()


def test_bundled_modules_import_without_the_live_chain():
    blocked = "".join(f"sys.modules[{m!r}] = None\n" for m in LIVE)
    imports = "".join(f"import mhfu_port.{m}\n" for m in BUNDLED)
    r = subprocess.run(
        [sys.executable, "-c", f"import sys\n{blocked}{imports}"], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr


def test_mhfu():
    m = Model.from_bytes(synthetic(), "t")
    assert m.game == MHFU and m.rig.n == 3 and isinstance(m.anim, fu.Anim)
    (g,) = m.groups
    assert (g.n_vertices, g.n_faces, g.material, g.texture) == (3, 1, 0, 0)
    assert np.allclose(g.positions[1], (0.0, 100.0, 50.0), atol=0.01)
    assert g.uvs is not None and np.allclose(g.uvs[1], (1.0, 0.0), atol=1e-3)
    (t,) = m.textures
    assert (t.width, t.height) == (2, 2) and t.rgba[0, 1].tolist() == [4, 5, 6, 7]
    walk, head = m.clips
    assert (walk.slot, walk.frames, walk.loop, walk.tracks) == (1, 10, True, 3)
    assert walk.whole_rig and walk.driven == (0, 1, 2) and walk.name == "clip_01"
    assert not head.whole_rig and head.driven == (2,) and "are partial" in m.notes[0]
    rot, loc = m.curves(1).at(10.0)
    assert np.allclose(m.rig.world(rot, loc)[2, :3, 3], (50.0, 100.0, 0.0), atol=0.05)
    assert m.group_range(0) == (0, 3) and len(m.merged.positions) == m.n_vertices == 3


def test_names():
    m = Model.from_bytes(synthetic(), "t")
    m.rename({1: ["walk", "amble"]})
    assert m.clip("walk") is m.clip(1) is m.clip("amble") and m.clip(1).name == "amble"
    m.rename({})
    with pytest.raises(KeyError):
        m.clip("walk")


def test_refused():
    with pytest.raises(ModelError, match="not a model PAC"):
        Model.from_bytes(b"\0" * 32, "x")
    with pytest.raises(ModelError, match="no skeleton"):
        Model.from_bytes(Pac([b"abc" * 8]).to_bytes(), "x")
    with pytest.raises(ModelError, match="the skeleton is entry 1"):
        Model.from_bytes(synthetic(skeleton_at=1), "x")


def test_from_path(tmp_path):
    path = tmp_path / "file_09999.bin"
    path.write_bytes(synthetic())
    m = Model.from_path(path)
    assert (m.name, m.path, m.pac) == ("file_09999", path, path.read_bytes())


def test_donor(mhp3rd_data, tmp_path):
    """The moveset is the emNNN file after the model PAC, or the one named."""
    m = Model.from_path(mhp3rd_data / "file_05248.bin")
    assert m.game == MHP3RD and m.anim is None and m.record_to_bone and len(m.clips) > 40
    assert not m.notes, m.notes
    renamed = tmp_path / "brute.pac"
    shutil.copy(mhp3rd_data / "file_05248.bin", renamed)
    with pytest.raises(ModelError, match="companion file"):
        Model.from_path(renamed)
    still = Model.from_path(renamed, geometry=mhp3rd_data / "file_05249.bin")
    assert not still.clips and len(still.groups) == len(m.groups)
    assert [n.split(":")[0] for n in still.notes] == [
        "brute.pac is not a file_NNNNN name",
        "no MHP3rd moveset was loaded",
    ]
    named = Model.from_path(
        renamed, 58, mhp3rd_data / "file_05249.bin", mhp3rd_data / "file_05250.bin"
    )
    assert [c.frames for c in named.clips] == [c.frames for c in m.clips]
    assert named.record_to_bone == m.record_to_bone and not named.notes
    assert m.pac is not None and m.em_id == 58
    again = Model.from_bytes(m.pac, m.name, m.geometry, m.moveset, m.em_id)
    assert again.record_to_bone == m.record_to_bone and len(again.clips) == len(m.clips)
