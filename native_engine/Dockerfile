# Build the UNMODIFIED, exact published upstream CLI, not an IntiHA Rust server.
# --locked uses the published package's Cargo.lock. CI must compile both arches.
FROM rust:1.99.1-slim-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config libdbus-1-dev libudev-dev libusb-1.0-0-dev libssl-dev git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN cargo install intiface-engine --version '=4.1.0' --locked --root /opt/intiface
RUN /opt/intiface/bin/intiface-engine --version
# Licence files come from the exact downloaded Cargo sources, not a directory
# outside the Supervisor's native_engine/ build context.
RUN set -eu; mkdir -p /opt/notices; \
    printf '%s\n' 'Unmodified upstream intiface-engine 4.1.0. Cargo registry dependency notices.' \
      > /opt/notices/README.txt; \
    test -d /usr/local/cargo/registry/src; \
    for crate in /usr/local/cargo/registry/src/*/*; do \
      [ -d "$crate" ] || continue; \
      for f in "$crate"/LICENSE* "$crate"/COPYING* "$crate"/NOTICE* "$crate"/UNLICENSE*; do \
        [ -f "$f" ] || continue; \
        mkdir -p "/opt/notices/$(basename "$crate")"; \
        cp "$f" "/opt/notices/$(basename "$crate")/"; \
      done; \
    done

FROM debian:bookworm-slim
ARG BUILD_VERSION=0.3.0b1
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="${BUILD_ARCH}" \
      org.opencontainers.image.source="https://github.com/s2n2/IntiHA" \
      org.opencontainers.image.title="IntiHA Native Engine" \
      org.opencontainers.image.description="Unmodified Intiface Engine 4.1.0, native Bluetooth" \
      org.opencontainers.image.licenses="BSD-3-Clause"
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates python3 libdbus-1-3 libudev1 libusb-1.0-0 libssl3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/intiface/bin/intiface-engine /usr/local/bin/intiface-engine
COPY --from=build /opt/notices/ /usr/share/licenses/intiface/
COPY run.py healthcheck.py /app/
ENV PYTHONUNBUFFERED=1 HOME=/data XDG_CONFIG_HOME=/data/config \
    DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket
EXPOSE 12347 12348
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python3 /app/healthcheck.py
ENTRYPOINT ["python3", "/app/run.py"]
