import base64
import io
import time

import pytest
from mhfu import addresses as a
from mhfu import hitbox, hitzone
from mhfu.cli import main
from mhfu.live import Session, shell_anim
from mhfu.live import shell as shell_module
from mhfu.live.shell import FREEZE_BITS, Shell
from mhfu.structs import ACTION_INPUT_BASE

MON = a.RAM.start + 0x90_0000  # entities, hurtbox set and grids inside the fake's memory
MON2 = MON + 0x1_0000
SET = a.RAM.start + 0xA0_0000
GRIDS = a.RAM.start + 0xA1_0000
ACTIONS = a.RAM.start + 0xA2_0000
E = a.ENTITY
BRIDGE = a.CLI_BRIDGE_BLOCK
B = a.CLI_BRIDGE


@pytest.fixture
def sh(s):
    return Shell(s, stdout=io.StringIO())


def run(shell: Shell, line: str) -> str:
    out = shell.stdout
    out.seek(0)
    out.truncate()
    shell.onecmd(line)
    return out.getvalue().rstrip("\n")


def until(done, seconds: float = 3.0) -> None:
    """Real time, for the shell's background threads."""
    end = time.monotonic() + seconds
    while not done():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


def monster(fake, base=MON, slot=1, species=0x4B, hp=900, vtable=a.TIGREX_VTABLE):
    fake.poke("I", a.ENTITY_REGISTRY + 4 * slot, base)
    fake.poke("I", base + E.VTABLE, vtable)
    fake.poke("B", base + E.SPECIES, species)
    fake.poke("H", base + E.HP, hp)
    fake.poke("f", base + E.SIZE_SCALE, 1.0)
    fake.poke("3f", base + E.POSITION, 1.0, 2.0, 3.0)


def test_help(sh):
    assert "  anim play slot action  " in run(sh, "help")
    assert run(sh, "help get mon").startswith("usage: get mon slot [field]")
    assert "one of: get player hp, get player pos" in run(sh, "get player")
    assert "invalid integer value" in run(sh, "set player hp many")
    assert run(sh, "nonsense").startswith("! unknown command 'nonsense'")
    assert run(sh, "# a comment") == ""
    assert sh.onecmd("quit") and sh.onecmd("EOF")


def test_complete(sh):
    assert sh.completenames("an") == ["anim"]
    assert sh.completedefault("p", "anim p", 5, 6) == ["play"]
    assert sh.completedefault("", "get player ", 11, 11) == ["hp", "pos", "stamina"]


def test_player(sh, fake):
    assert run(sh, "get player hp").startswith("! no player entity")
    p = a.PLAYER_ENTITY
    fake.poke("I", p + E.VTABLE, a.PLAYER_ENTITY_VTABLE)
    fake.poke("H", p + E.HP, 55)
    fake.poke("H", p + E.HP_CAP, 100)
    fake.poke("H", p + E.MAX_HP, 150)
    assert run(sh, "get player hp") == "HP 55/150 (cap 100)"
    run(sh, "set player hp 0x64")
    assert run(sh, "get player hp") == "HP 100/150"
    run(sh, "set player pos 1 2.5 -3")
    assert fake.peek("3f", p + E.POSITION) == (1.0, 2.5, -3.0)
    assert run(sh, "get player pos") == "pos (1.0, 2.5, -3.0)"
    run(sh, "set player stamina 120")
    assert fake.peek("H", a.PLAYER_STAMINA) == fake.peek("H", a.PLAYER_STAMINA_HEAP) == (120,)
    assert run(sh, "get player stamina") == "stamina 120"


def test_timer_and_sys(sh, fake):
    run(sh, "set timer 90")
    t = a.QUEST_SINGLETON + a.QUEST.TIMER
    assert fake.peek("I", t) == fake.peek("I", a.QUEST_TIMER_MIRROR) == (2700,)
    assert run(sh, "get timer") == "quest timer 01:30 (2700 frames)"
    fake.poke("B", a.SCREEN_STATE, 17)
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_QUEST)
    fake.poke("H", a.AREA_INDEX, 100)
    assert run(sh, "sys screen") == "screen 17 (IN_AREA), scene SCENE_QUEST"
    assert run(sh, "sys section") == "area 100, sub-area 0"
    monster(fake)
    assert run(sh, "sys status") == (
        "screen=17 (IN_AREA) area=100 timer=01:30 monsters=1 player_hp=n/a"
    )


