# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A capacity in addresses.toml (OWN_MOVES, a pool, a move's keys) is raised by changing its
const: the C structs that mirror the generated layout still agree with it, or the host build of
their static_asserts fails."""

import shutil
import subprocess
import tomllib
from importlib.resources import files
from pathlib import Path

import pytest
from mhfu import addresses

FRAMEWORK = Path(__file__).parents[1]
SOURCES = ("em_vhook.cpp", "move.cpp", "steer.cpp")


@pytest.mark.parametrize(
    ("const", "value"),
    [
        ("OWN_MOVES", 160),
        ("OWN_MOVES", 97),
        ("EM_KEYS", 8000),
        ("EM_SAMPLES", 901),
        ("EM_LUNGES", 33),
        ("STEER_KEYS", 301),
        ("STEER_SAMPLES", 201),
    ],
)
def test_a_raised_capacity_agrees_with_the_c_structs(
    const: str, value: int, tmp_path: Path
) -> None:
    cc = shutil.which("c++")
    if cc is None:
        pytest.skip("no host C++ compiler")
    data = tomllib.loads(files("mhfu").joinpath("addresses.toml").read_text(encoding="utf-8"))
    data["const"][const]["value"] = value
    (tmp_path / "addresses.gen.h").write_text(addresses.render_c(addresses.parse(data)))
    for src in SOURCES:
        subprocess.run(
            [cc, "-std=gnu++17", "-fsyntax-only", "-Wall", "-Werror", "-DMHFU_HOST"]
            + [f"-I{FRAMEWORK / 'include'}", f"-I{FRAMEWORK / 'src' / 'core'}", f"-I{tmp_path}"]
            + [str(FRAMEWORK / "src" / "core" / src)],
            check=True,
        )
