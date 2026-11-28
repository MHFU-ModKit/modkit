# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The documentation site: staged Markdown, generated Lua and Python reference, Zensical."""

from __future__ import annotations

import argparse
import importlib.util
import json
import posixpath
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from modkit_ci import api_lua, api_python, registry
from modkit_ci.lua_md import anchor, render
from modkit_ci.registry import Module, Registry

DECLARATION: Final = "mhfu.d.lua"
SHIM_SOURCE: Final = "docs/vendor/iframe-worker-1.0.4.js"
SHIM_PAGE: Final = "assets/iframe-worker.js"
LUA_API_SCRIPT: Final = "framework/tools/lua_api.py"
_LINK: Final = re.compile(r"\]\(([^)\s]+)")
_TITLE: Final = re.compile(r"^# +(.+?) *$", re.MULTILINE)
_SCHEME: Final = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//|#|/)")
_ANSI: Final = re.compile(r"\x1b\[[0-9;]*m")
_WHERE: Final = re.compile(r"\[ (\S+) ")


class DocsError(RuntimeError):
    """The documentation build cannot succeed."""


@dataclass
class Site:
    """What staging produced: the nav and everything the config needs."""

    nav: list[Any] = field(default_factory=list)
    lua_pages: dict[str, str] = field(default_factory=dict)  # docs-relative path -> text
    src_paths: list[str] = field(default_factory=list)
    totals: dict[str, tuple[int, int]] = field(default_factory=dict)  # module -> (public, bare)


def _title(path: Path, fallback: str) -> str:
    match = _TITLE.search(path.read_text(encoding="utf-8"))
    return match[1] if match else fallback


def _copy(root: Path, docs: Path, rel: str, as_rel: str | None = None) -> str | None:
    """Mirror `rel` into the docs tree (at `as_rel`); None when the source is missing."""
    src = root / rel
    if not src.is_file():
        return None
    dest = docs / (as_rel or rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix == ".md":
        dest.write_text(_retarget(src.read_text(encoding="utf-8"), as_rel or rel), encoding="utf-8")
    else:
        shutil.copyfile(src, dest)
    return as_rel or rel


def _guides(root: Path, docs: Path, reg: Registry) -> list[Any]:
    """Each module's README, then those of its extra dirs, in registry order."""
    out: list[Any] = []
    for module in reg.modules:
        for path in module.paths:
            rel = _copy(root, docs, f"{path}/README.md")
            if rel:
                out.append({module.name if path == module.path else path: rel})
    return out


def _formats(root: Path, docs: Path) -> list[Any]:
    out: list[Any] = []
    folder = root / "docs" / "formats"
    for src in sorted(folder.glob("*.md"), key=lambda p: (p.name != "README.md", p.name)):
        rel = _copy(root, docs, f"docs/formats/{src.name}")
        if rel:
            out.append({_title(src, src.stem): rel})
    return out


def _retarget(text: str, rel: str) -> str:
    """Point links at the root README to the staged index page."""

    def fix(match: re.Match[str]) -> str:
        target = match[1]
        file, hash_, frag = target.partition("#")
        if (
            _SCHEME.match(target)
            or posixpath.normpath(posixpath.join(posixpath.dirname(rel), file)) != "README.md"
        ):
            return match[0]
        return f"]({posixpath.relpath('index.md', posixpath.dirname(rel) or '.')}{hash_}{frag}"

    return _LINK.sub(fix, text)


def _lua_pages(root: Path, module: Module) -> str:
    return render(api_lua.load(root, module.lua), f"Lua reference: {module.name}")


def _python_pages(root: Path, module: Module, docs: Path, site: Site) -> list[Any]:
    """One page per public Python module; the totals are counted on the way."""
    pages: list[Any] = []
    public = bare = 0
    for path, obj, documented in api_python.public_objects(root, module):
        public += 1
        bare += not documented
        if obj.is_module:
            rel = f"reference/python/{module.name}/{path}.md"
            page = docs / rel
            page.parent.mkdir(parents=True, exist_ok=True)
            body = f"# {path}\n\n::: {path}\n    options:\n      show_root_heading: false\n"
            page.write_text(body, encoding="utf-8")
            pages.append({path: rel})
    if pages:
        site.src_paths.append(str(root / module.path / "src"))
        site.totals[module.name] = (public, bare)
    return [{module.name: pages}] if pages else []


def stage(root: Path, reg: Registry, docs: Path) -> Site:
    """Write the Markdown tree under `docs` and return the nav and what was found."""
    site = Site()
    home = _copy(root, docs, "README.md", "index.md")
    _copy(root, docs, "misc/banner.svg")
    _copy(root, docs, SHIM_SOURCE, SHIM_PAGE)
    contributing = _copy(root, docs, "CONTRIBUTING.md")
    pages: list[Any] = [{"Overview": home or "index.md"}]
    if contributing:
        pages.append({"Contributing": contributing})
    site.nav.append({"Home": pages})
    site.nav.append({"Guides": _guides(root, docs, reg)})
    site.nav.append({"Formats": _formats(root, docs)})
    lua: list[Any] = []
    python: list[Any] = []
    for module in reg.modules:
        if "lua" in module.api and module.lua:
            rel = f"reference/lua/{module.name}.md"
            site.lua_pages[rel] = _lua_pages(root, module)
            (docs / rel).parent.mkdir(parents=True, exist_ok=True)
            (docs / rel).write_text(site.lua_pages[rel], encoding="utf-8")
            lua.append({module.name: rel})
        if "python" in module.api:
            python += _python_pages(root, module, docs, site)
    site.nav.append({"Reference": [{"Lua": lua}, {"Python": python}]})
    return site


def config(site: Site, docs_dir: str, site_dir: str) -> dict[str, Any]:
    """The generated mkdocs.yml as data; Zensical and MkDocs 1.6 read the same keys."""
    return {
        "site_name": "modkit",
        "docs_dir": docs_dir,
        "site_dir": site_dir,
        "use_directory_urls": False,
        "theme": {"name": "material", "font": False},
        "nav": site.nav,
        # the offline plugin skips its own (unpinned, unpkg) shim when one is named
        "extra": {"polyfills": [SHIM_PAGE]},
        "markdown_extensions": ["attr_list", "tables", "toc", "pymdownx.highlight"],
        "plugins": [
            "search",
            "offline",
            {
                "mkdocstrings": {
                    "handlers": {
                        "python": {
                            "paths": site.src_paths,
                            "options": {
                                "show_if_no_docstring": True,
                                "allow_inspection": False,
                            },
                        }
                    }
                }
            },
        ],
    }


def _declared(root: Path, module: Module) -> list[str]:
    """Function names `mhfu.d.lua` registers, via the framework's own parser."""
    for folder in module.lua:
        decl = root / folder / DECLARATION
        if not decl.is_file():
            continue
        script = root / LUA_API_SCRIPT
        # a script, not a package: loaded by path
        spec = importlib.util.spec_from_file_location("lua_api", script)
        if spec is None or spec.loader is None:
            raise DocsError(f"cannot load {LUA_API_SCRIPT}")
        lua_api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lua_api)
        names: list[str] = list(lua_api.parse(decl.read_text(encoding="utf-8")).funcs)
        return names
    raise DocsError(f"{module.name}: no {DECLARATION} in {', '.join(module.lua)}")


