# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import shutil

import pytest
from mhfu import inject
from mhfu_port import manifest
from mhfu_studio.cli import main

PORT = """
[port]
name = "t"
host_species = 75
pac = "t.bin"

[source]
model = 5248

[clips.idle]
slot = 1
frames = 10
loop = true

[moves.m]
main = 0
sub = 1
clip = "idle"

[[hurtbox]]
bone = 1
radius = 9.0
part = 1
"""


@pytest.fixture
def offline(tmp_path, monkeypatch):
    monkeypatch.delenv("MHFU_DATA", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = tmp_path / "t.toml"
    path.write_text(PORT)
    return path


def test_scene(synthetic_pac, tmp_path, capsys):
    pac = tmp_path / "x.bin"
    pac.write_bytes(synthetic_pac)
    assert main(["port", "scene", str(pac), "--clips", "--groups", "--slot", "1"]) == 0
    out = capsys.readouterr().out
    assert "3 bones" in out and "[partial]" in out and "clip clip_01  frame 5/10" in out


def test_check(offline, synthetic_pac, capsys):
    assert main(["port", "check", str(offline), "--no-pac"]) == 0
    assert "[INTEL_ABSENT]" in capsys.readouterr().out
    assert main(["port", "check", str(offline), "--no-pac", "--strict"]) == 1
    pac = offline.with_name("t.bin")
    pac.write_bytes(synthetic_pac)
    assert main(["port", "check", str(offline), "--pac", str(pac)]) == 0
    assert main(["port", "check", str(offline), str(offline), "--pac", str(pac)]) == 1


def test_align(offline, capsys):
    assert main(["port", "align", str(offline)]) == 0
    assert "INTEL_ABSENT" in capsys.readouterr().out
    assert main(["port", "align", str(offline), "--pair", "1,4", "--slot", "1"]) == 0
    assert "(1,4) -> (1,4)  clip idle" in capsys.readouterr().out


def test_hit(offline, games, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["port", "hit", str(offline)]) == 1
    assert "build its intel" in capsys.readouterr().err
    data = ["--data", str(games.fu.root)]
    assert main(["port", "hit", str(offline), "--print", *data]) == 0
    assert 'P.hit("t", {' in capsys.readouterr().out
    assert main(["port", "hit", str(offline), "--capacity", "0", *data]) == 0
    assert (tmp_path / "t_hit.lua").is_file() and "left out" in capsys.readouterr().out
    stick = tmp_path / "PSP"
    (stick / inject.MODS_SUBDIR).mkdir(parents=True)
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(stick),))
    out = tmp_path / "o.lua"
    assert main(["port", "hit", str(offline), "-o", str(out), "--deploy", *data]) == 0
    assert (stick / inject.MODS_SUBDIR / "o.lua").read_text() == out.read_text()
    assert "deployed o.lua + lib/" in capsys.readouterr().out
    assert main(["port", "push", str(offline), "--dry", *data]) == 0
    assert capsys.readouterr().out.endswith("80 B  hurtboxes  (2 guard(s))\n")


def test_clips(games, ports, tmp_path, capsys):
    data = ["--data", str(games.fu.root), "--p3rd-data", str(games.p3rd.root)]
    assert main(["port", "clips", str(ports / "zinogre.toml"), *data]) == 0
    out = capsys.readouterr().out
    assert "102 populated slot(s): 102 carried, 0 filler" in out and "DROPPED" not in out
    assert main(["port", "clips", str(ports / "zinogre.toml"), "--slots", *data]) == 0
    assert "a1 2    CARRIED  welcome_howl" in capsys.readouterr().out
    path = tmp_path / "z.toml"
    shutil.copyfile(ports / "zinogre.toml", path)
    labels = tmp_path / "l.txt"
    labels.write_text("3 -> packed there\n7 -> a new one\n")
    args = ["port", "clips", str(path), "--import-labels", str(labels), *data]
    assert main(args) == 0
    assert "+[clips.clip_07]" in capsys.readouterr().out and "clip_07" not in path.read_text()
    assert main([*args, "--write"]) == 0
    m = manifest.load(path)
    assert m.clips["clip_07"].labelled_build.startswith("unrecorded: l.txt")
    assert (m.clips["clip_03"].source, m.clips["clip_07"].source) == (24, 7)
    assert m.clips["clip_07"].slot is None, "a name pins nothing"


def test_ports_check(games, ports, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    data = ["--data", str(games.fu.root), "--p3rd-data", str(games.p3rd.root)]
    paths = [str(ports / "zinogre.toml"), str(ports / "brute_tigrex.toml")]
    assert main(["port", "check", *paths, *data]) == 0
    assert (tmp_path / "mhfu-studio" / "species" / "em75.json").is_file()
    assert main(["port", "scene", paths[0], "--side", "source", *data]) == 0
    assert "anim map  37 records" in capsys.readouterr().out
