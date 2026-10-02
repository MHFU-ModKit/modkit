"""`hud --shot` and `--read-only` against a fake PPSSPP; LIVE's keys and every layout reach no
debugger client."""

from pathlib import Path

import mhfu_hud
import pygame
import pytest
from mhfu_hud import writer as writer_module
from mhfu_hud.cli import main
from mhfu_hud.state import Context

LAYOUTS = Path(mhfu_hud.__file__).parent / "layouts"


@pytest.fixture
def built(monkeypatch):
    """Every GameWriter the CLI builds."""
    out = []
    init = writer_module.GameWriter.__init__

    def record(self, source):
        out.append(self)
        init(self, source)

    monkeypatch.setattr(writer_module.GameWriter, "__init__", record)
    return out


@pytest.mark.parametrize("read_only", [False, True])
def test_shot(fake, tmp_path, built, read_only):
    out = tmp_path / "shots"
    argv = ["--port", str(fake.port), "--poll-hz", "20", "--shot", str(out)]
    assert main(argv + ["--read-only"] * read_only) == 0
    assert sorted(p.name for p in out.iterdir()) == ["ai_mod.png", "live.png", "quest_prep.png"]
    assert len(built) == (0 if read_only else 1)
    assert not fake.writes


def test_live_keys(app, reader, recorder, snaps):
    """The old size and species keys: not taken, nothing staged or written."""
    q = app.layouts[Context.QUEST]
    keys = [pygame.K_EQUALS, pygame.K_MINUS, pygame.K_COMMA, pygame.K_PERIOD, pygame.K_LESS]
    for key in keys + [pygame.K_PAGEUP, pygame.K_PAGEDOWN, pygame.K_KP_PLUS]:
        assert not q.handle_key(key, snaps["quest"])
    assert reader.sections == [] and recorder.writes == []
    assert app.writer.status.staged == ()


def test_no_client():
    for path in LAYOUTS.glob("*.py"):
        text = path.read_text()
        for word in ("_client", "Client", "Live(", "ppsspp_debug", "mhfu.memory"):
            assert word not in text, f"{path.name}: {word}"
