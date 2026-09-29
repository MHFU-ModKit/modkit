import pytest
from mhfu import inject
from mhfu.cli import main


def test_names():
    assert inject.file_id_from_name("any/dir/file_06185.bin") == 6185
    assert inject.inject_filename(6185) == "file_06185.bin"
    assert inject.relocate_filename(6185) == "file_06185_grown.bin"
    assert inject.orig_filename(6185) == "file_06185.bin.orig"
    for bad in ("tigrex.bin", "file_12.bin"):
        with pytest.raises(ValueError):
            inject.file_id_from_name(bad)


def test_write_inject_bytes(tmp_path):
    path = inject.write_inject_bytes(b"new" * 9, 6185, tmp_path, orig=b"old" * 9)
    assert path == str(tmp_path / "file_06185.bin")
    assert (tmp_path / "file_06185.bin").read_bytes() == b"new" * 9
    assert (tmp_path / "file_06185.bin.orig").read_bytes() == b"old" * 9
    assert sorted(p.name for p in tmp_path.iterdir()) == ["file_06185.bin", "file_06185.bin.orig"]


def test_write_relocate_bytes(tmp_path):
    d = tmp_path / "made"
    path = inject.write_relocate_bytes(b"grown", 6185, str(d))
    assert path == str(d / "file_06185_grown.bin")
    assert [p.name for p in d.iterdir()] == ["file_06185_grown.bin"]


def test_named(tmp_path):
    path = inject.write_relocate_bytes(b"grown", 6185, tmp_path, orig=b"o", name="zinogre.bin")
    assert path == str(tmp_path / "zinogre.bin")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["file_06185.bin.orig", "zinogre.bin"]


def test_default_inject_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(tmp_path / "none"), str(tmp_path)))
    d = inject.default_inject_dir()
    assert d == str(tmp_path / inject.INJECT_SUBDIR) and (tmp_path / inject.INJECT_SUBDIR).is_dir()
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(tmp_path / "none"),))
    with pytest.raises(FileNotFoundError):
        inject.default_inject_dir()


def test_cli(tmp_path, capsys):
    pac, orig = tmp_path / "edited.pac", tmp_path / "orig.pac"
    pac.write_bytes(b"e" * 8)
    orig.write_bytes(b"o" * 8)
    out = tmp_path / "inject"
    assert main(["inject", "6185", str(pac), "--orig", str(orig), "--dir", str(out)]) == 0
    assert (out / "file_06185.bin").read_bytes() == b"e" * 8
    pac.write_bytes(b"e" * 9)
    assert main(["inject", "6185", str(pac), "--orig", str(orig), "--dir", str(out)]) == 1
    assert "--relocate" in capsys.readouterr().err
    args = ["inject", "6185", str(pac), "--orig", str(orig), "--dir", str(out), "--relocate"]
    assert main(args) == 0
    assert (out / "file_06185_grown.bin").read_bytes() == b"e" * 9


def test_cli_orig_from_the_game(game, tmp_path):
    pac = tmp_path / "file_06185.bin"
    pac.write_bytes(game.read(6185))
    out = tmp_path / "i"
    assert main(["inject", "6185", str(pac), "--dir", str(out), "--data", str(game.root)]) == 0
    assert (out / "file_06185.bin.orig").read_bytes() == game.read(6185)
