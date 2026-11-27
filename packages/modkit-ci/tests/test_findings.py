# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Annotations and the step summary."""

from __future__ import annotations

from pathlib import Path

import pytest
from modkit_ci.findings import Finding, annotate, summary

F = Finding("mhfu", "break", "removed foo", "src/a.py", 12)


def test_annotate_github(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    annotate([F, Finding("hud", "doc", "a\nb 50%")], "error")
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "::error file=src/a.py,line=12,title=mhfu break::removed foo",
        "::error title=hud doc::a%0Ab 50%25",
    ]


def test_annotate_file_without_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    annotate([Finding("m", "warning", "w", "x.lua")], "notice")
    assert capsys.readouterr().out == "::notice file=x.lua,title=m warning::w\n"


def test_annotate_local(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    annotate([F], "warning")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "warning: mhfu break: src/a.py:12: removed foo\n"


def test_summary_table(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
    summary("API", [F, Finding("hud", "doc", "a|b")])
    summary("Again", [])
    text = target.read_text()
    assert "### API" in text
    assert "| mhfu | break | src/a.py:12 | removed foo |" in text
    assert "| hud | doc |  | a\\|b |" in text
    assert "No findings." in text


def test_summary_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    summary("API", [F])
