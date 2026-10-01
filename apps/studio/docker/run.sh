#!/usr/bin/env bash
# Runs a command in the studio's GL container with this repository mounted at /repo.
#   apps/studio/docker/run.sh                                   # the studio's tests
#   apps/studio/docker/run.sh uv run studio render selftest -o out/
# Output paths must lie inside the repository: anything else is written inside the container
# and is gone when it exits. MHFU_DATA and MHP3RD_DATA, when set, are mounted read-only.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
image=${STUDIO_IMAGE:-mhfu-studio-gl}

docker build --quiet --platform linux/amd64 -t "$image" "$here" >/dev/null

flags=(--rm --platform linux/amd64 --user "$(id -u):$(id -g)"
       -v "$repo:/repo" -v mhfu-studio-venv:/venv -v mhfu-studio-uv:/cache/uv)
for var in MHFU_DATA MHP3RD_DATA; do
    if [ -n "${!var:-}" ]; then
        # mount the whole extraction: the variable may name it or its data_files
        root=${!var%/} sub=
        if [ "$(basename "$root")" = data_files ]; then root=$(dirname "$root") sub=/data_files; fi
        mount=/data/$(printf %s "$var" | tr "[:upper:]" "[:lower:]")
        flags+=(-v "$root:$mount:ro" -e "$var=$mount$sub")
    fi
done
exec docker run "${flags[@]}" "$image" "$@"
