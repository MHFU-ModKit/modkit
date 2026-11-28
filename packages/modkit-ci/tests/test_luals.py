# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""LuaLS binary lookup, the verified download and the fixture doc."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import urllib.error
from pathlib import Path

import pytest
from modkit_ci import cli, luals

FIXTURE = Path(__file__).parent / "fixtures" / "lua"


def _archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        body = b"#!/bin/sh\n"
        info = tarfile.TarInfo("bin/lua-language-server")
        info.size, info.mode = len(body), 0o755
        tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(luals.ENV_BIN, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(luals, "platform_key", lambda: "linux-x64")


def _no_net(url: str) -> bytes:
    raise AssertionError(f"network use: {url}")


def _offline(url: str) -> bytes:
    raise urllib.error.URLError("offline")


def test_env_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exe = tmp_path / "mine"
    exe.write_text("x")
    cached = tmp_path / "cache" / "bin" / luals.EXE
    cached.parent.mkdir(parents=True)
    cached.write_text("y")
    monkeypatch.setenv(luals.ENV_BIN, str(exe))
    assert luals.binary(_no_net, tmp_path / "cache") == exe


def test_env_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(luals.ENV_BIN, str(tmp_path / "nope"))
    with pytest.raises(luals.LualsMissing):
        luals.binary(_no_net, tmp_path / "cache")


def test_cache_beats_download_and_path(tmp_path: Path) -> None:
    cached = tmp_path / "cache" / "bin" / luals.EXE
    cached.parent.mkdir(parents=True)
    cached.write_text("y")
    assert luals.binary(_no_net, tmp_path / "cache") == cached


def test_download_verified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = _archive()
    monkeypatch.setitem(luals.SHA256, "linux-x64", hashlib.sha256(data).hexdigest())
    urls: list[str] = []

    def opener(url: str) -> bytes:
        urls.append(url)
        return data

    exe = luals.binary(opener, tmp_path / "cache")
    assert exe == tmp_path / "cache" / "bin" / luals.EXE and exe.stat().st_mode & 0o100
    assert urls == [
        f"https://github.com/LuaLS/lua-language-server/releases/download/{luals.VERSION}/"
        f"lua-language-server-{luals.VERSION}-linux-x64.tar.gz"
    ]
    assert luals.binary(_no_net, tmp_path / "cache") == exe  # second call is cached


def test_sha_mismatch(tmp_path: Path) -> None:
    with pytest.raises(luals.LualsHashError):
        luals.binary(lambda url: _archive(), tmp_path / "cache")
    assert not (tmp_path / "cache").exists()


def _path_binary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, version: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / luals.EXE
    exe.write_text(f"#!/bin/sh\necho {version}\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    return exe


def test_offline_uses_path_of_same_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exe = _path_binary(monkeypatch, tmp_path, luals.VERSION)
    assert luals.binary(_offline, tmp_path / "cache") == exe


def test_offline_refuses_path_of_other_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _path_binary(monkeypatch, tmp_path, "3.0.0")
    with pytest.raises(luals.LualsMissing, match=r"version 3\.0\.0, not " + luals.VERSION):
        luals.binary(_offline, tmp_path / "cache")


def test_http_error_does_not_fall_to_other_path_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _path_binary(monkeypatch, tmp_path, "9.9.9")

    def gone(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]

    with pytest.raises(luals.LualsMissing, match="download"):
        luals.binary(gone, tmp_path / "cache")


def test_unknown_platform_refuses_path_of_other_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(luals, "platform_key", lambda: None)
    _path_binary(monkeypatch, tmp_path, "1.0.0")
    with pytest.raises(luals.LualsMissing):
        luals.binary(_no_net, tmp_path / "cache")


def test_nothing_found(tmp_path: Path) -> None:
    with pytest.raises(luals.LualsMissing):
        luals.binary(_offline, tmp_path / "cache")


def test_unknown_platform_skips_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(luals, "platform_key", lambda: None)
    with pytest.raises(luals.LualsMissing):
        luals.binary(_no_net, tmp_path / "cache")


def test_pins_cover_both_hosts() -> None:
    assert set(luals.SHA256) == {"linux-x64", "darwin-arm64"}
    assert all(len(h) == 64 for h in luals.SHA256.values())


def test_cache_dir_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["luals", "cache-dir"]) == 0
    assert capsys.readouterr().out.strip() == str(luals.cache_dir())
    assert luals.cache_dir().name == luals.VERSION


def test_fixture_doc_is_current(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.undo()  # the real PATH and cache
    try:
        luals.binary(_offline)
    except luals.LualsMissing:
        pytest.skip("LuaLS is not installed")
    got = luals.doc(FIXTURE, tmp_path / "doc.json")
    for entry in got:
        if entry["type"] == "luals.config":
            entry["DOC"] = ""
    assert got == json.loads((FIXTURE / "doc.json").read_text(encoding="utf-8"))
