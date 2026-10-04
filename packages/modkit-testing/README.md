# modkit-testing

The pytest plugin every modkit package's tests share. It is installed with the workspace and
registers itself with pytest, so a test only names a fixture. It is never published.

## Install

In a clone of modkit, `uv sync`; nothing else to do.

## Example

```python
def test_tigrex(mhfu_data):  # skips unless MHFU_DATA is set
    data = (mhfu_data / "file_06185.bin").read_bytes()
```

| Fixture | Gives |
|---|---|
| `mhfu_data` | `MHFU_DATA` as a `Path`: MHFU's (ULES01213) extracted DATA.BIN files |
| `mhp3rd_data` | `MHP3RD_DATA` as a `Path`: MHP3rd's extracted DATA.BIN files |

Each variable names the `data_files` directory `mhp-formats extract` writes. Both fixtures are
session-scoped; an unset variable skips the test, one that names no directory fails it.

`modkit_testing.mips` encodes MIPS instructions for test code (`lui`, `lw`, `jal`, `beq`, ...,
with `hi`/`lo` for address halves), so a test can build the code it analyses.

## Status

The two game-data fixtures and the MIPS encoders; the packages' own fixtures live in their
`conftest.py`.

## Licence

MIT.
