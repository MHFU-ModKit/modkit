# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
from __future__ import annotations

from pathlib import Path

import pytest
from modkit_ci import api_schema, registry
from modkit_ci.registry import Module, Schema

LOADER = """
def load(path) -> None:
    text = path.read_text(encoding="utf-8")
    if text.startswith("bad"):
        raise ValueError("bad document")
    return text


def dump(doc) -> None:
    return doc.strip() + "\\n"
"""


@pytest.fixture
def loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "fx_loader.py").write_text(LOADER, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(__import__("sys").modules, "fx_loader", raising=False)


def module(load: str = "fx_loader:load", dump: str | None = "fx_loader:dump") -> Module:
    schema = Schema("doc", load, dump, ("corpus/*.txt",))
    return Module("m", "m", "python", "0.1.0", ("m",), (), (), (), (schema,), False)


def corpus(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (root / "corpus").mkdir(parents=True, exist_ok=True)
        (root / "corpus" / name).write_text(text, encoding="utf-8")
    return root


def test_clean(loader: None, tmp_path: Path) -> None:
    base = corpus(tmp_path / "b", {"a.txt": "ok\n"})
    assert api_schema.check(module(), base, tmp_path / "h") == []


def test_load_fails(loader: None, tmp_path: Path) -> None:
    base = corpus(tmp_path / "b", {"a.txt": "ok\n", "b.txt": "bad\n"})
    [f] = api_schema.check(module(), base, tmp_path / "h")
    assert (f.kind, f.path) == ("break", "corpus/b.txt")
    assert "bad document" in f.message


def test_dump_differs(loader: None, tmp_path: Path) -> None:
    base = corpus(tmp_path / "b", {"a.txt": "ok  \n"})
    [f] = api_schema.check(module(), base, tmp_path / "h")
    assert (f.kind, f.path) == ("break", "corpus/a.txt")
    assert "round-trip" in f.message


def test_no_dump_skips_round_trip(loader: None, tmp_path: Path) -> None:
    base = corpus(tmp_path / "b", {"a.txt": "ok  \n"})
    assert api_schema.check(module(dump=None), base, tmp_path / "h") == []


def test_head_only_file_ignored(loader: None, tmp_path: Path) -> None:
    base = corpus(tmp_path / "b", {"a.txt": "ok\n"})
    head = corpus(tmp_path / "h", {"a.txt": "ok\n", "new.txt": "bad\n"})
    assert api_schema.check(module(), base, head) == []


def test_loader_gone(loader: None, tmp_path: Path) -> None:
    base = corpus(tmp_path / "b", {"a.txt": "ok\n"})
    [f] = api_schema.check(module(load="fx_loader:missing"), base, tmp_path / "h")
    assert f.kind == "break" and "loader is gone" in f.message


def test_repo_corpus_round_trips() -> None:
    reg = registry.load()
    checked = 0
    for m in reg.modules:
        for schema in m.schemas:
            files = api_schema._corpus(schema, reg.root)
            assert files, f"{m.name} {schema.name}: empty corpus"
            checked += len(files)
        assert api_schema.check(m, reg.root, reg.root) == []
    assert checked
