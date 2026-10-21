# MHP3rd AI script

A monster's brain in MHP3rd is bytecode, as in MHFU, with its own opcode numbering and no gate
operand. The script picks what the monster does next; the moves themselves are code in the
overlay. Read and written by `mhfu_port.brain` (`mhfu-port brain ID`): the 71 ids that have a
script lift into trees, and each routine encodes back to the game's bytes. It is static
research: nothing here has run on a PSP or in PPSSPP, and no part of it is carried into MHFU
yet. The MHFU script is [ai_script.md](ai_script.md).

## Finding a script

- game_task's species table has one 8-byte row `{u32 records, u32 info}` per monster id (1 to 74).
  `info & 0xFF` is the number of slots; `records` points at `{u32 descriptor, u32 file}` pairs,
  one per slot (m0, m1, ...). The engine file id is the extracted one plus 1 (`0xFFFF`: the
  descriptor sits in game_task). m0 and m1 are the same code linked twice; 33 ids have four slots (m2 and m3 too).
- A **descriptor** is in the em overlay. Its `+0x14` points at a table of 13 list pointers, in
  this order: base (routine 0 starts the script), call, walk, event interrupts 3 to 7, the
  pending-state pair, death, hit reaction, event interrupt 11, trigger. `p3ops.LISTS` names them.
- A **list** is `u32` routine addresses, 0 for an empty slot, and has no length: it ends where
  the next item starts, usually after one zero word. An item is a descriptor, a list table, a
  list or a routine that a descriptor field or a list slot points at; finding every extent is a
  fixpoint (`p3.Region`).
- Sibling ids share lists (Brute Tigrex 23 and Tigrex 58), except the pairs {6, 20}, {8, 54},
  {9, 55} and {19, 57}, which share an overlay but not their scripts. Ids 65, 66 and 67 have no
  script.
- A routine is named `<list>_<index>` after the first slot that holds it (`base_0`, `call_12`,
  `death_0`).

## Instructions

An instruction is an opcode byte and `u8` operands. The opcodes are 0x00 to 0x6F and 0xFF;
`p3ops.OPS` has each one's name, kind, operands, flow and a line on what it does. 0x10 is the same
end as 0xFF (`end_alias`). 0x70 to 0xFE do not exist. A name with an offset in it (`if_flag_bca`)
is a placeholder for a byte the species code or an unnamed field owns.

The length comes from one rule, the game's own: a plain opcode is 1 byte plus its operands. A
block opcode has a sub-code after it:

| Sub-code | Marker | Bytes |
|---|---|---|
| 0 | opens the block, with the opener's operands | 2 to 4 |
| 1 | a case: one value byte (a case value, or a random branch's weight) | 3 |
| 2 | else (an if) or default | 2 |
| 3 | break (`repeat` only) | 2 |
| 0xFF | ends the block | 2 |

The block shapes (`p3ops.Kind`): 36 ifs, 41 switches, `random`, `repeat` (a loop) and `transit` (a
bracket). An if's true-branch sits between the opener and its first marker. A `random`'s weights
add to 100 against a draw % 100; `switch_roll` does the same with its case values. Blocks nest,
and a marker belongs to the innermost open block of its opcode: a `repeat`'s break sits inside
the ifs of its body, and the decoder keeps it as a `Leave` there.

A routine runs from its entry to an end instruction at block depth 0. Nothing jumps inside a
routine, so decoding it linearly is exact. An end inside a block is an ordinary instruction (a
dead end after `goto_base`).

- `enter` and `wait` yield; everything else runs on.
- `call` runs a routine of list 1, `call_base` one of list 0, `call_list2` routine 0 of list 2;
  all come back. `goto_base` restarts routine 0 of list 0.
- What follows an end depends on the list its routine runs from.

## Checking

`mhfu-port brain --check` lifts every id and slot and compares each routine with its bytes. It
holds when no opcode is unknown, no byte after a routine is unaccounted for beyond zero padding,
every routine encodes to its own bytes, and the only problem is em003's: its list 1 routine 59
(ids 3 and 51, in each slot) has no end and runs into the next item. `p3ops.length` is checked
against the game's length table for all 0x70 opcodes in a game-data test.

## Open

- Which hit raises each interrupt list, and the format of list 2.
- The dispatch budget (a run stops after 1000 steps) and what it means for a ported script.
