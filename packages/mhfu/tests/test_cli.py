from mhfu.cli import main


def test_addresses(capsys):
    assert main(["addresses", "lua"]) == 0
    assert "SCREEN_STATE" in capsys.readouterr().out
