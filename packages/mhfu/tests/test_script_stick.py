# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""An AI script on the memory stick: its container and the mod that installs it, carrying its id."""

import struct
from pathlib import Path

import pytest
from lupa.lua54 import LuaRuntime
from mhfu import addresses as a
from mhfu import inject as sticks
from mhfu.em import script
from mhfu.em.script import asm, build, edit, ir, lift, stick
from mhfu.em.script.editset import Anchor, EditSet, Insert
from mhfu.em.script.inject import Container, Head, container

ROW = 0x2A
ID = 0xA1B2C3D4
N = int(a.AI_OVERLAY) + 0x1000  # the synthetic native region


def u32s(*words):
    return struct.pack(f"<{len(words)}I", *words)


NATIVE = (
    bytes.fromhex("0500010005000200ff00")  # r0: two enters and an end
    + bytes(6)
    + u32s(N, 0)  # base_l: r0 and an empty slot
    + u32s(N + 0x10, N + 0x10)  # h0 and h1
)


def program():
    """Two headers over one list, for map types 0-4 and 5-16."""

    def read(pc):
        return NATIVE[pc - N] if 0 <= pc - N < len(NATIVE) else script.SENTINEL

    body, end = lift.routine(read, N, ROW)
    items = [
        ir.Routine("r0", body, NATIVE[end - N : 0x10], N),
        ir.Table("base_l", ["r0", None], N + 0x10),
        ir.Header("h0", ["base_l"], tuple(range(5)), N + 0x18),
        ir.Header("h1", ["base_l"], tuple(range(5, script.VARIANTS)), N + 0x1C),
    ]
    p = ir.Program(ROW, N, items, N + len(NATIVE))
    return p, asm.assemble(p)


@pytest.fixture(autouse=True)
def no_stick_env(monkeypatch):
    for name in (sticks.MEMSTICK_ENV, sticks.LANE_ENV):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def psp(tmp_path):
    """A memory stick's PSP folder with the framework's mods directory."""
    root = tmp_path / "PSP"
    (root / sticks.MODS_SUBDIR).mkdir(parents=True)
    return root


def test_the_stick_container_is_load_0_without_maps():
    p, img = program()
    data = stick.build(p, img)
    assert data == container(p, img, 0, origin=False)
    box = Container.read(data)
    assert (box.row, box.load, box.origin, box.items) == (ROW, 0, (), ())
    assert len(box.patches) == 2


def test_the_paths():
    assert sticks.brain_path(7) == "ms0:/PSP/PLUGINS/mhfu_framework/brain/brain_row_07.mhai"
    assert sticks.brain_filename(ROW) == "brain_row_42.mhai"
    assert sticks.brain_module(ROW) == "brain_row_42.lua"


def run(text, *installed, live=ID):
    """The mod's text on a Lua with the framework's calls, `mhfu.brain_install` returning
    `installed` and the row's live container having id `live`: the paths it was asked for, and
    the log."""
    lua = LuaRuntime(unpack_returned_tuples=True)
    asked, logs = [], []

    def brain_install(path):
        asked.append(path)
        return installed

    lua.globals().mhfu = lua.table_from(
        {
            "brain_install": brain_install,
            "brain_status": lambda row: lua.table_from({"id": live}),
            "log": logs.append,
        }
    )
    lua.execute(text)
    return asked, logs


def test_the_mod_installs_the_path():
    path = sticks.brain_path(ROW)
    text = stick.lua_module(ROW, path, ID)
    assert path in text
    asked, logs = run(text, ROW)
    assert asked == [path] and logs == [f"[brain] row {ROW} installed, id A1B2C3D4"]


def test_the_mod_reads_an_id_from_0x80000000_up():
    text = stick.lua_module(ROW, sticks.brain_path(ROW), ID)
    _, logs = run(text, ROW, live=ID - (1 << 32))  # the framework returns a u32 as signed
    assert logs == [f"[brain] row {ROW} installed, id A1B2C3D4"]


def test_the_mod_carries_the_id():
    path = sticks.brain_path(ROW)
    text = stick.lua_module(ROW, path, ID)
    assert "container A1B2C3D4" in text and "0xA1B2C3D4" in text
    assert stick.lua_module(ROW, path, ID + 1) != text
    assert stick.lua_module(ROW, path, ID) == text


def test_the_mod_says_when_the_container_is_another_build():
    _, logs = run(stick.lua_module(ROW, "ms0:/x.mhai", ID), ROW, live=0x11223344)
    assert logs == [f"[brain] row {ROW}: the container is 11223344, not A1B2C3D4"]


def test_the_mod_logs_what_went_wrong():
    _, logs = run(stick.lua_module(ROW, "ms0:/x.mhai", ID), None, "brain_install ms0:/x.mhai: no")
    assert logs == ["[brain] row 42: brain_install ms0:/x.mhai: no"]


