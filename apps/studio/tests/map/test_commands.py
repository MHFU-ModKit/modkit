# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from mhfu.files import Extracted
from mhfu_studio.cli import main
from mhfu_studio.harness import flags
from mhfu_studio.map.document import MapDocument
from mhfu_studio.stage import live
from PIL import Image

MOVE = {"op": "move", "sub": 0, "group": 0, "vertices": [0, 1, 2], "by": [0, 40, 0]}
BOX = {"op": "collision", "group": None, "solid_box": [100, 0, 100, 300, 200, 300]}


@pytest.fixture
def doc(game: Extracted, doc_dir: Path) -> Path:
    d = MapDocument("t", 0, doc_dir)
    d.ensure_stage(139).ops.extend([MOVE, BOX])
    d.ensure_stage(98).ops.append({"op": "texture", "slot": 1, "rgb": [1, 2, 3]})
    d.save()
    return doc_dir


def run(*argv: str) -> int:
    return main(list(argv))


def test_cli_stays_light():
    run_code = "import sys; from mhfu_studio.cli import parser; parser(); "
    run_code += "print([m for m in sys.modules if m.startswith(('moderngl', 'imgui', 'numpy'))])"
    import subprocess

    out = subprocess.run([sys.executable, "-c", run_code], capture_output=True, text=True)
    assert out.stdout.strip() == "[]", out.stderr


def test_inject_dry(game: Extracted, doc: Path, capsys: pytest.CaptureFixture[str]):
    data = str(game.root)
    assert run("map", "inject", str(doc), "--data", data, "--catch", "0", "--dry") == 0
    out = capsys.readouterr().out
    assert "st139: 2 op(s) in st139.json" in out and "st098: 1 op(s) in st098.json" in out
    assert "sub 0: 1 runs" in out and "collision: 0 moved, 12 added" in out
    assert "textures: 1 runs" in out
    assert (
        run("map", "inject", str(doc / "map.toml"), "--stage", "98", "--data", data, "--dry") == 0
    )
    assert "st139" not in capsys.readouterr().out
    assert run("map", "inject", str(doc), "--stage", "97", "--data", data, "--dry") == 1
    assert "no stage st097" in capsys.readouterr().err


def test_inject_refuses_a_bad_list(game: Extracted, doc_dir: Path):
    d = MapDocument("t", 0, doc_dir)
    d.ensure_stage(139).ops.append({"op": "bogus"})
    d.save()
    assert main(["map", "inject", str(doc_dir), "--data", str(game.root), "--dry"]) == 1


def test_inject_live(game: Extracted, doc: Path, monkeypatch: pytest.MonkeyPatch):
    """The live path against stand-ins: what each stage is pushed with."""
    from mhfu import memory
    from ppsspp_debug import Client

    calls: list[tuple[Any, ...]] = []

    @contextmanager
    def connect(port: int | None = None) -> Any:
        calls.append(("connect", port))
        yield "client"

    monkeypatch.setattr(Client, "connect", staticmethod(connect))
    monkeypatch.setattr(memory, "Live", lambda client: "mem")
    monkeypatch.setattr(live, "pac_address", lambda mem, n: 0x1000 if n == 139 else None)  # noaddr

    def push(client: str, mem: str, p: live.Push, **kw: Any) -> None:
        calls.append(("run", p.stage.number, kw["catch_for"], kw["hold_for"], kw["undo_file"]))

    monkeypatch.setattr(live, "run", push)
    monkeypatch.setattr(live, "restore", lambda mem, sf: calls.append(("restore", sf.number)))
    data = str(game.root)
    assert run("map", "inject", str(doc), "--data", data, "--catch", "5", "--port", "9") == 0
    undo = doc / ".inject" / "st139_collision_undo.json"
    assert calls == [("connect", 9), ("run", 139, 5.0, 0.0, undo)]
    calls.clear()
    assert run("map", "inject", str(doc), "--stage", "98", "--data", data, "--hold", "3") == 0
    assert calls[1][:4] == ("run", 98, 0.0, 3.0)  # no mesh edit: no catch
    calls.clear()
    assert run("map", "inject", str(doc), "--stage", "139", "--data", data, "--restore") == 0
    assert calls == [("connect", None), ("restore", 139)]


def test_render_map_needs_somewhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.delenv(flags.MOUNT_ENV, raising=False)
    assert run("render", "map", "--stage", "98", "--data", str(tmp_path)) == 1
    assert "nothing to do" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        run("render", "map", "--view", "iso")


def test_render_map(
    gl: Any,
    shipped: Extracted,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.delenv(flags.MOUNT_ENV, raising=False)
    golden = Path(__file__).parent / "golden" / "st098.json"
    data = str(shipped.root)
    args = ["render", "map", "--stage", "98", "--data", data, "--size", "320x200"]
    args += ["--view", "iso", "--view", "top", "--view", "hunter"]
    assert run(*args, "--golden", str(golden), "-o", str(tmp_path / "a"), "--sheet") == 0
    names = sorted(p.name for p in (tmp_path / "a").glob("*.png"))
    assert names == ["sheet.png", "st098_hunter.png", "st098_iso.png", "st098_top.png"]
    assert "match" in capsys.readouterr().out
    sheet = tmp_path / "row.png"
    assert (
        run(
            "render",
            "map",
            "--row",
            "11",
            "--data",
            data,
            "--size",
            "80x50",
            "--contact",
            str(sheet),
        )
        == 0
    )
    assert Image.open(sheet).size[0] > 80 * 3
    two = ["--view", "iso", "--view", "top"]
    assert run("render", "map", "--row", "11", "--data", data, "-o", str(tmp_path), *two) == 1
    assert "one view" in capsys.readouterr().err


def test_render_map_with_a_document(
    gl: Any, shipped: Extracted, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv(flags.MOUNT_ENV, raising=False)
    d = MapDocument("d", 11, tmp_path / "doc")
    d.ensure_stage(98).ops.append({"op": "texture", "slot": 7, "rgb": [255, 0, 0]})
    d.save()
    data = str(shipped.root)
    base = ["render", "map", "--stage", "98", "--data", data, "--size", "160x100"]
    assert run(*base, "-o", str(tmp_path / "plain")) == 0
    assert run(*base, "--doc", str(tmp_path / "doc"), "-o", str(tmp_path / "red")) == 0
    a = Image.open(tmp_path / "plain" / "st098_iso.png").tobytes()
    b = Image.open(tmp_path / "red" / "st098_iso.png").tobytes()
    assert a != b
    assert json.loads((tmp_path / "doc" / "st098.json").read_text())[0]["slot"] == 7
