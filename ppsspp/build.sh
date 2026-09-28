#!/usr/bin/env bash
# Build PPSSPP at the pinned commit with patches/ applied, on macOS or Linux.
#   ppsspp/build.sh [extra cmake args]
# PPSSPP_BUILD (default ~/.cache/modkit/ppsspp) holds the checkout and the build, outside the repo
# so tools that walk it skip 2.4 GB of PPSSPP; JOBS caps the compile.
set -euo pipefail

COMMIT=fa50bb1976065c4f8b1b47af227d367fe9771555  # v1.20.4
REPO=https://github.com/hrydgard/ppsspp.git

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="${PPSSPP_BUILD:-${XDG_CACHE_HOME:-$HOME/.cache}/modkit/ppsspp}"
SRC="$BUILD/src"
OUT="$SRC/build"  # inside the tree: the macOS signing step finds its entitlements from there

if [ "$(git -C "$SRC" rev-parse HEAD 2>/dev/null)" != "$COMMIT" ]; then
    [ -d "$SRC/.git" ] || git init -q "$SRC"
    git -C "$SRC" fetch -q --depth 1 "$REPO" "$COMMIT"
    git -C "$SRC" checkout -q --force FETCH_HEAD
    git -C "$SRC" clean -q -ffdx
    git -C "$SRC" submodule -q update --init --recursive --force --depth 1 --jobs 8
    rm -rf "$OUT" "$BUILD/patches.stamp"
fi

# re-apply only when the patches changed, so an unchanged tree keeps its mtimes
stamp="$(cat "$HERE"/patches/*.patch | git hash-object --stdin)"
if [ "$(cat "$BUILD/patches.stamp" 2>/dev/null)" != "$stamp" ]; then
    git -C "$SRC" reset -q --hard
    git -C "$SRC" apply "$HERE"/patches/*.patch
    echo "$stamp" > "$BUILD/patches.stamp"
fi

args=(-DCMAKE_BUILD_TYPE=Release -DUSE_DISCORD=OFF -DUNITTEST=OFF -DHEADLESS=OFF)
if [ "$(uname)" = Linux ]; then
    args+=(-DUSING_QT_UI=OFF -DUSE_SYSTEM_LIBSDL2=ON -DUSE_SYSTEM_FFMPEG=OFF -DUSE_WAYLAND_WSI=OFF)
fi
# CMake 4 refuses the cmake_minimum_required of some bundled libraries
cmake -S "$SRC" -B "$OUT" -G Ninja -DCMAKE_POLICY_VERSION_MINIMUM=3.5 "${args[@]}" "$@"
cmake --build "$OUT" --target PPSSPPSDL ${JOBS:+--parallel "$JOBS"}

if [ -d "$OUT/PPSSPPSDL.app" ]; then
    echo "$OUT/PPSSPPSDL.app/Contents/MacOS/PPSSPPSDL"
else
    echo "$OUT/PPSSPPSDL"
fi
