# Brain file

The edits to a big monster's brain, as TOML: one file per em overlay, `brains/emNN.toml`, for the
species rows the overlay serves. A file holds only what differs from the game's own script, never
the script: no listing, no bytes. Read and written by `mhfu.em.script.brainfile`, built by
`mhfu.em.script.build` into the container `docs/formats/ai_script.md` describes. An empty file
builds the game's bytes again, container for container (all 42 rows).

```toml
schema = 1
overlay = 75
label = "Tigrex"

[pairs]
"0,2" = "alert hub"

[vars]
4 = "in_hunt_area"

[ranges]
1 = "melee"

[row.75]
build = "f2fe5a46f04a772d1de08424052b88bf71b73ee6"

[row.75.node."75:09D54C05".args]      # the first enter, (0, 1), becomes (0, 2)
sub = 2

[row.75.node."75:09D54C0E".args]      # two random branches swap weights: they keep adding up to 32
weight = 10

[row.75.node."75:09D54C23".args]
weight = 8

[row.75.insert.u1]                    # a new node before the counter check
before = "75:09D54C44"
code = """
if_enraged
  enter 3 0 0
"""
```

## Top level

| Key | Holds |
|---|---|
| `schema` | 1 |
| `overlay` | The em overlay: one of `files.EM_SPECIES` |
| `label` | A name to show, optional |
| `pairs` | Display names of move pairs, `"main,sub" = "name"`: for graphs and findings, no bytes depend on them |
| `vars` | Display names of script variables 1 to 9 (1 to 4 are the monster's own bytes, 5 to 9 call the species overlay; which are free: `mhfu script N --vars`) |
| `ranges` | Display names of distance ranges: 1 to 4 within that limit of the species' range set, 5 past them or with no target |
| `row.N` | The edits to species row N, one edit set (below) |

No other key is allowed. A row is in one section only: its own, or the `also` of one.

## An edit set

| Key | Holds |
|---|---|
| `build` | The SHA-1 of the row's lifted region the edits are for. Another build refuses them (`DOC_BUILD`) |
| `also` | Other rows the edits apply to as they are. Only rows that lift the same code at the same places (`brainfile.twins`): rows that merely share a SHA-1 do not (em01's rows 1, 37 and 42 and its rows 11, 41 and 49 read as one region and lift as two) |
| `node."ID"` | What is done to a native node, block opener or arm: `op`, `args`, `delete`, `bypass`, `move`, `arms` |
| `insert.uK` | New code and where it goes |
| `routine.NAME` | The code of a new routine, which code reaches by label (`@NAME`) |
| `list."HEADER.LIST"` | Changes to a header's list (`h0.call`, `h1.int_0x20`, `h0.deep2`): `fork`, `set.N`, `append` |
| `name`, `note` | A display name for a native routine, a note on a node, an insert or a routine |

Native nodes are named by the lift's ids, `{row}:{pc:08X}`, the address `mhfu script N` lists
the instruction at (a block's arm has the address of its marker). New code is named by `apply`, in
source order: `uK.0`, `uK.1`, ... for insert `uK`, `NAME.0`, ... in a new routine. An insert can
anchor in another insert's code: `after = "u1.2"`.

- `op`: another opcode for the node, in place: a block stays the same shape and keeps its arms and
  bodies, operands carry over by name (a missing one goes in `args`).
- `args`: the operands that differ from the game's, by name. `"@name"` is a list entry by label.
- `delete`: the node, with all that is in it, is gone.
- `bypass`: a block is replaced by its `then`, its `else`, the arm with this id or `none`.
- `move`: `before`, `after` or `into` another node or a list (`into` goes to the end, with `at = 0`
  to the start); the anchor may be new code (`after = "u1.0"`), so a native node goes into an
  inserted block by reference and the file never holds the game's code.
- `arms`: a block's native arms in the order wanted, by id.
- `insert`: `before` or `after` a node (an arm: the start or end of its body), or `into` a list (a
  routine, a block's head or an arm) at `at`, by default the end. With `arm = { sub, args }` the
  code is the body of a new arm of the block named.
- `list`: `fork` gives the header its own copy of a shared list first; `set` fills slots by index
  (`"-"` is empty, `"extern 0x..."` a word outside the program, else an item name); `append`
  adds entries.

`apply` runs the edits in a fixed order: build check, new routines, `op`, `args`, `arms`, then
moves and inserts in one pass in which each waits for the code it anchors in, `bypass`, `delete`,
lists, labels. A bad anchor or a cycle is a finding, not a failure (`DOC_ANCHOR`,
`DOC_CONFLICT`); see `docs/formats/ai_script.md`, "Editing".

## Code

Inserted code is source text, the lines `mhfu script --source` writes: a name and its operands by
position, a block's contents indented under it, its arms level with it, a dedent closing it. It
has no ids, because `apply` names it. A comment (`#`) reads, and is dropped when the file is saved
again; so is an `origin` that an edit set had on its nodes.

## Normal form

`brainfile.save` writes tables and keys sorted (inserts by number), `also` sorted, `build` in
lower case, code as multi-line strings, and nothing that changes nothing: an edit with nothing in
it, a blank name or note, a section with no edits. Reading a file in normal form and saving it
gives its bytes back. The file is written through a temp file, so a reader never sees half of it.

## Building

`build.compile(bf, game, row)` takes a loaded file, lifts the row (or takes the lift the caller
keeps), applies the file's edits to it, assembles the program, runs the checks and packs the
container; the CLI and the studio both call it. It returns a `Built`: the lift, the applied
program, the image, the findings (apply's, then the checks', then `PAIR_UNKNOWN` when species intel
is given), the run budget, and the container and its id. A finding with level `error` leaves no
container. A row the file has no edits for builds the game's own bytes.

```bash
uv run mhfu brain build  --doc brains/em75.toml -o row.mhai     # the container alone
uv run mhfu brain deploy --doc brains/em75.toml                 # container and mod, on the stick
uv run mhfu brain push   --doc brains/em75.toml                 # into the running game
uv run mhfu script 75    --doc brains/em75.toml                 # findings, id and the worst run
uv run mhfu script 75    --doc brains/em75.toml --source        # the program it builds, as text
```

Both take `--row N`, another row the overlay serves; `mhfu brain` also `--intel FILE`, a species
intel (`mhfu intel`), which adds `PAIR_UNKNOWN`. The mod `deploy` writes carries the container's
id, so a changed build is a changed mod, which the framework's Lua host runs again.
