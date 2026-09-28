#!/usr/bin/env bash
# Write the .env compose reads: the ISO directory, PPSSPP's user directory, their owner, and the
# GPU render node when the host has one (Linux only; otherwise PPSSPP renders in software).
#   [WEB_BIND=0.0.0.0] docker/setup.sh ISO_DIR PPSSPP_HOME
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ $# -eq 2 ] || { sed -n '2,4p' "$0" >&2; exit 2; }
ISO_DIR="$(cd "$1" && pwd)"
mkdir -p "$2/ppsspp/PSP/SYSTEM" "$2/ppsspp/PSP/PPSSPP_STATE"
PPSSPP_HOME="$(cd "$2" && pwd)"

# a discrete GPU over an integrated one: the last node whose driver is not i915
node=""
for n in /dev/dri/renderD*; do
    [ -e "$n" ] || continue
    driver="$(basename "$(readlink -f "/sys/class/drm/${n##*/}/device/driver")")"
    if [ "$driver" = i915 ]; then node="${node:-$n}"; else node="$n"; fi
done

{
    echo "ISO_DIR=$ISO_DIR"
    echo "PPSSPP_HOME=$PPSSPP_HOME"
    echo "PPSSPP_UID=$(id -u)"
    echo "PPSSPP_GID=$(id -g)"
    echo "WEB_BIND=${WEB_BIND:-127.0.0.1}"
    if [ -n "$node" ]; then
        echo "COMPOSE_FILE=compose.yaml:compose.gpu.yaml"
        echo "RENDER_NODE=$node"
        echo "RENDER_GID=$(stat -c %g "$node")"
    fi
} > "$HERE/.env"
cat "$HERE/.env"
[ -n "$node" ] || echo "no GPU render node: PPSSPP renders in software"
