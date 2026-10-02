# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Send to game: the map workspace's push job, read back by the push command, no game run."""

import io
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from mhfu.files import Extracted
from mhfu_studio.cli import main, parser
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.document import MapDocument
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.shell.workspace import Job
from mhfu_studio.stage import live
from mhfu_studio.stage.commands import read_list
from mhfu_studio.stage.live import QUEST_CATCH

MOVE = {"op": "move", "sub": 0, "group": 0, "vertices": [0, 1, 2], "by": [0, 40, 0]}
CLIMB = {"op": "collision", "group": None, "chunk": 0, "tri": 0, "flags": {"material": 9}}


def loaded(game: Extracted, atlas: Atlas, stage: int = 139) -> MapWorkspace:
    ws = MapWorkspace(game, atlas)
    assert ws.load_stage(stage)
    return ws


def stdin(monkeypatch: pytest.MonkeyPatch, job: Job) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(job.stdin.decode()))


def test_blockers(game: Extracted, atlas: Atlas, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MHFU_DATA", raising=False)
    assert (MapWorkspace().send_blocker() or "").startswith("no game files: ")
    ws = MapWorkspace(game, atlas)
    assert ws.send_blocker() == "no section loaded" == ws.push_blocker(restore=True)
    ws.load_stage(139)
    assert ws.send_blocker() == "no edits to st139 yet" and ws.push_blocker(restore=True) is None
    assert ws.session is not None
    ws.session.ops.append({"op": "bogus"})
    assert (ws.send_blocker() or "").startswith("op 0 has an error: `bogus` is not an op")
    ws.session.ops[:] = [MOVE]
    assert ws.send_blocker() is None
    studio = Studio([ws])
    studio.job = Job("x", ())
    assert studio.send_blocker() == "busy: x"
    with pytest.raises(ValueError, match="no edits"):
        loaded(game, atlas).send()


def test_job_round_trips(
    game: Extracted,
    atlas: Atlas,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws = loaded(game, atlas)
    assert ws.session is not None and ws.session.push([MOVE]) and ws.doc.directory is None
    job = ws.send()
    assert job.title == "push st139 into the game" and job.argv[:2] == ("map", "push")
    args = parser().parse_args(list(job.argv))
    assert (args.stage, args.catch, args.hold, args.base) == (139, 0.0, 0.0, None)
    assert args.data == game.root and not (args.mesh or args.collision or args.textures)
    stdin(monkeypatch, job)
    assert read_list(args) == (ws.session.ops, Path("."))
    stdin(monkeypatch, job)
    assert main([*job.argv, "--dry"]) == 0
    assert "sub 0: 1 runs" in capsys.readouterr().out


def test_job_follows_the_section(game: Extracted, doc_dir: Path) -> None:
    ws = loaded(game, Atlas(game, [(139,), (98,)]), 98)
    assert ws.catch == QUEST_CATCH and not ws.in_village() and ws.session is not None
    ws.session.ops += [MOVE, CLIMB]
    argv = ws.push_job().argv
    assert argv[argv.index("--catch") + 1] == "120" and argv[-2:] == ("--hold", "120")
    ws.catch = 30
    mesh = ws.push_job(("mesh",))
    assert mesh.title == "push st098's mesh into the game" and "--hold" not in mesh.argv
    assert mesh.argv[mesh.argv.index("--catch") + 1] == "30" and "--mesh" in mesh.argv
    ws.doc.save(doc_dir)
    argv = ws.push_job(("collision",)).argv
    assert argv[argv.index("--base") + 1] == str(doc_dir) and argv[-2:] == ("--hold", "30")
    back = ws.push_job(restore=True)
    assert back.title == "restore st098 in the game" and back.argv[-1] == "--restore"
    assert back.stdin == b"" and "--ops" not in back.argv
    ws.load_stage(139)
    assert ws.catch == 0 and ws.in_village()


def test_every_route_shares_the_undo(
    game: Extracted, atlas: Atlas, doc_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mhfu import memory
    from ppsspp_debug import Client

    @contextmanager
    def connect(port: int | None = None) -> Iterator[str]:
        yield "client"

    undos: list[Path] = []

    def run(client: str, mem: str, p: live.Push, **kw: Any) -> None:
        undos.append(kw["undo_file"])

    monkeypatch.setattr(Client, "connect", staticmethod(connect))
    monkeypatch.setattr(memory, "Live", lambda client: "mem")
    monkeypatch.setattr(live, "pac_address", lambda mem, n: 0x1000)
    monkeypatch.setattr(live, "run", run)
    ws = loaded(game, atlas)
    assert ws.session is not None
    ws.session.ops.append(MOVE)
    job = ws.send()
    stdin(monkeypatch, job)
    assert main(list(job.argv)) == 0
    doc = MapDocument("t", 0, doc_dir)
    doc.ensure_stage(139).ops.append(MOVE)
    doc.save()
    data = ["--data", str(game.root), "--catch", "0"]  # no map table to find the village in
    assert main(["map", "inject", str(doc_dir), *data]) == 0
    ops = tmp_path / "elsewhere" / "st139.json"
    ops.parent.mkdir()
    ops.write_text(json.dumps([MOVE]))
    assert main(["map", "push", "--stage", "139", "--ops", str(ops), *data]) == 0
    assert undos == [live.undo_path(139)] * 3
    assert undos[0].is_relative_to(tmp_path / "state")
