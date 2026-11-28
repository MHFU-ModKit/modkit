# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Release assets of one module: wheel, framework zip, Blender extension, docs zip."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from modkit_ci import docs, licences, luals, registry
from modkit_ci.archive import write_zip
from modkit_ci.registry import Module, Registry

FRAMEWORK: Final = luals.FRAMEWORK
BLENDER: Final = "blender"
PRXS: Final = ("mhfu_framework.prx", "mhfu_boot.prx")
PLUGIN_DIR: Final = "PSP/PLUGINS/mhfu_framework"
NOTICE_FILE: Final = "THIRD_PARTY_NOTICES.txt"
LICENSES: Final = "LICENSES"
ENV_BLENDER: Final = "BLENDER"
ENV_OUTPUT: Final = "GITHUB_OUTPUT"
_TIMEOUT: Final = 120

Runner = Callable[[Sequence[str], Path], None]
Fetch = Callable[[str, Path], None]


class BundleError(RuntimeError):
    """An input of the bundle is missing or wrong."""


@dataclass(frozen=True)
class Pin:
    """A pinned Blender LTS Linux tarball."""

    version: str
    sha256: str

    @property
    def series(self) -> str:
        """`major.minor` of the version."""
        return ".".join(self.version.split(".")[:2])

    @property
    def stem(self) -> str:
        """The tarball's name without `.tar.xz`, also its top directory."""
        return f"blender-{self.version}-linux-x64"

    @property
    def url(self) -> str:
        """Where the tarball is downloaded from."""
        return f"https://download.blender.org/release/Blender{self.series}/{self.stem}.tar.xz"


BLENDER_PIN: Final = Pin(
    "4.2.23", "bea0eb3146be13eae6225409a117b215184f41b7f79e799f97cb3abb8f6dc404"
)


def _run(cmd: Sequence[str], cwd: Path) -> None:
    subprocess.run(list(cmd), cwd=cwd, check=True)


def _fetch(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out)


def resolve(reg: Registry, name: str) -> Module:
    """The module called `name` or living at path `name`."""
    for module in reg.modules:
        if name in (module.name, module.path):
            return module
    raise BundleError(f"no module {name!r}")


def tag(module: Module) -> str:
    """The release tag of the module's manifest version."""
    return f"{module.name}-v{module.version}"


def project_name(root: Path, module: Module) -> str:
    """The `[project] name` of a Python module."""
    pyproject = root / module.path / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        return str(data["project"]["name"])
    except (OSError, KeyError, tomllib.TOMLDecodeError) as err:
        raise BundleError(f"{pyproject}: no [project] name ({err})") from err


def wheel_command(root: Path, module: Module, out: Path) -> list[str]:
    """The `uv build` command for the module's wheel."""
    name = project_name(root, module)
    return ["uv", "build", "--package", name, "--wheel", "--out-dir", str(out)]


def _globbed(directory: Path, pattern: str, prefix: str) -> dict[str, Path]:
    found = {f"{prefix}/{p.name}": p for p in sorted(directory.glob(pattern)) if p.is_file()}
    if not found:
        raise BundleError(f"no {pattern} in {directory}")
    return found


def framework_files(root: Path, build: Path) -> dict[str, Path]:
    """Archive path -> source file of the framework bundle, minus the generated files."""
    files: dict[str, Path] = {}
    for prx in PRXS:
        if not (build / prx).is_file():
            raise BundleError(f"{build / prx} is missing: run `make -C framework`")
        files[f"{PLUGIN_DIR}/{prx}"] = build / prx
    lua = root / FRAMEWORK / "lua"
    lib = {
        f"{PLUGIN_DIR}/mods/lib/{p.name}": p
        for p in sorted((lua / "lib").glob("*.lua"))
        if not p.name.startswith("_")
    }
    if not lib:
        raise BundleError(f"no Lua libraries in {lua / 'lib'}")
    files.update(lib)
    files.update(_globbed(lua / "meta", "*.d.lua", "sdk/meta"))
    files.update(_globbed(lua / "examples", "*.lua", "sdk/examples"))
    for spdx in licences.licence_ids(licences.components(root)):
        text = root / LICENSES / f"{spdx}.txt"
        if not text.is_file():
            raise BundleError(f"{text} is missing: the notices name {spdx}")
        files[f"{LICENSES}/{spdx}.txt"] = text
    return files


def framework_zip(root: Path, build: Path, module: Module, out: Path) -> Path:
    """Write `mhfu_framework-<version>.zip` from the PRX build and the Lua sources."""
    files = framework_files(root, build)
    try:
        addresses = luals.addresses_path(root)
    except (luals.LualsMissing, OSError) as err:
        raise BundleError(str(err)) from err
    if not addresses.is_file():
        raise BundleError(f"{addresses} is missing: run `make -C framework`")
    files["sdk/meta/addresses.gen.lua"] = addresses
    with tempfile.TemporaryDirectory() as tmp:
        notice = Path(tmp) / NOTICE_FILE
        notice.write_text(licences.render(licences.components(root)), encoding="utf-8")
        files[NOTICE_FILE] = notice
        archive = out / f"mhfu_framework-{module.version}.zip"
        write_zip(archive, files)
    return archive