def missing_lua(root: Path, reg: Registry, site: Site) -> list[str]:
    """Declared Lua functions that no rendered page documents."""
    text = "\n".join(site.lua_pages.values())
    out: list[str] = []
    for module in reg.modules:
        if "lua" not in module.api:
            continue
        for name in _declared(root, module):
            if f"{{ #{anchor('mhfu.' + name)} }}" not in text:
                out.append(f"mhfu.{name}")
    return out


def zip_site(site_dir: Path, target: Path) -> None:
    """Zip the built site, paths relative to its root."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(site_dir.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(site_dir).as_posix())


def warnings_of(output: str) -> list[str]:
    """Zensical's `Warning:` blocks as `file:line:col: message`."""
    lines = _ANSI.sub("", output).splitlines()
    out: list[str] = []
    for n, line in enumerate(lines):
        if line.startswith("Warning:"):
            where = next((m[1] for x in lines[n + 1 : n + 3] if (m := _WHERE.search(x))), "")
            out.append(f"{where}: {line[8:].strip()}" if where else line[8:].strip())
    return out


def _zensical(cfg: Path, cwd: Path) -> list[str]:
    """Run `zensical build --strict`; the warnings it printed. Raises DocsError on failure."""
    if importlib.util.find_spec("zensical") is None:
        raise DocsError("zensical is not installed: uv sync --group docs")
    proc = subprocess.run(
        [sys.executable, "-m", "zensical", "build", "-f", str(cfg), "--clean", "--strict"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    found = warnings_of(proc.stdout + proc.stderr)
    for line in found:
        print(f"warning: {line}")
    if proc.returncode:
        tail = _ANSI.sub("", proc.stdout + proc.stderr).strip().splitlines()[-5:]
        raise DocsError(f"zensical build failed ({proc.returncode}): {' | '.join(tail)}")
    return found


def build(root: Path, out: Path, zip_path: Path | None = None) -> int:
    """Build the site under `out`; 1 on a build error or an undocumented Lua function."""
    reg = registry.load(root)
    docs = out / "stage" / "docs"
    site_dir = out / "site"
    for folder in (docs.parent, site_dir):
        shutil.rmtree(folder, ignore_errors=True)
    docs.mkdir(parents=True)
    site = stage(root, reg, docs)
    for name, (public, bare) in site.totals.items():
        print(f"undocumented public objects: {name}: {bare} of {public}")
    try:
        missing = missing_lua(root, reg, site)
    except DocsError as err:
        print(f"error: {err}")
        return 1
    if missing:
        print(f"error: Lua functions missing from the reference: {', '.join(missing)}")
        return 1
    cfg = out / "mkdocs.yml"
    cfg.write_text(
        json.dumps(config(site, "stage/docs", "site"), indent=2) + "\n", encoding="utf-8"
    )
    try:
        _zensical(cfg, out)
    except DocsError as err:
        print(f"error: {err}")
        return 1
    if not (site_dir / "index.html").is_file():
        print("error: the build wrote no index.html")
        return 1
    if zip_path:
        zip_site(site_dir, zip_path)
    return 0


def _cmd(args: argparse.Namespace) -> int:
    root = registry.find_root()
    zip_path = Path(args.zip).resolve() if args.zip else None
    return build(root, Path(args.out).resolve(), zip_path)


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `docs` command."""
    p = sub.add_parser("docs", help="build the documentation site")
    p.add_argument("action", choices=["build"])
    p.add_argument("--out", default="build/docs")
    p.add_argument("--zip")
    p.set_defaults(func=_cmd)
