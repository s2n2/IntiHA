#!/bin/sh
# The same entrypoint works as a Supervisor app or a standalone container.
set -eu
export LC_ALL=C

fail() {
    printf '%s\n' "IntiHA: $1" >&2
    exit 1
}

if [ -f /data/options.json ]; then
    INTIHA_LOG_LEVEL="$(jq -er '.log_level // "info" | strings' /data/options.json)" \
        || fail 'Invalid log_level in the app configuration.'
fi
case "${INTIHA_LOG_LEVEL:-info}" in
    error|warn|info|debug) ;;
    *) fail 'log_level must be error, warn, info or debug.' ;;
esac
export RUST_LOG="${RUST_LOG:-intiha_engine=${INTIHA_LOG_LEVEL:-info},buttplug_server=warn}"

announce_discovery() {
    # Supervisor supplies this credential automatically; users configure nothing.
    # Pass its header on stdin so it is never printed or present in curl's argv.
    sleep_pid=""
    stop_discovery() {
        trap '' TERM INT
        if [ -n "$sleep_pid" ]; then
            kill "$sleep_pid" 2>/dev/null || true
            wait "$sleep_pid" 2>/dev/null || true
        fi
        exit 0
    }
    trap stop_discovery TERM INT

    supervisor_request() {
        printf 'Authorization: Bearer %s\n' "$SUPERVISOR_TOKEN" \
            | curl --fail --silent --connect-timeout 2 --max-time 5 \
                --header @- "$@"
    }

    delay=2
    while :; do
        # Do not announce an engine that failed to bind its ports.
        if curl --fail --silent --connect-timeout 2 --max-time 3 \
            "http://127.0.0.1:${INTIHA_CLIENT_PORT:-12345}/health" >/dev/null \
            && info="$(supervisor_request http://supervisor/addons/self/info)" \
            && host="$(printf '%s' "$info" | jq -er 'select(.result == "ok") | .data.hostname | strings | select(length > 0)')"; then
            case "$host" in
                *[!a-zA-Z0-9.-]*|'') ;;
                *)
                    body="$(jq -nc --arg host "$host" \
                        --argjson port "${INTIHA_BRIDGE_PORT:-12346}" \
                        '{service:"intiha",config:{host:$host,port:$port}}')"
                    if response="$(supervisor_request --request POST \
                        --header 'Content-Type: application/json' --data "$body" \
                        http://supervisor/discovery)" \
                        && printf '%s' "$response" | jq -e '.result == "ok"' >/dev/null; then
                        printf '%s\n' 'IntiHA: Home Assistant discovery announced.'
                        return 0
                    fi
                    ;;
            esac
        fi
        # Retry independently of the running engine, with bounded request times
        # and interruptible exponential backoff for Supervisor/Core startup order.
        sleep "$delay" &
        sleep_pid=$!
        wait "$sleep_pid" || true
        sleep_pid=""
        if [ "$delay" -lt 60 ]; then
            delay=$((delay * 2))
            [ "$delay" -le 60 ] || delay=60
        fi
    done
}

engine_pid=""
discovery_pid=""
cleanup() {
    trap '' TERM INT
    if [ -n "$discovery_pid" ]; then
        kill "$discovery_pid" 2>/dev/null || true
    fi
    if [ -n "$engine_pid" ]; then
        kill -TERM "$engine_pid" 2>/dev/null || true
        wait "$engine_pid" 2>/dev/null || true
    fi
    if [ -n "$discovery_pid" ]; then
        wait "$discovery_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 0' TERM INT

# Keep platform credentials out of the engine process. Its two WebSocket ports
# accept ordinary local-network clients without any IntiHA credentials.
(
    unset SUPERVISOR_TOKEN HASSIO_TOKEN INTIHA_CLIENT_TOKEN INTIHA_BRIDGE_TOKEN
    exec intiha-engine
) &
engine_pid=$!
if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
    announce_discovery &
    discovery_pid=$!
fi
unset SUPERVISOR_TOKEN HASSIO_TOKEN

status=0
wait "$engine_pid" || status=$?
engine_pid=""
exit "$status"
