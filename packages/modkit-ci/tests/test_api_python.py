# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
from __future__ import annotations

from pathlib import Path

from modkit_ci import api_python
from modkit_ci.findings import Finding
from modkit_ci.registry import Module

MODULE = Module("m", "pkg", "python", "0.1.0", ("pkg",), (), ("python",), (), (), False)


def tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        target = root / "pkg" / "src" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def run(tmp_path: Path, base: dict[str, str], head: dict[str, str]) -> list[Finding]:
    base_root = tree(tmp_path / "base", base)
    head_root = tree(tmp_path / "head", head)
    return api_python.check(MODULE, base_root, head_root)


def kinds(found: list[Finding]) -> list[tuple[str, str]]:
    return sorted((f.kind, f.message.split(":")[0]) for f in found)


def pkg(body: str) -> dict[str, str]:
    return {"p/__init__.py": body}


def test_same(tmp_path: Path) -> None:
    src = pkg('def f(a):\n    """Doc."""\n')
    assert run(tmp_path, src, src) == []


def test_removed_function(tmp_path: Path) -> None:
    found = run(tmp_path, pkg('def f():\n    """D."""\n'), pkg(""))
    assert kinds(found) == [("break", "p.f")]
    assert found[0].path is None


def test_changed_signature(tmp_path: Path) -> None:
    old = pkg('def f(a):\n    """D."""\n')
    new = pkg('def f(a, b):\n    """D."""\n')
    assert kinds(run(tmp_path, old, new)) == [("break", "p.f")]


def test_optional_param_added(tmp_path: Path) -> None:
    old = pkg('def f(a):\n    """D."""\n')
    new = pkg('def f(a, b=1):\n    """D."""\n')
    assert run(tmp_path, old, new) == []


def test_new_undocumented(tmp_path: Path) -> None:
    found = run(tmp_path, pkg(""), pkg("def g():\n    pass\n"))
    assert kinds(found) == [("doc", "p.g")]
    assert found[0].path == "pkg/src/p/__init__.py"
    assert found[0].line == 1


def test_new_documented(tmp_path: Path) -> None:
    assert run(tmp_path, pkg(""), pkg('def g():\n    """D."""\n')) == []


def test_docstring_removed(tmp_path: Path) -> None:
    found = run(tmp_path, pkg('def f():\n    """D."""\n'), pkg("def f():\n    pass\n"))
    assert kinds(found) == [("doc", "p.f")]
    assert "removed" in found[0].message


def test_old_undocumented_stays(tmp_path: Path) -> None:
    src = pkg("def f():\n    pass\n")
    assert run(tmp_path, src, src) == []


def test_private_ignored(tmp_path: Path) -> None:
    new = pkg("class _C:\n    def m(self):\n        pass\n\ndef _g():\n    pass\n")
    assert run(tmp_path, pkg(""), new) == []


def test_dunder_ignored(tmp_path: Path) -> None:
    new = pkg('class C:\n    """D."""\n\n    def __repr__(self):\n        return ""\n')
    assert run(tmp_path, pkg(""), new) == []


def test_all_hides(tmp_path: Path) -> None:
    new = pkg('__all__ = ["f"]\n\ndef f():\n    """D."""\n\ndef g():\n    pass\n')
    assert run(tmp_path, pkg(""), new) == []


def test_all_exposes(tmp_path: Path) -> None:
    new = pkg('__all__ = ["_g"]\n\ndef _g():\n    pass\n')
    assert kinds(run(tmp_path, pkg(""), new)) == [("doc", "p._g")]


def test_method_needs_doc(tmp_path: Path) -> None:
    new = pkg('class C:\n    """D."""\n\n    def m(self):\n        pass\n')
    old = pkg('class C:\n    """D."""\n')
    assert kinds(run(tmp_path, old, new)) == [("doc", "p.C.m")]


def test_attributes_need_no_doc(tmp_path: Path) -> None:
    new = pkg(
        "from dataclasses import dataclass\n\nLIMIT = 3\n\n@dataclass\nclass P:\n"
        '    """D."""\n\n    x: int\n    y: int = 0\n\n    z = 1\n'
    )
    assert run(tmp_path, pkg(""), new) == []


def test_attribute_removal_breaks(tmp_path: Path) -> None:
    found = run(tmp_path, pkg("LIMIT = 3\n"), pkg(""))
    assert kinds(found) == [("break", "p.LIMIT")]


def test_class_attribute_removal_breaks(tmp_path: Path) -> None:
    old = pkg('class C:\n    """D."""\n\n    x: int = 1\n')
    new = pkg('class C:\n    """D."""\n')
    assert kinds(run(tmp_path, old, new)) == [("break", "p.C.x")]


def test_new_package(tmp_path: Path) -> None:
    head = {**pkg(""), "q/__init__.py": '"""Q."""\n', "q/mod.py": "def f():\n    pass\n"}
    assert kinds(run(tmp_path, pkg(""), head)) == [("doc", "q.mod"), ("doc", "q.mod.f")]


def test_package_removed(tmp_path: Path) -> None:
    found = run(tmp_path, {**pkg(""), "q/__init__.py": ""}, pkg(""))
    assert [(f.kind, "q" in f.message) for f in found] == [("break", True)]


def test_alias_not_public(tmp_path: Path) -> None:
    base = {**pkg('"""P."""\n'), "p/m.py": '"""M."""\n\ndef f():\n    """D."""\n'}
    head = {**base, "p/__init__.py": '"""P."""\n\nfrom p.m import f\n'}
    assert run(tmp_path, base, head) == []


def test_public_objects(tmp_path: Path) -> None:
    body = (
        '"""P."""\n\nK = 1\n\ndef f():\n    """D."""\n\ndef g():\n    pass\n\ndef _h():\n    pass\n'
    )
    root = tree(tmp_path / "r", pkg(body))
    got = {p: d for p, _, d in api_python.public_objects(root, MODULE)}
    assert got == {"p": True, "p.f": True, "p.g": False}