def test_the_mod_says_when_the_container_is_another_row():
    _, logs = run(stick.lua_module(ROW, "ms0:/x.mhai", ID), 7)
    assert logs == ["[brain] row 42: the container is row 7's"]


def test_a_path_is_quoted():
    asked, _ = run(stick.lua_module(ROW, 'ms0:/a"b\\c.mhai', ID), ROW)
    assert asked == ['ms0:/a"b\\c.mhai']


def test_deploy_writes_the_container_and_the_mod(psp):
    p, img = program()
    written = stick.deploy(p, img, psp)
    brain = psp / sticks.BRAIN_SUBDIR / sticks.brain_filename(ROW)
    mod = psp / sticks.MODS_SUBDIR / sticks.brain_module(ROW)
    assert written == [brain, mod]
    assert brain.read_bytes() == container(p, img, 0, origin=False)
    assert mod.read_text() == stick.lua_module(
        ROW, sticks.brain_path(ROW), Head.parse(brain.read_bytes()).id
    )
    assert sorted(f.name for d in (brain.parent, mod.parent) for f in d.iterdir()) == sorted(
        [brain.name, mod.name]
    )


def test_deploying_again_replaces_both(psp):
    p, img = program()
    stick.deploy(p, img, psp)
    stale = psp / sticks.BRAIN_SUBDIR / sticks.brain_filename(ROW)
    stale.write_bytes(b"stale")
    stick.deploy(p, img, psp)
    assert stale.read_bytes() == container(p, img, 0, origin=False)


def test_deploy_finds_the_stick(monkeypatch, psp):
    monkeypatch.setenv(sticks.MEMSTICK_ENV, str(psp))
    p, img = program()
    [brain, _] = stick.deploy(p, img)
    assert Path(brain).parent == psp / sticks.BRAIN_SUBDIR


def test_deploy_without_the_framework_writes_nothing(tmp_path):
    root = tmp_path / "PSP"
    root.mkdir()
    p, img = program()
    with pytest.raises(FileNotFoundError, match="mods directory"):
        stick.deploy(p, img, root)
    assert list(root.iterdir()) == []


# ---- ship


def built(*nodes):
    """The program with `nodes` put after its first instruction, built as a brain file would."""
    p, _ = program()
    es = EditSet.of(p)
    if nodes:
        es.inserts["u1"] = Insert(Anchor("after", p.routines()[0].body[0].id), list(nodes))
    return build.Built.of(p, es)


def test_ship_writes_the_container_then_the_mod(psp):
    b = built(edit.insn("enter", 3, 0, 0))
    written = stick.ship(b, psp)
    brain = psp / sticks.BRAIN_SUBDIR / sticks.brain_filename(ROW)
    mod = psp / sticks.MODS_SUBDIR / sticks.brain_module(ROW)
    assert written == [brain, mod]
    assert brain.read_bytes() == b.container
    assert mod.read_text() == stick.lua_module(ROW, sticks.brain_path(ROW), b.id)
    assert f"{b.id:08X}" in mod.read_text()


def test_a_changed_build_changes_the_mod(psp):
    mod = psp / sticks.MODS_SUBDIR / sticks.brain_module(ROW)
    stick.ship(built(edit.insn("enter", 3, 0, 0)), psp)
    first = mod.read_text()
    stick.ship(built(edit.insn("enter", 3, 0, 0)), psp)
    assert mod.read_text() == first
    other = built(edit.insn("enter", 3, 1, 0))
    stick.ship(other, psp)
    assert mod.read_text() != first
    assert (psp / sticks.BRAIN_SUBDIR / sticks.brain_filename(ROW)).read_bytes() == other.container


def test_ship_and_deploy_write_the_same_files(psp, tmp_path):
    other = tmp_path / "other"
    (other / sticks.MODS_SUBDIR).mkdir(parents=True)
    p, img = program()
    stick.ship(built(), psp)
    stick.deploy(p, img, other)
    for sub, name in (
        (sticks.BRAIN_SUBDIR, sticks.brain_filename(ROW)),
        (sticks.MODS_SUBDIR, sticks.brain_module(ROW)),
    ):
        assert (psp / sub / name).read_bytes() == (other / sub / name).read_bytes()


def test_ship_refuses_a_build_with_an_error(psp):
    b = built(edit.insn("enter", 3, 0, 1))
    assert b.container is None
    with pytest.raises(ValueError, match="row 42 has errors"):
        stick.ship(b, psp)
    assert not (psp / sticks.BRAIN_SUBDIR).exists()
    assert list((psp / sticks.MODS_SUBDIR).iterdir()) == []


def test_ship_without_the_framework_writes_nothing(tmp_path):
    root = tmp_path / "PSP"
    root.mkdir()
    with pytest.raises(FileNotFoundError, match="mods directory"):
        stick.ship(built(), root)
    assert list(root.iterdir()) == []
