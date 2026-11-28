# modkit-ci

The CLI behind the repo's CI: `uv run modkit-ci --help`. The module list is
`release-please-config.json` (key `x-modkit` per package); everything here reads it through
`modkit_ci.registry`. Never published.

## Example

```console
$ uv run modkit-ci registry --json
$ uv run modkit-ci affected --base origin/main --output json
```

## Licence

MIT.
