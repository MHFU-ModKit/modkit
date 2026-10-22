# MHP3rd AI script

A monster's brain in MHP3rd is bytecode, as in MHFU, with its own opcode numbering and no gate
operand. The script picks what the monster does next; the moves themselves are code in the
overlay. Read and written by `mhfu_port.brain` (`mhfu-port brain ID`): the 71 ids that have a
script lift into trees, and each routine encodes back to the game's bytes. It is static
research: nothing here has run on a PSP or in PPSSPP. A manifest's `[brain]` carries one routine
into an MHFU host's script ([below](#onto-an-mhfu-brain)). The MHFU script is
[ai_script.md](ai_script.md).

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
end as 0xFF (`end_alias`). 0x70 to 0xFE do not exist. A name with an offset in it (`if_flag_3a8`)
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

## Onto an MHFU brain

A manifest's `[brain]` names a donor routine (`entry`) and a routine of the host species' script
(`splice`). `mhfu-port splice` (`mhfu_port.brain.splice`) reads both games and makes the host
routine's body the donor's, as an edit set that `mhfu brain push|deploy` carries on. The host
keeps everything else: its roaming, sections, target pick and interrupts.

- The host routine keeps its leading plain instructions and its `end`. The donor's `entry` is
  pasted after them without its `end`, and so is the base routine it ends on with `call_base`.
- A `call n` in pasted code runs a new routine `p3_<name>` that ends `end return`. A `call` from
  there is inlined, so no call nests deeper than one level.
- A donor op maps as below; any other op is a finding.

