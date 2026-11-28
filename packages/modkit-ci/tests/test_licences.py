# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Notices of the framework zip and licence metadata of every module."""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest
from modkit_ci import licences, registry

ROOT = registry.find_root(Path(__file__))
REGISTRY = registry.load(ROOT)
# split so the REUSE hook does not read this file as carrying an identifier
_TAG = "SPDX-" + "License-Identifier"
_LINK = (
    "LIBS = $(PSPSDK)/lib/prxexports.o -llua -lm -Wl,--start-group -lpthreadglue \\\n"
    "       -lc -Wl,--end-group -lpspsdk\n"
)


def test_linked_libs_reads_the_link_line() -> None:
    assert licences.linked_libs(_LINK) == {"prxexports", "lua", "m", "pthreadglue", "c", "pspsdk"}
    assert licences.linked_libs("LIBS = -lone\nLIBS += -ltwo\nOTHER = -lthree\n") == {"one", "two"}


def test_linked_library_without_notice_is_found(tmp_path: Path) -> None:
    (tmp_path / "framework/third_party/tlsf").mkdir(parents=True)
    (tmp_path / licences.NOTICES).write_text(
        '[[component]]\nname = "A"\nsource = "s"\nspdx = "MIT"\ntext = true\n'
        'libs = ["lua"]\ncopyright = ["c"]\n',
        encoding="utf-8",
    )
    (tmp_path / licences.MAKEFILE).write_text("LIBS = -llua -lmystery\n", encoding="utf-8")
    assert licences.uncovered(tmp_path) == ["mystery", "third_party/tlsf"]


def test_every_linked_library_has_a_notice() -> None:
    assert licences.uncovered(ROOT) == []


def test_notices_name_the_known_libraries() -> None:
    parts = {c.name.split()[0]: c for c in licences.components(ROOT)}
    assert parts["Lua"].spdx == "MIT"
    assert parts["TLSF"].spdx == "BSD-3-Clause"
    assert parts["PSPSDK"].spdx == "BSD-3-Clause"
    assert "Copyright (c) 2006-2016, Matthew Conte" in parts["TLSF"].copyright


def test_tlsf_notice_matches_the_vendored_header() -> None:
    header = (ROOT / "framework/third_party/tlsf/tlsf.h").read_text(encoding="utf-8")
    tlsf = next(c for c in licences.components(ROOT) if c.name.startswith("TLSF"))
    for line in tlsf.copyright:
        assert line.replace("Copyright (c) ", "") in header


def test_licence_texts_exist() -> None:
    for spdx in licences.licence_ids(licences.components(ROOT)):
        assert (ROOT / "LICENSES" / f"{spdx}.txt").is_file()


def test_framework_sources_use_shipped_licences() -> None:
    shipped = set(licences.licence_ids(licences.components(ROOT)))
    found: set[str] = set()
    for path in (ROOT / "framework").rglob("*"):
        if path.is_file() and "build" not in path.relative_to(ROOT / "framework").parts:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            found.update(re.findall(_TAG + r": ([\w.+-]+)", text))
    assert found <= shipped


def _reuse_licence(path: str) -> str:
    """The licence REUSE.toml gives a module directory: the last matching annotation."""
    found = ""
    for note in tomllib.loads((ROOT / "REUSE.toml").read_text(encoding="utf-8"))["annotations"]:
        patterns = [note["path"]] if isinstance(note["path"], str) else note["path"]
        if any(p == "**" or path.startswith(p.removesuffix("/**") + "/") for p in patterns):
            found = note["SPDX-License-Identifier"]
    return found


@pytest.mark.parametrize("module", [m for m in REGISTRY.modules if m.release_type == "python"])
def test_pyproject_declares_the_reuse_licence(module: registry.Module) -> None:
    project = tomllib.loads((ROOT / module.path / "pyproject.toml").read_text())["project"]
    assert project["license"] == _reuse_licence(module.path + "/x")
    assert project["license-files"] == ["LICENSE"]
    link = ROOT / module.path / "LICENSE"
    assert link.resolve() == ROOT / "LICENSES" / f"{project['license']}.txt"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv builds the wheels")
@pytest.mark.parametrize("module", [m for m in REGISTRY.modules if m.release_type == "python"])
def test_built_wheel_names_its_licence(module: registry.Module, tmp_path: Path) -> None:
    project = tomllib.loads((ROOT / module.path / "pyproject.toml").read_text())["project"]
    cmd = ["uv", "build", "--package", project["name"], "--wheel"]
    subprocess.run([*cmd, "--out-dir", str(tmp_path)], cwd=ROOT, check=True, capture_output=True)
    (wheel,) = tmp_path.glob("*.whl")
    with zipfile.ZipFile(wheel) as zf:
        metadata = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        text = zf.read(metadata).decode()
        names = zf.namelist()
        assert f"License-Expression: {project['license']}" in text
        assert "License-File: LICENSE" in text
        licence = next(n for n in names if n.endswith("/licenses/LICENSE"))
        want = (ROOT / "LICENSES" / f"{project['license']}.txt").read_bytes()
        assert zf.read(licence) == want
