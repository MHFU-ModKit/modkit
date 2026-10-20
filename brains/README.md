# Brains

One TOML file per big-monster em overlay, `emNN.toml` (`em75.toml`, `em01.toml`): the edits to the
native AI scripts of the species rows the overlay serves, as data. A file holds only what differs
from the game's own script, never the script, so it carries no game data; an empty one builds the
game's bytes again. The format is [`docs/formats/brain_file.md`](../docs/formats/brain_file.md),
read and written by `mhfu.em.script.brainfile`.

| File | Species |
|---|---|
| [`em75.toml`](em75.toml) | Tigrex (rows 75, 76, 81, 88): move names, no edits yet |

```bash
uv run mhfu brain build  --doc brains/em75.toml -o row.mhai     # the container alone
uv run mhfu brain deploy --doc brains/em75.toml                 # container and mod, on the stick
uv run mhfu brain push   --doc brains/em75.toml                 # into the running game
uv run mhfu script 75    --doc brains/em75.toml                 # findings, id and worst run
```

Each prints the findings, one line each, and refuses to build while one is an error. A file is
written in normal form by `brainfile.save`: sorted, nothing that changes nothing, and no comments.
Edits name native nodes by the ids of a lift of the game's script, and a row's `build` SHA-1 gates
them, so a file is only for the game build it was made against.
