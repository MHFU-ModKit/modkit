# AI script

A big monster's brain is bytecode: game_task's `AI_SCRIPT` interprets it. The script picks what
the monster does next; the moves themselves (`(main, sub)` pairs) are code in the overlay. Of the
90 species rows, 42 have their script in one of the 17 big-monster em overlays (em75 also serves
rows 76, 81 and 88), 43 in game_task and 5 have none. Read and written by `mhfu.em.script`
(`mhfu script EM`): each of the 42 rows lifts into a program that assembles back to the same bytes.
`mhfu script EM --row N` also disassembles one of the 43, which are not lifted or edited.

## Finding a script

`SCRIPT_HEADERS` holds one row per species, `SPECIES.stride` apart: 17 `u32` header addresses, one
per map type. `SCRIPT_INIT` copies the one for the quest's map type into the entity's
`SCRIPT_HEADER` and starts routine 0 of the base list. Of the 42 rows, 25 use one header for every
map type and 17 have two to eight: em01's own row and em20's have seven.

A header is a run of `u32` fields, each the address of a list, followed by `f32` parameters. A list
is `u32` routine addresses with no terminator; 0 is an unused slot. One `call` names one: em82's header
for map types 5, 13, 14 and 15 calls `call` slot 15, an empty slot inside its list. A species has 17 to 36
fields:

| Field | Name | Holds |
|---|---|---|
| 0 | base | Routines; `goto` names them, routine 0 starts the script |
| 1 | call | Routines for `call` (0x81) |
| 2 | walk | Routines and stage arrays for `walk` (0x0F), mixed |
| 3, 4 | int_0x10, int_0x20 | Interrupt routine for that `SCRIPT_PENDING` bit (only slot 0 is read) |
| 5 | stages | Data: `u16` stage numbers, zero-terminated; an array may run on into the next |
| 6 | loop | Loop bodies |
| 7 | pick | Data: weighted-pick records |
| 8 | int_0x02 | Interrupt routine |
| 9 | ret | Routines for `call_ret` (0x16) |
| 10, 11 | int_0x04, int_0x08 | Interrupt routines |
| 12 | node | Route arrival routines |
| 13, 14 | int_0x80, int_0x40 | Interrupt routines |
| 15 + k | deep*k* | Routines for `call_deep` (0x82) table *k* |

## Instructions

An instruction is an opcode byte and its operands. 130 opcodes exist (0x01 to 0x99 with gaps, and
0xFF); `mhfu.em.script.OPCODES` has each one's name, kind and layout. The names are modkit's. Operands
are `u8` unless the layout says otherwise; a `u16` (a section number, a mask) is **big-endian**,
`s8` is signed.

Structure is inline. A block opcode is followed by a sub-code: 0 opens the block, the others are
markers inside it, and one closes it. The markers repeat the opener's opcode:

| Shape | Sub-codes |
|---|---|
| if | 1 else, 2 end |
| switch | 1 case, 2 default, 3 end; the header is `n` and the first case follows as a marker |
| repeat | 1 end |
| random | 1..13 branch (a weight each), 0xFF end; 0x85 (`random_shifted`) has its branches as 0x80 markers |
| bucket (0x83), zone (0x62) | cases numbered 1..5 and 1..3, 0xFF end; a bucket's header is `n`, one less than its markers |

An if that fails, or a case that does not match, steps forward to the next marker of its block,
skipping inner blocks of the same opcode whole. `SCRIPT_SKIP` is the game's own length table for
that walk. `OPCODES` follows it, apart from two things: the game steps a switch, random or bucket
header together with its first marker, which `OPCODES` lists as a marker of its own, and it steps
0x94's header (`switch_enemy_var`) 5 bytes, one short of header and marker, onto the first case
value.

## Flow and running

- `call` (0x81), `call_ret` (0x16) and `call_deep` (0x82) run a routine from their list and come
  back; two call levels exist. `walk` (0x0F) runs routines along a route. `goto` (0x07) continues in
  a base routine. `end` (0xFF) returns, restarts or ends an interrupt; its operand says which
  (`end return`, `end base`, ...: `END_KINDS` has the 17 kinds the game acts on).
- A routine is decoded from its entry along every path; each path ends at a `goto` or an `end`.
  Data tables sit between routines, so a linear sweep misreads them.
- `AI_SCRIPT` runs when `SCRIPT_WAKE` is set and `SCRIPT_WAIT` is 0, at most 999 instructions a
  run (the 1000th ends it with no new resume address). Only `enter` (0x05, `main sub gate`) ends a
  run: it enters the pair through enter-action mode 1 and saves the next address in `SCRIPT_PCS`,
  under the slot `SCRIPT_CONTEXT` picks. The next run resumes there.
- A raised interrupt bit in `SCRIPT_PENDING` switches the next run to the header's routine for it.

## Editing

`mhfu.em.script.edit` changes a lifted program in place, `asm` lays it out, `check` vets it.

- **Nodes.** Builders (`insn`, `if_`, `switch`, `random`, `repeat`) make nodes; `insert`, `put`,
  `remove` and `replace` place them and give each an `n<k>` id. A node with no `pc` is new code,
  and a native block with anything new inside is touched: only those are checked.
- **Lists.** A list index is a byte, so a list holds 256 entries at most. New code names an entry
  by label (`Sym`); `resolve` swaps each for its index in the list of every header that reaches
  the routine, appends it where it is missing, and refuses a label two headers would index
  differently. `fork_table` gives one header its own copy of a shared list, `add_deep` a header
  a new `call_deep` list.
