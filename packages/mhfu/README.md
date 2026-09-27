# mhfu

What the modkit knows about Monster Hunter Freedom Unite (PSP, EU release ULES01213) at runtime.

## Addresses

`src/mhfu/addresses.toml` is the one list of game addresses and struct layouts. Python reads it
directly; the framework's C header and Lua table are generated from it, so a new address is added
in this file only.

```python
from mhfu import addresses

addresses.SCREEN_STATE  # <SCREEN_STATE 0x08A8CA48 u8>, usable as an int
addresses.SCREEN_STATE.doc  # what it is
addresses.ENTITY.HP  # <HP +0x2E4 u16>, the field's offset
```

```bash
python -m mhfu.addresses c -o addresses.gen.h      # MHFU_SCREEN_STATE, MHFU_ENTITY_HP, ...
python -m mhfu.addresses lua -o addresses.gen.lua  # mhfu.addr.SCREEN_STATE, mhfu.addr.ENTITY.HP, ...
```

Product code in this repository never writes an address as a number: a pre-commit check rejects
one. For a value that only looks like an address, such as a MIPS jump opcode, end the line with
a `noaddr` comment.
