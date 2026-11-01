# The app build context is this directory, including Cargo.lock and src/.
# Both stages use Debian Bookworm so the Rust binary's glibc ABI matches.
FROM rust:1.99.1-slim-bookworm AS builder

WORKDIR /build
RUN apt-get update \
    && apt-get install -y --no-install-recommends git pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked --bin intiha-engine

# Package the original dependency licences from the exact Cargo registry
# sources used for this build.  The Supervisor only sends engine/ as the
# Docker build context, and a separate licenses/ directory may be absent.
RUN set -eu; \
    registry="${CARGO_HOME:-/usr/local/cargo}/registry/src"; \
    test -d "$registry"; \
    mkdir -p /build/dependency-licenses; \
    printf '%s\n' 'Licences collected from the Cargo packages used to build IntiHA.' \
      > /build/dependency-licenses/README.txt; \
    for crate in "$registry"/*/*; do \
      [ -d "$crate" ] || continue; \
      crate_name="$(basename "$crate")"; \
      for notice in "$crate"/LICENSE* "$crate"/COPYING* "$crate"/NOTICE* "$crate"/UNLICENSE*; do \
        [ -f "$notice" ] || continue; \
        mkdir -p "/build/dependency-licenses/$crate_name"; \
        cp "$notice" "/build/dependency-licenses/$crate_name/"; \
      done; \
    done

FROM debian:bookworm-slim

ARG BUILD_VERSION=0.2.5
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}" \
      org.opencontainers.image.title="IntiHA Engine" \
      org.opencontainers.image.description="Buttplug server with a Home Assistant Bluetooth bridge" \
      org.opencontainers.image.source="https://github.com/s2n2/IntiHA" \
      org.opencontainers.image.licenses="BSD-3-Clause"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl jq libssl3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/target/release/intiha-engine /usr/local/bin/intiha-engine
COPY run.sh /usr/local/bin/intiha-run
COPY LICENSE /usr/share/licenses/intiha/LICENSE
COPY --from=builder /build/dependency-licenses/ /usr/share/licenses/intiha/dependencies/
RUN chmod 0755 /usr/local/bin/intiha-engine /usr/local/bin/intiha-run

ENV INTIHA_BIND=0.0.0.0 \
    INTIHA_CLIENT_PORT=12345 \
    INTIHA_BRIDGE_PORT=12346
EXPOSE 12345 12346
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD curl --fail --silent --show-error "http://127.0.0.1:${INTIHA_CLIENT_PORT}/health" >/dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/intiha-run"]
