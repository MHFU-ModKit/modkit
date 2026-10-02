# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Statistics goldens of posed renders: written in the container (apps/studio/docker, where the
hash must match), compared within tolerance elsewhere. `STUDIO_GOLDEN_UPDATE=1` rewrites them.

The synthetic rig's golden runs everywhere; the perturbation tests prove it fails on an FK, a
skinning and a bone-palette regression even where only the statistics are compared."""

import dataclasses
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from mhfu_port import fk
from mhfu_studio import cli
from mhfu_studio.harness import flags
from mhfu_studio.harness.render import Shots
from mhfu_studio.harness.stats import Stats, compare_all, load_golden, measure, save_golden
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.render import shots

GOLDEN = Path(__file__).parent / "golden"
SIZE = (320, 240)
UPDATE = bool(os.environ.get("STUDIO_GOLDEN_UPDATE"))
#: no ground: its screen-derivative lines near the horizon are the renderer's, not the rig's
SYNTHETIC = (
    shots.Options(views=("three", "side", "top"), clip=1, frames=(0, 10, 20), grid=False),
    shots.Options(views=("three",), clip=2, frames=(15,), grid=False),
    shots.Options(views=("three",), bind=True, grid=False),
)
#: what a port's and the Tigrex's golden draws: the opening clip at three frames, two views
MONSTER = shots.Options(views=("three", "side"), frames=(0, 20, 40), grid=False)


def stats_of(gl: Any, scene: Scene, *options: shots.Options) -> dict[str, Stats]:
    out: dict[str, Stats] = {}
    for o in options:
        s: Shots = shots.render(scene, o, SIZE, 4, gl)
        out |= {k: measure(v, s.clear, s.renderer) for k, v in s.images.items()}
    return out


def check(stats: dict[str, Stats], name: str) -> None:
    path = GOLDEN / f"{name}.json"
    if UPDATE:
        save_golden(path, stats)
        return
    errors = [f for f in compare_all(stats, load_golden(path)) if f.level != "info"]
    assert errors == [], "\n".join(map(str, errors))


def elsewhere(stats: dict[str, Stats]) -> dict[str, Stats]:
    """The same statistics claiming another renderer: only the statistics are compared."""
    return {k: dataclasses.replace(s, renderer="elsewhere") for k, s in stats.items()}


def test_synthetic(gl: Any, rig: Scene) -> None:
    check(stats_of(gl, rig, *SYNTHETIC), "synthetic")


@pytest.fixture
def broken_fk(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Euler composed in the wrong order: a transposed rotation."""
    right = fk.euler_xyz
    monkeypatch.setattr(fk, "euler_xyz", lambda r: np.swapaxes(right(r), -1, -2))
    yield


@pytest.fixture
def broken_skinning(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """World matrices blended without the bind inverse."""
    monkeypatch.setattr(fk.Rig, "deform", lambda self, world: np.asarray(world, np.float64))
    yield


def shifted_palette(scene: Scene) -> Scene:
    """Every influence one joint further along: a bone palette off by one."""
    sk = scene.merged
    sk.joints[:] = np.where(sk.weights > 0, (sk.joints + 1) % scene.rig.n, sk.joints)
    return scene


@pytest.mark.parametrize("fault", ["fk", "skinning", "palette"])
def test_the_golden_catches(
    gl: Any, make_rig: Callable[..., Scene], fault: str, request: pytest.FixtureRequest
) -> None:
    if UPDATE:
        pytest.skip("rewriting the goldens")
    scene = make_rig()
    if fault == "palette":
        shifted_palette(scene)
    else:
        request.getfixturevalue(f"broken_{'fk' if fault == 'fk' else 'skinning'}")
    got = elsewhere(stats_of(gl, scene, *SYNTHETIC))
    found = [
        f for f in compare_all(got, load_golden(GOLDEN / "synthetic.json")) if f.level == "error"
    ]
    posed = {f.where for f in found}
    assert found and posed - {"bind_three"}, f"{fault}: the statistics did not move"


def test_render_command(
    gl: Any, rig_bytes: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(flags.MOUNT_ENV, raising=False)
    pac = tmp_path / "rig.bin"
    pac.write_bytes(rig_bytes)
    out = tmp_path / "out"
    args = ["render", "monster", str(pac), "--slot", "1", "--frames", "0,20", "--view", "three"]
    assert cli.main([*args, "--size", "160x120", "-o", str(out), "--sheet"]) == 0
    assert sorted(p.name for p in out.glob("*.png")) == [
        "clip_01_f0_three.png",
        "clip_01_f20_three.png",
        "sheet.png",
    ]
    assert cli.main(["render", "monster", str(pac), "--bind", "-o", str(out)]) == 0
    assert (out / "bind_three.png").is_file()
    assert cli.main([*args, "--clip", "x", "-o", str(out)]) == 1
    assert cli.main(["render", "monster", str(pac), "--clip", "nope", "-o", str(out)]) == 1
    assert cli.main(["render", "monster", str(pac), "--only", "rest", "--hilite", "3"]) == 1


def test_tigrex(gl: Any, mhfu_data: Path) -> None:
    check(stats_of(gl, Scene.from_path(mhfu_data / "file_06185.bin"), MONSTER), "tigrex")


@pytest.mark.parametrize("name", ["zinogre", "brute_tigrex"])
def test_port(gl: Any, built: Callable[[str], bytes], name: str) -> None:
    check(stats_of(gl, Scene.from_bytes(built(name), name), MONSTER), name)
