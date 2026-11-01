#!/bin/sh
# Never enable shell tracing here: the configuration contains credentials.
set -eu
export LC_ALL=C

INTIHA_CLIENT_TOKEN="${INTIHA_CLIENT_TOKEN:-}"
INTIHA_BRIDGE_TOKEN="${INTIHA_BRIDGE_TOKEN:-}"

fail() {
    printf '%s\n' "IntiHA: $1" >&2
    exit 1
}

# Supervisor supplies /data/options.json. Standalone containers use env vars.
if [ -f /data/options.json ]; then
    INTIHA_CLIENT_TOKEN="$(jq -er '.client_token | strings' /data/options.json)" \
        || fail 'Set a client_token in the app configuration.'
    INTIHA_BRIDGE_TOKEN="$(jq -er '.bridge_token | strings' /data/options.json)" \
        || fail 'Set a bridge_token in the app configuration.'
    INTIHA_LOG_LEVEL="$(jq -er '.log_level // "info" | strings' /data/options.json)" \
        || fail 'Invalid log_level in the app configuration.'
fi

[ "${#INTIHA_CLIENT_TOKEN}" -ge 32 ] && [ "${#INTIHA_CLIENT_TOKEN}" -le 256 ] \
    || fail 'client_token must contain 32 to 256 characters.'
[ "${#INTIHA_BRIDGE_TOKEN}" -ge 32 ] && [ "${#INTIHA_BRIDGE_TOKEN}" -le 256 ] \
    || fail 'bridge_token must contain 32 to 256 characters.'
case "$INTIHA_CLIENT_TOKEN" in
    *[![:graph:]]*) fail 'client_token must use visible ASCII characters without spaces.' ;;
esac
case "$INTIHA_BRIDGE_TOKEN" in
    *[![:graph:]]*) fail 'bridge_token must use visible ASCII characters without spaces.' ;;
esac
[ "$INTIHA_CLIENT_TOKEN" != "$INTIHA_BRIDGE_TOKEN" ] \
    || fail 'Use different values for client_token and bridge_token.'

case "${INTIHA_LOG_LEVEL:-info}" in
    error|warn|info|debug) ;;
    *) fail 'log_level must be error, warn, info or debug.' ;;
esac

export INTIHA_CLIENT_TOKEN INTIHA_BRIDGE_TOKEN
export RUST_LOG="${RUST_LOG:-intiha_engine=${INTIHA_LOG_LEVEL:-info},buttplug_server=warn}"
exec /usr/local/bin/intiha-engine