def test_paint(sh, fake):
    assert run(sh, "paint on").startswith("map paint on")
    until(lambda: fake.peek("B", a.MAP_PAINT) == (0xFF,))
    run(sh, "paint off")
    assert fake.peek("B", a.MAP_PAINT) == (0,) and not sh.tasks


def test_monsters(sh, fake):
    assert run(sh, "ls mon") == "(no monsters loaded)"
    monster(fake)
    monster(fake, MON2, slot=3, species=0x46, hp=100, vtable=a.POPO_VTABLE)
    rows = run(sh, "ls mon").splitlines()
    assert rows[0].split() == ["slot", "entity", "species", "name", "hp", "size", "pos"]
    assert rows[2].split()[2:5] == ["0x4B", "Tigrex", "900"]
    assert rows[3].split()[2:5] == ["0x46", "Popo", "100"]
    fake.poke("I", a.QUEST_SINGLETON + a.QUEST.TARGETS + a.QUEST_TARGET.EM_ID, 0x4B)
    fake.poke("H", a.QUEST_SINGLETON + a.QUEST.TARGETS + a.QUEST_TARGET.COUNT, 1)
    assert len(run(sh, "ls bigmon").splitlines()) == 3
    assert run(sh, "get mon 1 species") == "species = 0x4B Tigrex"
    assert "pos = (1.0, 2.0, 3.0)" in run(sh, "get mon 1")
    assert run(sh, "get mon 2").startswith("! no entity in slot 2")
    run(sh, "set mon 1 size 2")
    assert fake.peek("f", MON + E.SIZE_SCALE) == fake.peek("f", MON + E.SIZE_RADIUS) == (2.0,)
    assert fake.peek("3f", MON + E.RENDER_SCALE) == (2.0, 2.0, 2.0)
    run(sh, "set mon 1 hp 5")
    assert fake.peek("H", MON + E.HP) == (5,)
    run(sh, "set mon 1 pos 4 5 6")
    assert fake.peek("3f", MON + E.POSITION) == (4.0, 5.0, 6.0)
    assert run(sh, "set mon 1 pos 4") == "! pos takes 3 value(s)"


def test_aggro(sh, fake):
    monster(fake)
    sight = a.SPECIES_TABLE + 0x4B * a.SPECIES.stride + a.SPECIES.SIGHT_RADIUS
    fake.poke("f", sight, 1500.0)
    run(sh, "mon aggro 1 off")
    assert fake.peek("f", sight) == fake.peek("f", MON + E.ENGAGE) == (0.0,)
    run(sh, "mon aggro 1 off")  # a second calm keeps the first radius
    run(sh, "mon aggro 1 on")
    assert fake.peek("f", sight) == (1500.0,) and fake.peek("f", MON + E.ENGAGE) == (1.0,)


def test_state(sh, monkeypatch):
    assert "no savestate commands" in run(sh, "state save /tmp/x.ppst")
    calls = []
    monkeypatch.setattr(sh.session.client, "load_state", calls.append)
    assert run(sh, "state load /tmp/x.ppst").startswith("loaded /tmp/x.ppst")
    assert calls == ["/tmp/x.ppst"]


def test_anim_ls_and_table(sh, fake):
    monster(fake)
    fake.poke("3H", MON + E.ANIM_INPUT, ACTION_INPUT_BASE + 5, 1205, 1405)
    fake.poke("I", MON + E.FREEZE_GATE, FREEZE_BITS)
    assert run(sh, "anim ls 1") == (
        "mon 1 Tigrex: action 5 (inputs 1005/1205/1405), move (0, 0), AI halted"
    )
    assert "not a pointer" in run(sh, "anim table 1")
    fake.poke("I", MON + E.ACTION_LIST, ACTIONS)
    for r in range(3):
        fake.poke("BB", ACTIONS + 8 * r, r, 0xFF)
    rows = run(sh, "anim table 1 --rows 3").splitlines()[-3:]
    assert [r.split()[2:4] for r in rows] == [["0", "0xFF"], ["1", "0xFF"], ["2", "0xFF"]]


def test_record(sh, fake):
    monster(fake)
    fake.poke("B", MON + E.MAIN_STATE, 3)
    assert "MAIN_STATE" in run(sh, "anim record 1 start --cell main_state")
    rec = sh.tasks["record 1"]
    until(lambda: rec.hits[3] >= 2)
    fake.poke("B", MON + E.MAIN_STATE, 7)
    until(lambda: rec.hits[7] >= 1)
    out = run(sh, "anim record 1 stop").splitlines()
    assert "2 distinct MAIN_STATE values" in out[0]
    assert [r.split()[0] for r in out[3:]] == ["3", "7"]
    assert run(sh, "anim record 1 stop") == "not recording mon 1"
    assert "not an integer ENTITY field" in run(sh, "anim record 1 start --cell position")


