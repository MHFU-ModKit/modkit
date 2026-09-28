#!/bin/bash
# Bring up the virtual display, VNC and noVNC, then idle; `ppsspp-ctl start` runs the emulator.
set -euo pipefail

n="${DISPLAY#:}"
LOGS=/run/ppsspp
mkdir -p "$LOGS"

# /tmp survives `docker restart`, and Xvfb refuses to start over its old lock
rm -f "/tmp/.X$n-lock" "/tmp/.X11-unix/X$n"

Xvfb "$DISPLAY" -screen 0 "${SCREEN_GEOMETRY:-960x544x24}" -nolisten tcp -ac +extension GLX \
    +render -noreset >"$LOGS/xvfb.log" 2>&1 &
for _ in $(seq 100); do
    xdpyinfo >/dev/null 2>&1 && break
    sleep 0.1
done
xdpyinfo >/dev/null 2>&1 || { cat "$LOGS/xvfb.log"; exit 1; }

x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport 5900 -noxdamage -repeat -quiet \
    >"$LOGS/x11vnc.log" 2>&1 &
websockify --web=/usr/share/novnc 6080 localhost:5900 >"$LOGS/websockify.log" 2>&1 &

echo "display $DISPLAY ready, GPU backend ${GPU_BACKEND:-software}; start PPSSPP with ppsspp-ctl start"
exec tail -f /dev/null
