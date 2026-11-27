# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhfu_studio.map.document import DocumentError, check_file

CORPUS = sorted((Path(__file__).parent / "schema").glob("*/map.toml"))


def test_corpus_found():
    assert CORPUS


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.parent.name)
def test_corpus_loads(path: Path):
    doc = check_file(path)
    assert doc.stages and doc.stage(107).ops is doc.stage(98).ops


def test_bad_op_raises(tmp_path: Path):
    (tmp_path / "map.toml").write_text(
        '[map]\nname = "x"\n[[stage]]\nnumber = 98\nops = "a.json"\n', encoding="utf-8"
    )
    (tmp_path / "a.json").write_text('[{"op": "bogus"}]', encoding="utf-8")
    with pytest.raises(DocumentError, match="unknown-op"):
        check_file(tmp_path)


def test_missing_asset_raises(tmp_path: Path):
    (tmp_path / "map.toml").write_text(
        '[map]\nname = "x"\n[[stage]]\nnumber = 98\nops = "a.json"\n', encoding="utf-8"
    )
    (tmp_path / "a.json").write_text(
        '[{"op": "texture", "slot": 0, "png": "nope.png"}]', encoding="utf-8"
    )
    with pytest.raises(DocumentError, match="missing-asset"):
        check_file(tmp_path)