| Donor | Host |
|---|---|
| `enter m s` | `enter M S 0`, the pair from `[brain.pairs]`; an own move there: `enter 16 k 0`; a list there: one `enter` per item |
| `random` | `random`: each weight `w` becomes `w * 32 / 100` by largest remainder, at least 1; a zero arm goes. Too many live arms for MHFU's 13 branches (the padding one counts): an outer `random` over groups of at most 12 and an inner `random` per group, all reading one draw (`P3_SPLIT`, below) |
| `switch_range` | `switch_range`: case `k` is bucket `k + 1`, the default the last, a missing case copies the default |
| `switch_mode` | `if_mode2` for mode 1, the default as its else |
| `if_enraged` | `if_enraged` |
| `if_facing ang` | `if_pursuit_off_facing` at `ang * 360 / 256` degrees with the arms swapped; past 127, `if_off_facing` |
| `switch_query arg` | `[brain] charge`: args 0 (charged) and 6 (cooldown running) stay a `switch_query`, which the framework answers, cases ascending and repeats dropped; any other arg, or no `charge`, is answer 0: the case 0 arm or else the default, the rest dropped (`P3_FOLDED`, naming them) |
| `switch_dist` | `switch_dist`: the cases are hundreds in both; a case at or under an earlier one, which no distance reaches, goes |
| `switch_hp` | `switch_hp` with each case one higher: MHP3rd's matches the integer HP percent at or below it, MHFU's below it |
| `if_target_side` | `if_target_in_arc 0 127`: the target's bearing minus the facing in the first half turn (bit 15 clear), arms as they are |
| `switch_global_540` | its case 2, the only value the scripts test (the default without one): the host has no such word |
| `if_target_state`, `if_tired`, `if_stamina_low`, `if_target_set` | no host state: `[brain.when]` picks the arm; with `[brain] fatigue`, `if_tired` and `if_stamina_low 0` are `if_gauge_a_low` ([Fatigue](#fatigue)) |
| `if_object_near` | false |
| `set_target 0 0 0` | dropped: the host picked its target before |
| `set_latch`, `switch_latch` | folded: case 0 when something between them yields, else the default |
| `switch_prev_call` | `if_var` on `[brain] history` (unset: the host's first free variable, `mhfu script N --vars`), which each called routine sets to its own index on every path, after the read where it reads |
| map and area ops | none: the host's roaming owns them |

The history is one variable, MHP3rd's two ids: the variable holds the newest call, and a read before
the routine's own note sees the call before it. The note goes first on each path, but the read's
arms follow the read, wherever it is (after code, in a branch, in its own default). That is right
while no call precedes the read in the routine, as at every site of the Zinogre's script: a
call before it would be the newest (`P3_HISTORY` warns), and two reads in a row, the second seeing
the first's note, are an error.

A random that can follow another draw in the same run gets an `rng_step2` first, as the host's
own picks do. MHP3rd draws a fresh number per pick; MHFU's `random` reads the entity's counter and
only a step moves it, by 1, so that pick reads the next value, not a fresh one (`CORRELATED_DRAW`
says so; the draws after another draw stay, as in MHFU's own scripts).

A split `random` is one draw read twice: the inner random is the first node of its outer arm, gets
no step, and reads the outer's draw. The weights are rounded once over all the live arms, arm `k`
owning the slots `c_k` to `c_k + s_k` of the 32. A group's outer weight is its arms' slot sum, so
it owns the band `lo` to `hi`; its inner weights are its arms' `s_k`, the `lo` slots below the band
added to the first arm and the `32 - hi` from its end to the last (they never occur). Every value
then takes the arm one random over all of them would. A group of one arm is its body.

Findings are errors (`P3_PAIR_UNMAPPED`, `P3_OWN_MISSING`, `P3_UNSUPPORTED`,
`P3_HOST_OP`, `P3_UNDECIDED`, `P3_LATCH`, `P3_HISTORY`, `P3_WIDE`, `P3_ROUTINE`, `P3_FATIGUE_HOST`), which stop a
build, warnings (`P3_FOLDED`, `P3_APPROX`, `P3_PAIR_UNUSED` (not for a pair the charge table names or whose
move another pair plays), `P3_WHEN`, `P3_WHEN_IGNORED`, a
`P3_HISTORY` for a call before the read) and notes (`P3_DROPPED`, `P3_WEIGHT`, `P3_SPLIT`, an `info`
`P3_FOLDED`).

A host pair needs what the host's own script does around it. em75 enters (1, 0) only after a
destination is set (`set_target kind=2`, a `walk_next` step) and, mostly, a turn (1, 2) toward it;
(1, 0) then walks there. Entered without one, it waits for good, and the script with it. So map a
donor pair to a host pair only where the host enters that pair the same way (`mhfu script <row>`
shows each site); a donor walk is derived (`"own"`).

### Own moves

A `[brain.pairs]` value may name an own move of the port's `[moves]` (`"7,13" = "slam"`) instead of
a host pair; `"own"` stands for the move the build derives from the donor (below), `p3_7_13`. The
donor's `enter 7 13` becomes `enter 16 k 0`: main 16 is
`OWN_MOVE_MAIN`, which no species uses, and `k` the move's registry slot. em_vhook plays move `k`
in place of the pair and the script goes on after the `enter` when it ends, as for a host pair.

A value may be a list, played in order, each item one `enter` that yields: `"7,23" = [[1, 2], "own"]`
is the host's turn (1, 2), then the move. An item is a host pair, an own move's name or `"own"`; the
build charges the donor pair's stamina to the last item.

A pair can also name the move another pair derives, `"7,23" = [[1, 2], "p3_7_0"]` where `"7,0" =
"own"`: a turn wrapper (a donor pair that turns toward the target, then enters another pair)
plays the move of the pair it leads to rather than a duplicate of its own handler, and twin pairs
(same clips, attacks and steer) share one move, which saves slots and turn keys. The name must be
`own_name` of a pair mapped to `"own"` (`Brain.derived`), and that pair is not reported unused
while another pair plays its move (`Brain.shared`).

The slots are `mhfu_port.moves.slots`: the own moves by sorted name, numbered from 0, at most
`EM_MOVES.MOVES.count`. The moves module `mhfu-port` writes carries each one as `slot = k` and
`mhfu_port.lua` registers by it, so the number in the script, the module and the registry is one.
`mhfu-port splice` passes the slots; `-o` names the pair `(16, k)` after the move in the brain
file, so the Brain tab reads `Move (own k) slam`.

### Derived own moves

`"own"` has the build derive the move from both games' files each time, in `own.expand`
(`mhfu_port.brain.own`): the moves module, `splice` and the slots read the manifest it returns,
and nothing derived is stored. A move of the same name in `[moves]` is used instead.
`mhfu-port own M` lists them.

The donor's handler for the pair is run an AI frame at a time on the clip cursor its own tests
ask about (`mhfu_port.p3hits`), which gives a chain of steps, `p3_7_14`, `p3_7_14_2`, ..., each a
clip that hands to the next by `after`:

- **Clips.** The default path. A branch on something the build cannot know (a bearing, a counter,
  a wait) is tried each way and the path that spawns most attacks and plays fewest clips is taken;
  the others are named in `P3_OWN_BRANCH`. A clip the donor starts past its first frame gets that
  `start`; one the handler leaves at a frame it tests gets that frame as `stop`. Both are clip
  frames, so they hold at any blend and cursor rate (a blend holds the cursor 2 to 5 AI frames,
  enraged it runs 2.8 a frame). Only a step that ended on no frame of its clip (a pair, a loop,
  a test the cursor never changed) gets the `length`, in AI frames, the donor played it for.
- **Hand-over.** A handler that ends by entering another pair (not a rest pair) hands over to it,
  as MHP3rd does: the chain goes on into that pair's moves, `p3_7_12` into `p3_7_156`. The pair
  plays the `[brain.pairs]` move that names it (`"7,5" = "p3_7_4"`), else the one `own_name`
  gives it, derived the same way and so following its own hand-over; an authored move of that
  name is used as it is. Each pair is derived once, and a hand-over that would close a ring is cut
  where it closes. A hand-over to a pair the manifest plays as a host pair or a list, or the port
  cannot play (no clip, a clip the donor lacks, a walk that gave up), is not followed:
  `P3_OWN_NEXT`.
- **Sharing.** After every pair is derived, moves equal in every field but `label` become one
  (their `after` compared as the merge renames it, so equal tails make their predecessors equal):
  `p3_7_237`, which the donor dispatches as `(7, 0)`, is `p3_7_0`. The one kept is the one
  `[brain.pairs]`, the behaviour graph, an effect or an authored move's `after` names, else the
  first derived; two named moves stay apart.
- **Attacks.** A record's START and LEN against the cursor at the spawn give the frames; a node
  that outlives a clip change is split per clip. The record's volume set is matched to the host
  record whose volumes overlap it most on the port's rig (the same kind first, then power); below
  0.25 there is no match and the record takes a spare host record (below). `[brain.attacks]
  "26" = 40` names the host record for donor record 26; the port may define it with `[[attack]]`
  and `[[hitbox]]`.
- **Turn.** A `hunter` steer with the donor's rate and the cursor window it turned in
  (`steer.window`). What the donor does by code, not by its clips (a slide, a curve), is not carried.

| Finding | Level | What |
|---|---|---|
| `P3_OWN_NOCLIPS`, `P3_OWN_CARRIER`, `P3_OWN_SLOTS` | error | no clip for the pair; no carrier for the host; more own moves than the framework holds |
| `P3_OWN_ATTACK` | warning | a donor record with no host record and no spare, or more attacks on a clip than the move player holds: left out |
| `P3_OWN_SPARE` | info | a donor record with no host record took a spare one: its id, set, volumes, power, kind, active frames |
| `P3_OWN_PARTIAL` | warning | the walk gave up, or a clip it could not resolve |
| `P3_OWN_BRANCH`, `P3_OWN_MOTION` | info | a branch taken one way; code movement |
| `P3_OWN_NEXT` | info | a hand-over not followed, and why |
| `P3_OWN_SPLIT`, `P3_OWN_GROUP`, `P3_OWN_ACTIVE` | info | a node split per clip; records that land one hit between them in the donor; a window longer than the host record's life |

### Spare attack records

A donor record that no host record matches gets a spare one in `own.expand`
(`mhfu_port.brain.spare`), so its attack is played instead of dropped. em75's record table holds
two more species' records (`hitbox.ID_OFFSETS`: 34-69 and 71-106, on sets 18-55), which a species-75
port never spawns. The build points one of them at one of those sets and adds both to the
*expanded* manifest as `[[attack]]` and `[[hitbox]]`; the saved manifest never has them, and the
own move's attack plays the spare's id.

- **Record.** Of the free ids, the one whose own kind is the donor's family (hit 2, weak 1, a node
  that hurts nobody 29: donor kind 0x34 or power 0), then the plainest (`+0x0C` and the angle
  zero, the family byte the host's records of that kind carry), then the lowest. Not an id the node
  code treats by id (5, 6, 21, 22, 24, 28, 31, 39, 40, 99 and 100, read from the overlay), not 70
  (blank), not one the manifest defines (`[[attack]]`, `[brain.attacks]`). The levers set are
  `power`, `kind`, `active` and `volume`; the other bytes (family, angle, tag, element) stay the
  game's own record's.
- **Power** is the donor's times the median of host power over donor power across this port's
  matched records (0.78, em75 against em058, where none matched); 0 for a node that hurts nobody.
- **Active frames** are the donor's LEN, grown to the longest window a move holds the node
  (a node the donor refreshes lives to the clip's end), at most 255.
- **Set.** The free set (not one of the species' own, not in `[[hitbox]]`) with the fewest records
  that holds the donor's volumes; records on one donor set share one host set. A set holds as many
  volumes as the host's does: a donor set that no free set holds gets no spare.
- **Volumes** are the donor's on the port's joints (`binding.joint_of`, the match's rig); a capsule
  across two bones becomes a capsule on the first with its far end in bind pose. Markers, bones off
  the rig and empty volumes are left out.

The tables reach the game through the studio's write plan, not the moves module: a port with a
`[brain]` gets them from `studio port hit` (`--deploy` for `<name>_hit.lua`, `port push`, Send and
Push to game), which expand the manifest first (`mhfu_studio.monster.runtime.expanded`). A move
that plays a spare record without its tables plays the species-76 or 88 record of that id.

### Fatigue

MHP3rd keeps a stamina per monster and its script asks `if_tired`; MHFU has none and its script
cannot count. `[brain] fatigue = true` has the framework's helper (`mhfu.em_fatigue`, armed by
`mhfu_port.lua` from the moves module's `fatigue` table) keep it and show "tired" as the host's
gauge A, which `if_gauge_a_low` tests. The build does two things (`mhfu_port.brain.fatigue`):

- **The splice** turns `if_tired`, and `if_stamina_low` at 0 (stamina 0 only precedes tired), into
  `if_gauge_a_low` with the arms as they are. Any other `if_stamina_low` stays a `[brain.when]`
  answer. The host must leave gauge A alone: `P3_FATIGUE_HOST` (error) where its species code calls
  `GAUGE_A_LATCH` or a routine other than `splice` tests or refills it. em75 does neither.
- **The moves module** gets `fatigue = {...}` from the donor species' row (descriptor `+0xA4`, its
  `+0x3C`): the maximum (1000), the two starting staminas the spawn rolls between (row 0 of the
  start rows: 400 or 600), the frames a tired spell lasts, what `recover` adds afterwards (the
  stamina after a spell), and the HP percent at or below which costs halve; no rage starts while
  tired (`calm`).

A donor pair's cost, which MHP3rd charges when the move ends, is charged when the host enters what
`[brain.pairs]` plays for it: a host pair into `pairs`, an own move into `own` by name (its head
only, one charge per donor pair, whatever its chain). Two donor pairs on one target with different
costs (a free one counts) charge the larger. The hub pair that carries own moves has no entry, so
the moves are not charged twice.

The donor's reactions cost by sub in three classes (15, 50, 200 for the Brute). em75's reaction
pairs, from the cases of its AI step (`EM75_REACTIONS`), are sorted into the same three: a
knockout where a trap's REACTION_CHECK code enters the pair, a fall where it plays three or more
clips, else a flinch, which is also what a pair that cannot be told costs. Other hosts have none:
hits cost nothing there.

| Finding | Level | What |
|---|---|---|
| `P3_FATIGUE_DONOR`, `P3_FATIGUE_PAIRS`, `P3_FATIGUE_HOST` | error | the species has no fatigue row; more pair costs than the helper holds; the host keeps gauge A itself |
| `P3_WHEN_IGNORED`, `P3_FATIGUE_HUB`, `P3_FATIGUE_REACTIONS` | warning | a `[brain.when]` answer the helper gives; a cost on a pair that carries own moves, dropped; no reaction table for the host |
| `P3_FATIGUE_SHARED`, `P3_FATIGUE_UNMAPPED`, `P3_FATIGUE_STARTS` | info | the larger of two costs on one target; donor pairs that cost stamina but have no host move; a spawn roll that is not a coin flip |

`mhfu-port splice` prints the table's size and findings with the splice's.

### Charge

MHP3rd's Zinogre fills a gauge by charge-ups, transforms at 100 and stays charged until hits wear
its durability out; its script asks `switch_query` for "charged" and "the cooldown runs". `[brain]
charge = true` has the framework's helper (`mhfu.em_charge`, armed by `mhfu_port.lua` from the
moves module's `charge` table) keep that state and answer the two queries; the splice keeps those
two `switch_query` args (`P3_FOLDED` for any other). The build reads the donor's overlay
(`mhfu_port.p3charge`) and `mhfu_port.brain.charge` maps it onto the port's moves:

- **Charge-ups**: the pairs whose handler calls the gauge's one writer. Each is run to its end
  from an empty gauge at an HP ratio in each of the helper's bands (`Walk.settle`: the handler's
  counter, the gauge and the float tests of the HP ratio against the species' constants answered
  from the code), and the gauge left is the gain, by HP at or above 95 %, 75 %, 40 % and below. For
  the Zinogre, (1,15) gives 25, 35, 40, 55 and (1,16) 40, 55, 65, 100: the repeats of one handler
  add up, and a charge-up near full tops itself up to 100. The gains are for the normal spawn (the
  variant byte clear). The helper adds a gain when the own move starts, the donor over the
  handler's keyframes.
- **The transform**: the pair whose handler sets the charged state, (1,4); the lowest of the
  pairs sharing that handler.
- **Chips**: a hit takes the row's step off the gauge while the monster is not charged and off the
  durability while it is charged, by rank; the lowest rank's row (5, with a cap of 10 per burst of
  hits, both tables). The helper chips each hit and has no cap; the donor chips in its
  flinch handler.
- **Cooldowns** are the constants the handlers store: 420 frames after a charge-up, 900 after a
  discharge.
- **Discharge**: em75's knockout reaction pairs (the class `brain.fatigue` sorts, where a trap's
  REACTION_CHECK code enters the pair), as MHP3rd's knockout and trap handler zeroes the charge.

A donor pair becomes the own move `[brain.pairs]` plays for it (the last item of a list). A
charge-up pair the map lacks is a warning, the transform's an error; a pair mapped for the table
that the script never enters is not reported unused. Two charge-ups on one move take the larger
gain in each band. The moves module's `charge = { ups = { name = { g95, g75, g40, glow } },
transform = name, chip_gauge, chip_durability, cooldown_up, cooldown_out, discharge = {{m, s}} }`
is written with the module (`mhfu-port build`, `inject`, the studio's deploy).

| Finding | Level | What |
|---|---|---|
| `P3_CHARGE_DONOR`, `P3_CHARGE_PAIRS`, `P3_CHARGE_UPS` | error | the species has no charge state or one the build cannot read; more discharge pairs or charge-up moves than the helper holds |
| `P3_CHARGE_UNMAPPED`, `P3_CHARGE_MOVE` | error for the transform, warning for a charge-up | no own move for the donor pair: no `[brain.pairs]` entry, a host pair, or a name `[moves]` lacks |
| `P3_CHARGE_REACTIONS` | warning | no reaction table for the host: nothing discharges |
| `P3_CHARGE_SHARED` | info | the larger gain of two charge-ups on one move |

## Checking

`mhfu-port brain --check` lifts every id and slot and compares each routine with its bytes. It
holds when no opcode is unknown, no byte after a routine is unaccounted for beyond zero padding,
every routine encodes to its own bytes, and the only problem is em003's: its list 1 routine 59
(ids 3 and 51, in each slot) has no end and runs into the next item. `p3ops.length` is checked
against the game's length table for all 0x70 opcodes in a game-data test.

## Open

- Which hit raises each interrupt list, and the format of list 2.
- The dispatch budget (a run stops after 1000 steps) and what it means for a ported script.
