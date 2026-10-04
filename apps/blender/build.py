# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Build the extension zip into dist/; `test` also runs tests/ inside Blender.

uv run apps/blender/build.py [build | test [PYTEST_ARGS]] [--blender PATH]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = HERE / "src" / "mhfu_blender"
DIST = HERE / "dist"
CACHE = HERE / ".cache"
PROJECT = tomllib.loads((HERE / "pyproject.toml").read_text())["project"]
ID = tomllib.loads((SOURCE / "blender_manifest.toml").read_text())["id"]
REPO = "user_default"  # Blender's built-in local repository
PROVIDED = {"numpy"}  # Blender's own; a bundled copy would shadow it for every extension
MACOS = "/Applications/Blender.app/Contents/MacOS/Blender"


def run(*args: str | Path, env: dict[str, str] | None = None, capture: bool = False) -> str:
    sys.stdout.flush()  # keeps our lines in order with the command's
    pipe = subprocess.PIPE if capture else None
    done = subprocess.run(
        [str(a) for a in args], check=True, text=True, cwd=ROOT, env=env, stdout=pipe
    )
    return done.stdout or ""


def extension(blender: str, *args: str | Path, env: dict[str, str] | None = None) -> None:
    run(blender, "--command", "extension", *args, env=env)


def locked(*args: str) -> list[dict[str, Any]]:
    """uv.lock's packages for these `uv export` arguments, as pylock.toml entries."""
    out = run(
        "uv", "export", "--frozen", "--format=pylock.toml", "--no-emit-project", *args, capture=True
    )
    packages: list[dict[str, Any]] = tomllib.loads(out)["packages"]
    return packages


def stage_wheels(wheels: Path) -> None:
    """The dependency closure as pure wheels: workspace members built, the rest as uv.lock pins."""
    members, pins, skipped = [], [], set()
    for package in locked("--package", PROJECT["name"], "--no-default-groups"):
        name = package["name"]
        pure = [w for w in package.get("wheels", []) if w["url"].endswith("-none-any.whl")]
        if "directory" in package:
            members.append(name)
        elif pure and name not in PROVIDED:
            pins.append(f"{name} @ {pure[0]['url']} --hash=sha256:{pure[0]['hashes']['sha256']}\n")
        else:
            skipped.add(name)
    for name in members:
        run("uv", "build", "--quiet", "--package", name, "--wheel", "--out-dir", wheels)
    requirements = wheels.parent / "requirements.txt"
    requirements.write_text("".join(pins))
    run("uvx", "pip", "download", "--quiet", "--no-deps", "--dest", wheels, "-r", requirements)
    print("skipped (Blender's own, or no pure wheel):", ", ".join(sorted(skipped)))


def build(blender: str) -> Path:
    """Stage the source with its wheels, build the zip and validate it."""
    archive = DIST / f"{ID}-{PROJECT['version']}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / ID
        shutil.copytree(SOURCE, stage, ignore=shutil.ignore_patterns("__pycache__"))
        stage_wheels(stage / "wheels")
        wheels = sorted(f"./wheels/{w.name}" for w in (stage / "wheels").glob("*.whl"))
        print("bundled:", ", ".join(w.removeprefix("./wheels/") for w in wheels))
        manifest = stage / "blender_manifest.toml"
        text = manifest.read_text()
        table = text.index("\n[") + 1  # top-level keys go before the first table
        keys = f'version = "{PROJECT["version"]}"\nwheels = {json.dumps(wheels)}\n\n'
        manifest.write_text(text[:table] + keys + text[table:])
        DIST.mkdir(exist_ok=True)
        extension(blender, "build", "--source-dir", stage, "--output-filepath", archive)
    extension(blender, "validate", archive)
    return archive


def _marker(package: dict[str, Any]) -> str:
    return f"; {package['marker']}" if "marker" in package else ""


def pytest_dir(python: str) -> Path:
    """pytest at uv.lock's pins for Blender's Python, installed once under .cache/."""
    pins = [p for p in locked("--only-group", "dev", "--no-emit-workspace") if "version" in p]
    version = next(p["version"] for p in pins if p["name"] == "pytest")
    target = CACHE / f"pytest-{version}-py{python}"
    if target.is_dir():
        return target
    CACHE.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE) as tmp:  # renamed into place once complete
        constraints = Path(tmp) / "constraints.txt"
        constraints.write_text("".join(f"{p['name']}=={p['version']}{_marker(p)}\n" for p in pins))
        install = Path(tmp) / "pytest"
        run("uv", "pip", "install", "--quiet", "--only-binary=:all:", "--target", install,
            "--python-version", python, "-c", constraints, "pytest")  # fmt: skip
        install.rename(target)
    return target


def test(blender: str, archive: Path, pytest_args: list[str]) -> int:
    """Install the zip into a throwaway profile and run pytest inside Blender; pytest's code."""
    with tempfile.TemporaryDirectory() as profile:
        env = {**os.environ, "BLENDER_USER_RESOURCES": profile}
        extension(blender, "install-file", "-r", REPO, "-e", archive, env=env)
        probe = "import sys; print('PYTHON=%d.%d' % sys.version_info[:2])"
        out = run(blender, "-b", "--factory-startup", "--python-expr", probe, env=env, capture=True)
        python = re.search(r"^PYTHON=(\S+)$", out, re.M)
        assert python, out
        paths = [str(pytest_dir(python[1])), str(ROOT / "packages" / "modkit-testing" / "src")]
        args = [str(HERE / "tests"), "-p", "modkit_testing", "-p", "no:cacheprovider"]
        args += ["-W", "ignore::pytest.PytestConfigWarning", *pytest_args]  # root's asyncio keys
        expr = f"import sys; sys.path[:0] = {paths}; import pytest; sys.exit(pytest.main({args}))"
        env["MHFU_BLENDER_EXTENSION"] = f"bl_ext.{REPO}.{ID}"
        command = [blender, "-b", "--factory-startup", "--python-exit-code", "1"]
        return subprocess.run([*command, "--python-expr", expr], cwd=ROOT, env=env).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", choices=["build", "test"], default="build")
    default = os.environ.get("BLENDER") or (MACOS if sys.platform == "darwin" else "blender")
    parser.add_argument("--blender", default=default, help="its binary (default: %(default)s)")
    args, pytest_args = parser.parse_known_args()
    if pytest_args and args.command != "test":
        parser.error(f"unrecognized arguments: {' '.join(pytest_args)}")
    archive = build(args.blender)
    print(f"{archive} ({archive.stat().st_size / 1e6:.1f} MB)")
    return test(args.blender, archive, pytest_args) if args.command == "test" else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:  # the command has printed why
        sys.exit(error.returncode)