@pytest.fixture
def lua(fake):
    """Extra RAM for the bridge block, and cli_bridge.lua's side of it, run on each ACK read."""
    fake.memory.extend(bytes(BRIDGE + 0x40 - fake.base - len(fake.memory)))

    def tick(address):
        if address != BRIDGE + B.ACK:
            return
        magic, seq, cmd, _slot, arg = fake.peek("5I", BRIDGE)
        if magic == shell_anim.MAGIC and fake.peek("I", BRIDGE + B.ACK) != (seq,):
            fake.poke("I", BRIDGE + B.ACK, seq)
            if cmd == shell_anim.Op.FORCE_ACTION:
                fake.poke("I", BRIDGE + B.STATUS, arg)
                fake.poke("I", MON + E.CLIP, MON + 0x100 * (arg // 2))
                fake.poke("I", MON + E.ACTION_TABLE, MON)

    fake.on_read.append(tick)
    return fake


def test_bridge_unreachable(sh, fake):
    monster(fake)
    assert run(sh, "bridge status").startswith("bridge not reachable")
    assert run(sh, "anim play 1 5").startswith("! bridge not reachable")


def test_bridge(sh, lua):
    monster(lua)
    lua.poke("I", BRIDGE + B.SEQ, 41)
    out = run(sh, "anim play 1 7").splitlines()
    assert "[seq 42]" in out[0] and out[1] == "  acked and held on a live monster"
    assert lua.peek("5I", BRIDGE) == (shell_anim.MAGIC, 42, 1, 1, 7)
    [write] = [m for m in lua.received if m["event"] == "memory.write" and m["address"] == BRIDGE]
    assert len(base64.b64decode(write["base64"])) == B.ACK  # the whole command in one write
    assert "in step" in run(sh, "bridge status")
    assert run(sh, "anim stop 1") == "released mon 1 [seq 43]"
    assert lua.peek("I", BRIDGE + B.STATUS) == (0,)
    lua.poke("I", MON + E.FREEZE_GATE, FREEZE_BITS | 1)
    run(sh, "ai freeze 1 off")
    assert lua.peek("I", MON + E.FREEZE_GATE) == (1,)


def test_bridge_stalled(sh, lua):
    monster(lua)
    lua.on_read.clear()
    assert "no ack" in run(sh, "anim play 1 7")
    assert "behind" in run(sh, "bridge status")
    assert run(sh, "anim stop 1").endswith(f"; no ack: {shell_anim.STALLED}")
    assert "no ack at action 0" in run(sh, "anim sweep 1 0 3")


def test_sweep(sh, lua, tmp_path):
    monster(lua)
    csv = tmp_path / "sweep.csv"
    out = run(sh, f"anim sweep 1 0 3 --dwell 0 --out {csv}").splitlines()
    assert "4 actions reach 2 clips" in out
    assert "0x000000  0,1" in out and "0x000100  2,3" in out
    assert csv.read_text().splitlines()[1:3] == ["0,0,0x0,1", "1,0,0x0,1"]
    assert lua.peek("I", BRIDGE + B.CMD) == (shell_anim.Op.CLEAR,)


def test_watch_and_py(sh, fake, monkeypatch):
    fake.poke("H", a.AREA_INDEX, 7)
    out = run(sh, "watch -n 2 -i 0 sys section").splitlines()
    assert out == [
        "watch [1/2] sys section",
        "area 7, sub-area 0",
        "watch [2/2] sys section",
        "area 7, sub-area 0",
    ]
    seen = {}
    monkeypatch.setattr(shell_module.code, "interact", lambda **kw: seen.update(kw["local"]))
    run(sh, "py")
    assert {"s", "game", "player", "monsters", "a", "run"} <= seen.keys()


def test_errors_keep_the_shell(sh, monkeypatch):
    monkeypatch.setattr(Shell, "monster", lambda self, slot: 1 / 0)
    assert run(sh, "anim ls 1") == "! ZeroDivisionError: division by zero"


def test_connect(sh, fake):
    assert run(sh, f"connect --port {fake.port}") == f"attached to port {fake.port}"
    sh.session.close()


# --- hitzone and hitbox ---


def hurtboxes(fake, species=0x4B):
    row = a.SPECIES_TABLE + species * a.SPECIES.stride
    fake.poke("I", row + a.SPECIES.HURTBOX_SET, SET)
    for i in range(3):
        va = SET + i * hitzone.STRIDE
        fake.poke("4H", va, i + 1, i % 2, i, i)
        fake.poke("f", va + a.HIT_VOLUME.RADIUS, 50.0)
    fake.poke("4H", SET + 3 * hitzone.STRIDE, *[hitzone.SENTINEL_BONE] * 4)
    grid = a.HITZONE_GRID.size
    table = GRIDS + 2 * grid
    fake.poke("I", row + a.SPECIES.HITZONE_STATES, table)
    fake.poke("2I", table, GRIDS, GRIDS + grid)
    fake.poke("B", GRIDS + grid + 3, 45)  # state 1, row 0, shot


def test_hitzone(sh, fake):
    monster(fake)
    hurtboxes(fake)
    fake.poke("b", MON + E.HITZONE_STATE, 1)
    out = run(sh, "hitzone show 1").splitlines()
    assert out[0] == f"mon 1 Tigrex: 3 hurtboxes at 0x{SET:08X}"
    assert out[3].split()[:3] == ["0", "1", "sphere"] and out[4].split()[2] == "capsule"
    assert out[-10] == f"hitzone state 1 at 0x{GRIDS + a.HITZONE_GRID.size:08X}, current:"
    assert out[-7].split()[:5] == ["0", "0", "0", "0", "45"]
    run(sh, "hitzone setvol 1 1 9 75.5")
    v = hitzone.HitVolume(sh.mem, SET + hitzone.STRIDE)
    assert (v.bone, v.shape, v.radius) == (9, 1, 75.5)
    assert "not 0..2" in run(sh, "hitzone setvol 1 3 9 1")
    run(sh, "hitzone setwk 1 0 6 ice 150")
    assert fake.peek("B", GRIDS + 6 * hitzone.GRID_COLS + 8) == (150,)
    run(sh, "hitzone setwk 1 1 2 0 10")
    assert fake.peek("B", GRIDS + a.HITZONE_GRID.size + 2 * hitzone.GRID_COLS) == (10,)
    assert "percent 0..255" in run(sh, "hitzone setwk 1 0 7 0 10")


def test_hitbox_needs_data(sh, fake, monkeypatch):
    monkeypatch.delenv("MHFU_DATA", raising=False)
    monster(fake)
    assert "no extracted game" in run(sh, "hitbox show 1")


def test_hitbox(sh, fake, game):
    ovl = game.em(75)
    fake.memory[ovl.base - fake.base : ovl.end - fake.base] = ovl.data
    sh.data = game.root
    monster(fake)
    [t] = [t for t in hitbox.tables(ovl) if len(t.attacks) > 100]
    out = run(sh, "hitbox show 1").splitlines()
    assert out[0] == f"mon 1 Tigrex: {len(t.attacks)} attacks at 0x{t.records:08X} (em75.ovl)"
    run(sh, "hitbox set 1 1 power 99")
    assert fake.peek("B", t.records + hitbox.RECORD + a.ATTACK_RECORD.POWER) == (99,)
    one = run(sh, "hitbox show 1 1").splitlines()
    assert "power 99" in one[0] and len(one) == 3 + len(t.volume_for(1).volumes)
    run(sh, "hitbox setvol 1 1 0 5 12.5")
    v = hitzone.HitVolume(sh.mem, t.volume_for(1).va)
    assert (v.bone, v.radius) == (5, 12.5)
    monster(fake, MON2, slot=2, species=76)  # a subspecies em75.ovl serves
    assert run(sh, "hitbox show 2").splitlines()[0].endswith("(em75.ovl)")
    fake.poke("I", t.volume_table, 0)
    assert "relocated" in run(sh, "hitbox show 1")


# --- the command ---


def test_cli(fake, monkeypatch, tmp_path, capsys):
    attach = Session.attach
    monkeypatch.setattr(shell_module, "HISTORY", tmp_path / "history")
    monkeypatch.setattr(Session, "attach", lambda port, host: attach(fake.port, timeout=5))
    monkeypatch.setattr("sys.stdin", io.StringIO("sys section\nquit\n"))
    fake.poke("H", a.AREA_INDEX, 3)
    assert main(["shell"]) == 0
    assert "area 3, sub-area 0" in capsys.readouterr().out


def test_cli_fails_cleanly(monkeypatch, capsys):
    def attach(port, host):
        raise ConnectionError("no debugger")

    monkeypatch.setattr(Session, "attach", attach)
    assert main(["shell", "--port", "1"]) == 1
    assert "mhfu shell: no debugger" in capsys.readouterr().err
