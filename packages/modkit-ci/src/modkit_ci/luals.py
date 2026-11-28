# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The pinned LuaLS: locate it, dump its doc JSON, run its check."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from modkit_ci import registry

VERSION: Final = "3.19.1"
SHA256: Final = {
    "linux-x64": "e9235d2d72ef55bc41cf8c99cda2ed64777682024b4bb81f5dea425060c5cbb8",
    "darwin-arm64": "0bc077f4447f076b4c92c14e9fd303f5b569eda2ec74b4dca2b55f75fae2e90c",
}

ENV_BIN: Final = "LUALS_BIN"
EXE: Final = "lua-language-server"
ADDRESSES: Final = "framework/build/gen/addresses.gen.lua"
_URL: Final = (
    "https://github.com/LuaLS/lua-language-server/releases/download/"
    "{v}/lua-language-server-{v}-{plat}.tar.gz"
)
_TIMEOUT: Final = 120

Opener = Callable[[str], bytes]


class LualsMissing(RuntimeError):
    """No usable LuaLS binary."""


class LualsHashError(LualsMissing):
    """A downloaded LuaLS archive does not match the pinned sha256."""


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
        data: bytes = resp.read()
    return data


def platform_key() -> str | None:
    """The release-asset suffix of this machine, or None if there is no pinned hash for it."""
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    os_name = {"linux": "linux", "darwin": "darwin"}.get(sys.platform)
    key = f"{os_name}-{arch}"
    return key if key in SHA256 else None


def cache_dir() -> Path:
    """Where the pinned release is unpacked."""
    return Path.home() / ".cache" / "modkit-ci" / "luals" / VERSION


def _download(key: str, dest: Path, opener: Opener) -> Path:
    data = opener(_URL.format(v=VERSION, plat=key))
    got = hashlib.sha256(data).hexdigest()
    if got != SHA256[key]:
        raise LualsHashError(f"LuaLS {VERSION} {key}: sha256 {got}, pinned {SHA256[key]}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".luals-"))
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            tar.extractall(tmp, filter="data")
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dest / "bin" / EXE


def _version_of(exe: Path) -> str | None:
    """The version `exe --version` prints, or None."""
    try:
        run = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return run.stdout.strip() or None


def binary(opener: Opener = _fetch, cache: Path | None = None) -> Path:
    """$LUALS_BIN, else the verified cached download, else a PATH binary of exactly VERSION."""
    env = os.environ.get(ENV_BIN)
    if env:
        path = Path(env)
        if not path.is_file():
            raise LualsMissing(f"${ENV_BIN} is not a file: {env}")
        return path
    cache = cache or cache_dir()
    cached = cache / "bin" / EXE
    if cached.is_file():
        return cached
    key = platform_key()
    failure = f"no pinned LuaLS {VERSION} for this platform"
    if key:
        try:
            return _download(key, cache, opener)
        except (urllib.error.URLError, OSError) as err:
            failure = f"download of LuaLS {VERSION} failed: {err}"
    found = shutil.which(EXE)
    if found:
        have = _version_of(Path(found))
        if have == VERSION:
            return Path(found)
        failure += f"; {found} is version {have or 'unknown'}, not {VERSION}"
    raise LualsMissing(f"no LuaLS {VERSION}: {failure}. Set ${ENV_BIN} or allow the download")


def _run(args: list[str], root: Path, quiet: bool = False) -> int:
    with tempfile.TemporaryDirectory() as log:
        cmd = [str(binary()), f"--logpath={log}", *args]
        return subprocess.run(cmd, cwd=root, check=False, capture_output=quiet).returncode


def doc(root: Path, out: Path) -> list[dict[str, Any]]:
    """LuaLS `--doc` JSON of `root`, written to `out`."""
    with tempfile.TemporaryDirectory() as tmp:
        code = _run([f"--doc={root}", f"--doc_out_path={tmp}"], root, quiet=True)
        produced = Path(tmp) / "doc.json"
        if code != 0 or not produced.is_file():
            raise RuntimeError(f"LuaLS --doc failed on {root} (exit {code})")
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, out)
    data: list[dict[str, Any]] = json.loads(out.read_text(encoding="utf-8"))
    return data


def render_addresses(root: Path) -> None:
    """Write the generated address table LuaLS reads as a library."""
    target = root / ADDRESSES
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "mhfu.addresses", "lua", "-o", str(target)]
    subprocess.run(cmd, cwd=root, check=True)


def check(root: Path) -> int:
    """Render the generated addresses, then LuaLS `--check` at Warning; its exit code."""
    render_addresses(root)
    return _run([f"--check={root}", "--checklevel=Warning"], root)


def _cmd(args: argparse.Namespace) -> int:
    try:
        if args.action == "path":
            print(binary())
            return 0
        if args.action == "cache-dir":
            print(cache_dir())
            return 0
        root = Path(args.root).resolve() if args.root else registry.find_root()
        if args.action == "check":
            return check(root)
        if not args.output:
            print("luals doc: -o FILE is required", file=sys.stderr)
            return 2
        doc(root, Path(args.output))
    except (LualsMissing, RuntimeError, subprocess.CalledProcessError) as err:
        print(f"luals: {err}", file=sys.stderr)
        return 1
    return 0


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `luals` command."""
    p = sub.add_parser("luals", help="LuaLS helper: path, cache-dir, check, doc")
    p.add_argument(
        "action", nargs="?", choices=["path", "cache-dir", "check", "doc"], default="path"
    )
    p.add_argument("root", nargs="?")
    p.add_argument("-o", "--output")
    p.set_defaults(func=_cmd)
