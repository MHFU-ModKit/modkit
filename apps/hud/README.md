# mhfu-hud

A live HUD for MHFU in PPSSPP: it attaches over the debugger and draws the game state in a
pygame window. The QUEST_PREP tab writes game memory (its staged size, species and HP edits);
LIVE and AI_MOD only read.

```bash
uv run hud                      # next to a running PPSSPP
uv run hud --shot out/          # one PNG per tab, no window
```

Artwork is not shipped: point `--assets DIR` (or `MHFU_HUD_ASSETS`) at a folder of
`monsters/`, `items/`, `maps/` and `backgrounds/`; without one the HUD draws placeholders.