def blender_cache() -> Path:
    """Where pinned Blender releases are unpacked."""
    return Path.home() / ".cache" / "modkit-ci" / "blender"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def blender_binary(
    pin: Pin = BLENDER_PIN, cache: Path | None = None, fetch: Fetch = _fetch
) -> Path:
    """$BLENDER, else the sha256-checked pinned tarball (Linux x64) unpacked in the cache."""
    env = os.environ.get(ENV_BLENDER)
    if env:
        return Path(env)
    cache = cache or blender_cache()
    binary = cache / pin.version / pin.stem / "blender"
    if binary.is_file():
        return binary
    if not (sys.platform == "linux" and os.uname().machine in ("x86_64", "AMD64")):
        raise BundleError(f"the Blender pin is linux-x64 only: set ${ENV_BLENDER} elsewhere")
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache, prefix=".blender-") as tmp:
        tarball = Path(tmp) / f"{pin.stem}.tar.xz"
        try:
            fetch(pin.url, tarball)
        except (urllib.error.URLError, OSError) as err:
            raise BundleError(f"download of {pin.url} failed: {err}") from err
        got = _sha256(tarball)
        if got != pin.sha256:
            raise BundleError(f"Blender {pin.version}: sha256 {got}, pinned {pin.sha256}")
        unpacked = Path(tmp) / "unpacked"
        with tarfile.open(tarball, mode="r:xz") as tar:
            tar.extractall(unpacked, filter="data")
        dest = cache / pin.version
        if dest.exists():
            shutil.rmtree(dest)
        unpacked.rename(dest)
    if not binary.is_file():
        raise BundleError(f"{binary} is missing from the Blender tarball")
    return binary


def blender_zip(
    root: Path, module: Module, out: Path, run: Runner = _run, binary: Path | None = None
) -> Path:
    """Build the extension zip with `apps/blender/build.py` and copy it to `out`."""
    binary = binary or blender_binary()
    run(
        [sys.executable, str(root / module.path / "build.py"), "build", "--blender", str(binary)],
        root,
    )
    built = sorted((root / module.path / "dist").glob(f"*-{module.version}.zip"))
    if not built:
        raise BundleError(f"build.py left no *-{module.version}.zip in {module.path}/dist")
    out.mkdir(parents=True, exist_ok=True)
    return Path(shutil.copy2(built[-1], out))


def docs_name(module: Module) -> str:
    """The name of the module's documentation zip."""
    return f"modkit-docs-{tag(module)}.zip"


def docs_zip(root: Path, module: Module, out: Path) -> Path:
    """Build the documentation site and write `modkit-docs-<tag>.zip`."""
    archive = out / docs_name(module)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        if docs.build(root, Path(tmp), archive) != 0:
            raise BundleError("docs build failed")
    if not archive.is_file():
        raise BundleError("docs build wrote no zip")
    return archive


def _own_asset(module: Module) -> Callable[[str], bool] | None:
    """Tells whether a name is the module's own asset; None when it has no own asset."""
    if module.name == FRAMEWORK:
        return f"mhfu_framework-{module.version}.zip".__eq__
    if module.name == BLENDER:
        return lambda n: n.endswith(f"-{module.version}.zip") and n != docs_name(module)
    if module.release_type == "python":
        return lambda n: n.endswith(".whl")
    return None


def complete(module: Module, names: Collection[str]) -> bool:
    """Whether a release holding the assets `names` has everything `bundle` makes for it."""
    own = _own_asset(module)
    return docs_name(module) in names and (own is None or any(own(n) for n in names))


def needs(module: Module) -> dict[str, bool]:
    """Build steps `assets` expects first: `make` (the PRXs), `gl` (Blender libraries)."""
    return {"make": module.name == FRAMEWORK, "gl": module.name == BLENDER}


def assets(
    root: Path,
    module: Module,
    out: Path,
    *,
    build: Path | None = None,
    run: Runner = _run,
    binary: Path | None = None,
) -> list[Path]:
    """The module's own release assets, written under `out`."""
    if module.name == FRAMEWORK:
        return [framework_zip(root, build or root / FRAMEWORK / "build", module, out)]
    if module.name == BLENDER:
        return [blender_zip(root, module, out, run, binary)]
    if module.release_type == "python":
        out.mkdir(parents=True, exist_ok=True)
        run(wheel_command(root, module, out), root)
        return sorted(out.glob("*.whl"))
    return []


def write_output(name: str, value: str) -> None:
    """Append `name=value` to $GITHUB_OUTPUT, or print it outside Actions."""
    target = os.environ.get(ENV_OUTPUT)
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def _cmd(args: argparse.Namespace) -> int:
    root = registry.find_root()
    try:
        module = resolve(registry.load(root), args.module)
        out = Path(args.out).resolve()
        made = [docs_zip(root, module, out)] if args.docs else assets(root, module, out)
    except (BundleError, registry.RegistryError, subprocess.CalledProcessError) as err:
        print(f"bundle: {err}", file=sys.stderr)
        return 1
    for path in made:
        print(path)
    return 0


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `bundle` command."""
    p = sub.add_parser("bundle", help="assemble a module's release assets")
    p.add_argument("module", help="module name or path")
    p.add_argument("--out", required=True)
    p.add_argument("--docs", action="store_true", help="the docs zip instead of the module's own")
    p.set_defaults(func=_cmd)
