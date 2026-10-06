# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhfu import inject
from mhfu_studio.shell import places, settings
from mhfu_studio.shell.places import MEMSTICK, MHFU, MHP3RD, Missing
from ppsspp_debug import Lane


def extraction(at: Path) -> Path:
    (at / "data_files").mkdir(parents=True)
    return at


@pytest.fixture(autouse=True)
def _none(monkeypatch: pytest.MonkeyPatch) -> None:
    for p in places.PLACES:
        monkeypatch.delenv(p.env, raising=False)
    monkeypatch.delenv(inject.LANE_ENV, raising=False)
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", ())


def test_env_beats_saved_beats_guess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env, saved = extraction(tmp_path / "env"), extraction(tmp_path / "saved")
    guess = extraction(tmp_path / "up" / "workspace" / "extracted")
    monkeypatch.setattr(places, "GUESS_FROM", tmp_path / "up" / "a" / "b" / "c" / "d" / "e")
    assert places.find(MHFU).path is None  # too far up
    monkeypatch.setattr(places, "GUESS_FROM", tmp_path / "up" / "a" / "b")
    got = places.find(MHFU)
    assert (got.path, got.source) == (guess, "found") and "(found)" in got.says()
    places.remember(MHFU, saved / "data_files")  # its data_files means the extraction
    assert settings.store.get("places/mhfu") == str(saved)
    assert (places.find(MHFU).path, places.find(MHFU).source) == (saved, "saved")
    monkeypatch.setenv("MHFU_DATA", str(env))
    got = places.find(MHFU)
    assert (got.path, got.source) == (env, "env") and "from MHFU_DATA" in got.says()
    assert places.extracted(MHFU).root == env and places.extracted(given=saved).root == saved


def test_bad_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    saved = extraction(tmp_path / "saved")
    places.remember(MHP3RD, saved)
    monkeypatch.setenv("MHP3RD_DATA", str(tmp_path / "nope"))
    got = places.find(MHP3RD)  # a bad variable is not passed over for the setting
    assert got.path is None and got.source == "env" and "has no data_files" in got.says()
    with pytest.raises(FileNotFoundError):
        places.remember(MHFU, tmp_path / "nope")
    assert settings.store.get("places/mhfu") is None


def test_missing_words(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(Missing) as e:
        places.games()
    assert "set MHFU_DATA" in str(e.value) and isinstance(e.value, FileNotFoundError)
    assert "MHFU_DATA" not in e.value.words and "start page" in e.value.words
    assert places.find(MHFU).says() == "No MHFU extraction found"


def test_memstick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stick = tmp_path / "PPSSPP"
    (stick / "PSP" / inject.MODS_SUBDIR).mkdir(parents=True)
    with pytest.raises(Missing):
        places.mods_dir()
    places.remember(MEMSTICK, stick)
    assert places.mods_dir() == stick / "PSP" / inject.MODS_SUBDIR
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(tmp_path / "found"),))
    (tmp_path / "found").mkdir()
    places.remember(MEMSTICK, None)
    assert places.find(MEMSTICK).source == "found"
    assert places.environ() == {inject.MEMSTICK_ENV: str(tmp_path / "found")}


def test_memstick_lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lane = tmp_path / "2/.config/ppsspp/PSP"
    (lane / inject.MODS_SUBDIR).mkdir(parents=True)
    monkeypatch.setattr(inject, "Lane", lambda n: Lane(n, root=tmp_path))
    places.remember(MEMSTICK, tmp_path)
    monkeypatch.setenv(inject.MEMSTICK_ENV, str(tmp_path))
    monkeypatch.setenv(inject.LANE_ENV, "2")
    got = places.find(MEMSTICK)
    assert (got.path, got.source) == (lane, "lane") and "from MHFU_LANE" in got.says()
    assert places.mods_dir() == lane / inject.MODS_SUBDIR


def test_the_wrong_game(tmp_path: Path) -> None:
    p3rd = extraction(tmp_path / "p3rd")
    (p3rd / "UMD_DATA.BIN").write_bytes(b"ULJM-05800|0|0001|G")
    with pytest.raises(ValueError, match="ULJM-05800, not MHFU"):
        places.remember(MHFU, p3rd)
    assert places.remember(MHP3RD, p3rd).path == p3rd
