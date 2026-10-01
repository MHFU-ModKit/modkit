# The studio's tests and `studio render` without a display or a GPU: Python 3.11, uv and Mesa's
# llvmpipe behind EGL, and Qt's offscreen platform. The repository is mounted at /repo, never
# copied (run.sh).
# Base images are pinned by digest so the renderer string, and with it the exact-hash goldens,
# only change when this file does.
FROM ghcr.io/astral-sh/uv:0.11.5@sha256:555ac94f9a22e656fc5f2ce5dfee13b04e94d099e46bb8dd3a73ec7263f2e484 AS uv

FROM --platform=linux/amd64 python:3.11-slim-trixie@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e
# Debian's archive as of one date, so Mesa (whose version is in the renderer string) is fixed too.
# Qt's own libraries come next, then its xcb platform and Xvfb for MHFU_UI_DISPLAY (run.sh).
ARG SNAPSHOT=20260920T000000Z
RUN apt-get update --snapshot "$SNAPSHOT" -o Acquire::Check-Valid-Until=false \
    && apt-get install -y --no-install-recommends --snapshot "$SNAPSHOT" \
      libegl1 libegl-mesa0 libgl1 libgl1-mesa-dri git ca-certificates \
      libdbus-1-3 libfontconfig1 libglib2.0-0t64 libxkbcommon0 \
      libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
      libxcb-shape0 libxcb-util1 libxcb-xkb1 libxkbcommon-x11-0 xauth xvfb \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /usr/local/bin/

# the environment and the uv cache live in volumes, apart from the host's .venv in the mount;
# world-writable so the container can run as the host user
RUN mkdir -p /venv /cache/uv && chmod 1777 /venv /cache/uv
ENV UV_PROJECT_ENVIRONMENT=/venv \
    UV_CACHE_DIR=/cache/uv \
    UV_PYTHON=/usr/local/bin/python3.11 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_FROZEN=1 \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    STUDIO_MOUNT=/repo
WORKDIR /repo
# with MHFU_UI_DISPLAY set the command runs on an Xvfb display, where Qt takes xcb and GL
ENTRYPOINT ["sh", "-c", "exec ${MHFU_UI_DISPLAY:+xvfb-run -a} \"$@\"", "sh"]
CMD ["uv", "run", "pytest", "apps/studio", "-q", "-p", "no:cacheprovider"]
