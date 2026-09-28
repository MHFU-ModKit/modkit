# PPSSPP on a virtual display, watchable in a browser over noVNC, its debugger on port 12345.
# The build stage runs the same build.sh as a native build; the build context is ppsspp/.
ARG DEBIAN=trixie

FROM debian:${DEBIAN} AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake ninja-build git ca-certificates pkg-config python3 \
      libsdl2-dev libgl-dev libglu1-mesa-dev libvulkan-dev libpng-dev zlib1g-dev libzip-dev \
    && rm -rf /var/lib/apt/lists/*
COPY build.sh /ppsspp/
COPY patches /ppsspp/patches
# caps parallel compiles where memory is short, such as a Docker Desktop VM
ARG JOBS
ENV PPSSPP_BUILD=/ppsspp/build
# the checkout and the build live in a cache, so a rebuild compiles only what changed
RUN --mount=type=cache,target=/ppsspp/build \
    /ppsspp/build.sh \
    && mkdir /opt/ppsspp \
    && cp -r /ppsspp/build/src/build/PPSSPPSDL /ppsspp/build/src/build/assets /opt/ppsspp/

FROM debian:${DEBIAN}-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb x11vnc novnc websockify x11-utils x11-apps netpbm \
      libsdl2-2.0-0 libgl1 libglu1-mesa libegl1 libgl1-mesa-dri libglx-mesa0 \
      libvulkan1 mesa-vulkan-drivers \
      libpng16-16t64 zlib1g libzip5 \
      ca-certificates procps tini \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html
COPY --from=build /opt/ppsspp /opt/ppsspp
# a library the build linked but this stage lacks fails here, not at the first launch
RUN ! ldd /opt/ppsspp/PPSSPPSDL | grep "not found"

COPY docker/entrypoint.sh docker/ppsspp-ctl /usr/local/bin/
RUN mkdir -p /config /iso /run/ppsspp && chmod 777 /config /run/ppsspp

ENV DISPLAY=:99 \
    SCREEN_GEOMETRY=960x544x24 \
    HOME=/config \
    XDG_CONFIG_HOME=/config \
    SDL_AUDIODRIVER=dummy

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
