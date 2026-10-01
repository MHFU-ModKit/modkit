# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import hashlib
import json

from mhfu import files
from mhfu.em import intel
from mhfu_studio.monster import species as sp


def test_root(tmp_path):
    (tmp_path / "em75.json").write_text(json.dumps({"host_species": 75, "pairs": []}))
    (tmp_path / "emx.json").write_text("{}")
    assert sp.available(tmp_path) == [75]
    assert sp.find(75, tmp_path).host_species == 75 and sp.find(7, tmp_path) is None
    assert [h.species for h in sp.survey(tmp_path)] == [75]


def test_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    game = tmp_path / "game"
    (game / "data_files").mkdir(parents=True)
    ovl = game / "data_files" / f"file_{files.em_overlay(75):05d}.bin"
    ovl.write_bytes(b"one")
    builds = []

    def build(g, species):
        builds.append(species)
        sha1 = hashlib.sha1(ovl.read_bytes()).hexdigest()
        return {"host_species": species, "overlay": {"sha1": sha1}, "pairs": []}

    monkeypatch.setattr(intel, "build", build)
    assert sp.find(75, data=game).host_species == 75 and builds == [75]
    assert (sp.cache_dir() / "em75.json").is_file()
    assert sp.find(75, data=game) is not None and builds == [75]
    ovl.write_bytes(b"two")
    sp.find(75, data=game)
    assert builds == [75, 75]
    assert sp.find(76, data=game) is None
    monkeypatch.delenv("MHFU_DATA", raising=False)
    assert sp.find(75) is None
