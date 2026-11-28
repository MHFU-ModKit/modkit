# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The docs build: staging, nav, config, the Lua coverage check and the zip."""

from __future__ import annotations

import importlib.util
import json
import re
import zipfile
from pathlib import Path

import pytest
from modkit_ci import api_lua, docs, luals, registry
from modkit_ci.api_lua import Func, LuaApi

EXTERNAL = re.compile(r"""<(?:script|link)\b[^>]*\b(?:src|href)=["'](?:https?:)?//[^"']+""")
ALLOWED_EXTERNAL: frozenset[str] = frozenset()  # an entry needs a reason on its line
REPO = Path(__file__).resolve().parents[3]
DECL = "---Doc.\n---@return integer\nfunction mhfu.foo() end\n---Doc.\nmhfu.api_version = 1\n"


def _func(name: str) -> Func:
    return Func(name, (), (), "d", (), "framework/lua/meta/mhfu.d.lua", 1)


@pytest.fixture
def tree(registry_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The registry tree with READMEs, formats, a Python package and a declared Lua function."""
    root = registry_root

    def write(rel: str, text: str) -> None:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")

    write("README.md", "# modkit\n\n[alpha](packages/alpha/README.md) [c](CONTRIBUTING.md)\n")
    write("CONTRIBUTING.md", "# Contributing\n")
    write("misc/banner.svg", "<svg/>")
    write("packages/alpha/README.md", "# Alpha\n\n[home](../../README.md#top) [x](https://e.org)\n")
    write("data/README.md", "# Data\n")
    write("apps/beta/README.md", "# Beta\n")
    write("docs/formats/README.md", "# Formats\n")
    write("docs/formats/zeta.md", "# Zeta format\n")
    write("docs/formats/alpha.md", "# Alpha format\n")
    write("packages/alpha/src/alpha/__init__.py", '"""Alpha."""\n')
    write("packages/alpha/src/alpha/core.py", '"""Core."""\n\n\ndef f() -> None:\n    pass\n')
    write("framework/lua/meta/mhfu.d.lua", DECL)
    script = (REPO / docs.LUA_API_SCRIPT).read_text(encoding="utf-8")
    write(docs.LUA_API_SCRIPT, script)
    write(docs.SHIM_SOURCE, "//shim\n")
    monkeypatch.setattr(api_lua, "load", lambda *_: LuaApi((_func("mhfu.foo"),), (), ()))
    return root


def _stage(tree: Path, tmp_path: Path) -> tuple[docs.Site, Path]:
    out = tmp_path / "docs"
    out.mkdir()
    return docs.stage(tree, registry.load(tree), out), out


def _titles(entries: list[object]) -> list[str]:
    return [next(iter(e)) for e in entries if isinstance(e, dict)]


def test_staging_mirror(tree: Path, tmp_path: Path) -> None:
    _, out = _stage(tree, tmp_path)
    for rel in (
        "index.md",
        "CONTRIBUTING.md",
        "misc/banner.svg",
        "packages/alpha/README.md",
        "data/README.md",
        "docs/formats/zeta.md",
        "reference/lua/framework.md",
        docs.SHIM_PAGE,
    ):
        assert (out / rel).is_file(), rel
    assert not (out / "framework" / "README.md").exists()


def test_nav_order(tree: Path, tmp_path: Path) -> None:
    site, _ = _stage(tree, tmp_path)
    top = {k: v for e in site.nav for k, v in e.items()}
    assert list(top) == ["Home", "Guides", "Formats", "Reference"]
    assert _titles(top["Guides"]) == ["alpha", "data", "beta"]
    assert _titles(top["Formats"]) == ["Formats", "Alpha format", "Zeta format"]
    ref = {k: v for e in top["Reference"] for k, v in e.items()}
    assert _titles(ref["Lua"]) == ["framework"]
    assert _titles(ref["Python"]) == ["alpha"]


def test_links_to_root_readme_follow(tree: Path, tmp_path: Path) -> None:
    _, out = _stage(tree, tmp_path)
    text = (out / "packages/alpha/README.md").read_text(encoding="utf-8")
    assert "](../../index.md#top)" in text
    assert "](https://e.org)" in text


def test_python_pages_and_totals(tree: Path, tmp_path: Path) -> None:
    site, out = _stage(tree, tmp_path)
    page = out / "reference/python/alpha/alpha.core.md"
    assert "::: alpha.core" in page.read_text(encoding="utf-8")
    assert site.totals["alpha"] == (3, 1)  # alpha, alpha.core, alpha.core.f; f is bare
    assert site.src_paths == [str(tree / "packages/alpha/src")]


def test_config(tree: Path, tmp_path: Path) -> None:
    site, _ = _stage(tree, tmp_path)
    cfg = docs.config(site, "stage/docs", "site")
    assert cfg["nav"] == site.nav
    assert cfg["use_directory_urls"] is False
    assert cfg["extra"]["polyfills"] == [docs.SHIM_PAGE]
    names = [p if isinstance(p, str) else next(iter(p)) for p in cfg["plugins"]]
    assert names == ["search", "offline", "mkdocstrings"]
    handler = cfg["plugins"][2]["mkdocstrings"]["handlers"]["python"]
    assert handler["paths"] == site.src_paths
    assert handler["options"]["allow_inspection"] is False
    yaml = pytest.importorskip("yaml")
    assert yaml.safe_load(json.dumps(cfg)) == cfg


def test_declared_functions(tree: Path) -> None:
    reg = registry.load(tree)
    assert docs._declared(tree, reg.get("framework")) == ["foo"]


def test_missing_declaration_fails(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tree / "framework/lua/meta/mhfu.d.lua").unlink()
    with pytest.raises(docs.DocsError, match="no mhfu.d.lua"):
        docs._declared(tree, registry.load(tree).get("framework"))
    monkeypatch.setattr(docs, "_zensical", lambda *_: pytest.fail("built anyway"))
    assert docs.build(tree, tmp_path / "out") == 1
    assert "no mhfu.d.lua" in capsys.readouterr().out


def test_missing_lua_function(tree: Path, tmp_path: Path) -> None:
    reg = registry.load(tree)
    site, _ = _stage(tree, tmp_path)
    assert docs.missing_lua(tree, reg, site) == []
    site.lua_pages = {"x.md": "no functions"}
    assert docs.missing_lua(tree, reg, site) == ["mhfu.foo"]


def test_build_fails_on_missing_function(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(api_lua, "load", lambda *_: LuaApi((), (), ()))
    monkeypatch.setattr(docs, "_zensical", lambda *_: pytest.fail("built anyway"))
    assert docs.build(tree, tmp_path / "out") == 1
    assert "mhfu.foo" in capsys.readouterr().out


def _fake_zensical(cfg: Path, cwd: Path) -> list[str]:
    config = json.loads(cfg.read_text(encoding="utf-8"))
    site = cwd / config["site_dir"]
    (site / "sub").mkdir(parents=True)
    (site / "index.html").write_text("<html/>", encoding="utf-8")
    (site / "sub" / "a.html").write_text("a", encoding="utf-8")
    return []


def test_build_zips_the_site(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(docs, "_zensical", _fake_zensical)
    target = tmp_path / "out" / "docs.zip"
    assert docs.build(tree, tmp_path / "out", target) == 0
    assert zipfile.ZipFile(target).namelist() == ["index.html", "sub/a.html"]
    assert "undocumented public objects: alpha: 1 of 3" in capsys.readouterr().out


def test_build_fails_without_index(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docs, "_zensical", lambda *_: [])
    assert docs.build(tree, tmp_path / "out") == 1


def test_build_fails_when_zensical_fails(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: object) -> list[str]:
        raise docs.DocsError("failed")

    monkeypatch.setattr(docs, "_zensical", boom)
    assert docs.build(tree, tmp_path / "out") == 1


def test_warning_blocks() -> None:
    out = (
        "Build started\n\x1b[33mWarning:\x1b[0m page does not exist\n"
        "    \x1b[2m╭─[\x1b[0m a/README.md:11:10 \x1b[2m]\x1b[0m\n    | x\n"
        "6 issues found\n"
    )
    assert docs.warnings_of(out) == ["a/README.md:11:10: page does not exist"]


# integration: the whole repo ------------------------------------------------------------


def test_real_build(tmp_path: Path) -> None:
    if importlib.util.find_spec("zensical") is None:
        pytest.skip("zensical is not installed")
    try:
        luals.binary(lambda url: (_ for _ in ()).throw(OSError("offline")))
    except luals.LualsMissing:
        pytest.skip("LuaLS is not installed")
    target = tmp_path / "docs.zip"
    assert docs.build(REPO, tmp_path / "out", target) == 0
    with zipfile.ZipFile(target) as z:
        assert "index.html" in z.namelist()
        page = z.read("reference/lua/framework.html").decode("utf-8")
        for name in z.namelist():
            if name.endswith(".html"):
                found = set(EXTERNAL.findall(z.read(name).decode("utf-8")))
                assert found <= ALLOWED_EXTERNAL, (name, found)
        assert any("iframe-worker" in n for n in z.namelist())
    reg = registry.load(REPO)
    for name in docs._declared(REPO, reg.get("framework")):
        assert f'id="mhfu.{name}"' in page
