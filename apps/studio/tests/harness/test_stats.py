# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from mhfu_studio.harness import flags
from mhfu_studio.harness.render import Shots, contact_sheet
from mhfu_studio.harness.stats import (
    Tolerance,
    compare,
    compare_all,
    load_golden,
    measure,
    save_golden,
)

CLEAR = (0.0, 0.0, 0.0, 1.0)


def picture(fill: int = 200, rows: slice = slice(0, 4)) -> np.ndarray:
    img = np.zeros((8, 8, 4), np.uint8)
    img[..., 3] = 255
    img[rows, :, 0] = fill
    return img


def test_measure() -> None:
    s = measure(picture(), CLEAR, "r")
    assert s.size == (8, 8) and s.coverage == 0.5
    assert s.cells[:8] == (1.0,) * 8 and s.cells[8:] == (0.0,) * 8
    assert s.mean == (100.0, 0.0, 0.0, 255.0)
    assert s.histogram[0][0] == 0.5 and s.histogram[0][200 >> 4] == 0.5
    assert s.sha256 != measure(picture(201), CLEAR, "r").sha256


def test_compare() -> None:
    want = measure(picture(), CLEAR, "llvmpipe")
    assert compare(want, want) == []
    nudged = measure(picture(201), CLEAR, "llvmpipe")
    assert [f.code for f in compare(nudged, want)] == ["hash"]
    elsewhere = replace(nudged, renderer="Metal")
    assert [f.level for f in compare(elsewhere, want)] == ["info"]
    moved = measure(picture(rows=slice(4, 8)), CLEAR, "Metal")
    codes = {f.code for f in compare(moved, want) if f.level == "error"}
    assert codes == {"cells"}
    loose = Tolerance(cells=1.0)
    assert not [f for f in compare(moved, want, loose) if f.level == "error"]
    darker = measure(picture(150, slice(0, 8)), CLEAR, "Metal")
    assert {"coverage", "mean", "histogram"} <= {f.code for f in compare(darker, want)}
    small = measure(picture()[:4], CLEAR, "llvmpipe")
    assert [f.code for f in compare(small, want)] == ["size"]


def test_golden_round_trip(tmp_path: Path) -> None:
    stats = {"a": measure(picture(), CLEAR, "r"), "b": measure(picture(9), CLEAR, "r")}
    path = save_golden(tmp_path / "g" / "x.json", stats)
    assert load_golden(path) == stats
    found = compare_all({"a": stats["a"], "c": stats["b"]}, stats)
    assert {(f.where, f.code) for f in found} == {("b", "missing"), ("c", "new")}
    path.write_text('{"version": 0, "images": {}}')
    with pytest.raises(ValueError, match="version"):
        load_golden(path)


def test_contact_sheet() -> None:
    sheet = contact_sheet([picture(), picture(9)[:6]], ["a", "b"], columns=1, pad=2)
    assert sheet.shape == (2 + 2 * (8 + 2), 2 + 8 + 2, 4)
    with pytest.raises(ValueError):
        contact_sheet([])


def args(**kw: object) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    flags.add_flags(p)
    return p.parse_args([]) if not kw else argparse.Namespace(**{**vars(p.parse_args([])), **kw})


def test_size_flag() -> None:
    assert flags.size("640x480") == (640, 480)
    for bad in ("640", "0x10", "axb"):
        with pytest.raises(argparse.ArgumentTypeError):
            flags.size(bad)


def test_out_must_be_in_the_mount(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(flags.MOUNT_ENV, str(tmp_path / "repo"))
    assert flags.check_out(tmp_path / "repo" / "out") == tmp_path / "repo" / "out"
    with pytest.raises(ValueError, match="vanish"):
        flags.check_out(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="vanish"):
        flags.check_args(args(out=tmp_path / "elsewhere"))
    with pytest.raises(ValueError, match="nothing to do"):
        flags.check_args(args())
    with pytest.raises(ValueError, match="--update needs"):
        flags.check_args(args(out=tmp_path / "repo", update=True))


def test_finish(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(flags.MOUNT_ENV, raising=False)
    shots = Shots({"a": picture(), "b": picture(9)}, "r", CLEAR)
    golden = tmp_path / "golden.json"
    assert (
        flags.finish(args(out=tmp_path / "png", sheet=True, golden=golden, update=True), shots) == 0
    )
    assert sorted(p.name for p in (tmp_path / "png").iterdir()) == ["a.png", "b.png", "sheet.png"]
    assert flags.finish(args(golden=golden), shots) == 0
    changed = Shots({"a": picture(), "b": picture(10)}, "r", CLEAR)
    assert flags.finish(args(golden=golden), changed) == 1
    assert "error b: the pixels changed" in capsys.readouterr().err