- **Layout.** A native item that fits up to the next one stays; a routine that does not first
  gives up the zero bytes at the end of its tail. The rest and the new items follow the native
  region, 8-aligned, a header that grew last, and the vacated span is zero. `Image.origin` keeps
  mapping native addresses to their new offsets. The region ends with the last header: the float
  parameters behind it are not carried. Bytes no item owns inside it, such as the floats behind an
  inner header, are `blob`s that stay where they were.
- **The game's scan.** It steps by `SCRIPT_SKIP`, not by `OPCODES`' lengths: a `switch_enemy_var`
  opener lands on its first case value, an opcode with no case does not move at all, and the scan
  gives up at the 0x95 sentinel and after 1000 steps between two markers (natively 794 at most).
  A `random_shifted` shares its markers with `random`, so inside one it confuses the outer scan
  (em83 does natively); a `repeat` keeps one loop return, so repeats do not nest.
- **Checks.** New code must keep random weights at 32, ordered cases ascending, repeat counts
  at 1 or more, and every list index inside its list; a new 0x94 block, a `random_shifted` inside
  a `random` and a repeat inside a repeat are refused; the assembled bytes must scan as the tree
  says and decode back to it. Each finding is an error, except an index at an empty slot, which
  is a warning.

## Source text

`mhfu script EM --source` writes a row as text, `--check FILE` and `--build FILE -o OUT` read an
edited one back (`mhfu.em.script.parse`). Lines and indentation, one item per top-level line:

| Line | Item |
|---|---|
| `row N at ADDR end ADDR sha1 HEX` | First line: it must be the row and game build the text is edited against |
| `routine NAME` | Its instructions indented under it, then `tail 00 01` and `: 00 01` rows |
| `table NAME: a b - extern ADDR` | A list: item names, `-` an empty slot, `extern` a word outside the program |
| `data NAME` | Rows `label: 00 01` and `: 00 01`; a label names the offset of its row |
| `blob` | Rows `: 00 01`, bytes kept as they are |
| `header NAME variants 0-4,6` | One `FIELD entry` line per field, in order, named as in the field table (then `deep0`, `deep1`, ...) |

- An instruction is its name and operands by position, decimal or `0x`; `end` takes a kind by
  name or number. An operand of a list index may be `@NAME` instead: the routine or list entry a
  new line names, which `--check` turns into its slot.
- A block's opener has its contents indented under it. Its arms (`else`, `case N`, `default`,
  `branch WEIGHT`, `bucket`) sit level with the opener, in the shape's order, and a dedent closes
  the block: the closing marker, the count and the marker numbers are implied.
- `#` starts a comment and blank lines are ignored; any consistent indentation will do.
- Unchanged code keeps its native place: items by name, routine nodes by aligning the lines
  against the row's own. What the text adds or changes is new code, which `check` holds to the
  rules; code moved elsewhere is new too.
- A syntax error names its line. `--build` refuses while a finding is an error; `mhfu brain
  push|build|deploy EM FILE` check the same way first.
- `--source` is a game listing: keep it out of repositories.

## Container

An assembled script travels as an `MHAI` container: the image, and the maps that carry a native
address into it and back. Little-endian; every offset counts from the container's first byte; each
section is 8-aligned.

| Offset | Field |
|---|---|
| 0x00 | `char[4]` `MHAI` |
| 0x04 | `u16` version, 1 |
| 0x06 | `u16` species row |
| 0x08 | `u32` id: the first 4 bytes of the SHA-1 of the image written for base 0, read as little-endian |
| 0x0C | `u32` load: the address the image's pointer words are written for; 0 in a stick container, whose loader adds its own |
| 0x10 | `u32` image offset, `u32` size |
| 0x18 | `u32` reloc offset, count: each the image offset of a pointer word that moves with the image |
| 0x20 | `u32` patch offset, count: each `{u32 native header address, u32 image offset}` |
| 0x28 | `u32` origin offset, count: each `{u32 native address, u32 image offset}`, sorted: the start of every instruction and marker, and the address after each |
| 0x30 | `u32` item offset, count: each `{u32 native address, u32 image offset, u32 size, u32 kind}`, by native address; kind 0 routine, 1 table, 2 data, 3 header, 4 blob |
| 0x38 | 8 zero bytes |

The image starts at 0x40; relocs, patches, origin and items follow in that order. A stick container
(the framework loads it from the memory stick) has no origin and no items: their counts are 0.

A native address maps into the image by an origin entry or a patch exactly, else by its offset inside a
table or data item; anything else, such as a routine's tail, has no place. The inverse takes the lowest
native address of an offset.

`mhfu brain push` writes the container at `SCRIPT_PUSH` (960 KB of PPSSPP's 64 MB mode), points the
row's `SCRIPT_HEADERS` words at it and moves each live monster's script words across the maps,
restarting at base routine 0 the monster with a word that has no place. All of it is written in one
stop where no monster is inside its script: at `SCRIPT_TICK` of a live monster of the species, else
with the CPU paused. It lasts until the next quest load re-reads the overlay. `mhfu brain restore`
moves the words and monsters back across the same maps and clears the container.

The framework loads a stick container: `mhfu brain deploy` writes `brain_row_NN.mhai` to
`PLUGINS/mhfu_framework/brain` and a mod, `mods/brain_row_NN.lua`, that calls
`mhfu.brain_install(path)`. It stages the file in extra RAM, adds the image's address to every reloc
word, and points each word of the row's `SCRIPT_HEADERS` that holds a patch's native address at the
image: at install, and after every chunk of the overlay that `SCRIPT_HEADERS` lies in, since each
quest load re-reads it. A monster that starts its script later runs the container's; one already
running keeps the game's. Extra RAM is PPSSPP's, so a real PSP installs nothing.
