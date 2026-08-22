#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=config-lib.sh
source "$SCRIPT_DIR/config-lib.sh"
CONFIG_FILE="$HOME/.config/herdr-remote/config.env"

RELAY_PID=""
TUNNEL_PID=""

cleanup() {
    local rc=$?
    set +e
    trap - INT TERM EXIT
    echo ""
    echo "Shutting down..."
    [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null && wait "$TUNNEL_PID" 2>/dev/null
    [ -n "$RELAY_PID" ] && kill "$RELAY_PID" 2>/dev/null && wait "$RELAY_PID" 2>/dev/null
    echo "Done."
    exit "$rc"
}

trap cleanup INT TERM EXIT

echo "herdr-remote relay"
echo ""

# Load config if available
SECRETS_FILE="$HOME/.config/herdr-remote/secrets.env"
load_config_file "$CONFIG_FILE"
load_config_file "$SECRETS_FILE"

WS_PORT="${HERDR_RELAY_PORT:-8375}"

TUNNEL_MODE="${HERDR_TUNNEL_MODE:-temp}"

# Refuse before anything is started, so the relay is not raised and then
# torn down again on the way out.
if [ "$TUNNEL_MODE" = "aws" ] && [ -z "${HERDR_RELAY_TOKEN:-}" ]; then
    echo "Error: refusing to start the AWS reverse tunnel without HERDR_RELAY_TOKEN."
    echo "  The AWS tunnel publishes this relay on the public internet over HTTPS,"
    echo "  and the relay grants whoever reaches it full control of your agents."
    echo "  A token is mandatory for this path and there is no way to skip it."
    echo "  Set HERDR_RELAY_TOKEN in $SECRETS_FILE, or re-run install-service.sh."
    exit 1
fi

# 1. Start relay
echo "Starting relay on :$WS_PORT..."
uv run "$SCRIPT_DIR/herdr_relay.py" &
RELAY_PID=$!
sleep 2

if ! kill -0 "$RELAY_PID" 2>/dev/null; then
    echo "Error: Relay failed to start. Check if port $WS_PORT is in use."
    echo "  lsof -iTCP:$WS_PORT"
    RELAY_PID=""
    exit 1
fi
echo "Relay running (pid $RELAY_PID)"

# 2. Start tunnel
if [ "$TUNNEL_MODE" = "aws" ]; then
    echo "Starting AWS reverse tunnel..."
    "$SCRIPT_DIR/tunnel-aws.sh" &
    TUNNEL_PID=$!
    sleep 2

    if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
        echo "Error: AWS tunnel failed to start. Check HERDR_AWS_HOST/HERDR_AWS_SSH_KEY in $CONFIG_FILE."
        TUNNEL_PID=""
    elif [ -n "${HERDR_AWS_HOST:-}" ]; then
        # The supervisor staying alive proves nothing: the plain-ssh fallback
        # loop keeps retrying forever even if every connection attempt fails.
        # Probe the endpoint before calling it reachable.
        TUNNEL_URL="https://${HERDR_AWS_HOST}"
        if curl -fsS -o /dev/null --max-time 10 \
             -H "Authorization: Bearer $HERDR_RELAY_TOKEN" "$TUNNEL_URL" 2>/dev/null; then
            echo "Tunnel URL: $TUNNEL_URL (reachable)"
        else
            echo "Tunnel supervisor started; URL will be $TUNNEL_URL once connected."
            echo "  Not answering yet - it may still be connecting, or check the"
            echo "  SSH key, HERDR_AWS_HOST, and the EC2 security group's SSH CIDR."
        fi
    fi
elif command -v cloudflared >/dev/null 2>&1; then

    if [ "$TUNNEL_MODE" = "named" ] && [ -n "$HERDR_TUNNEL_NAME" ]; then
        echo "Starting named tunnel ($HERDR_TUNNEL_NAME)..."
        CF_CONFIG="$HOME/.cloudflared/config-herdr.yml"
        if [ -f "$CF_CONFIG" ]; then
            cloudflared tunnel --config "$CF_CONFIG" run "$HERDR_TUNNEL_NAME" &
            TUNNEL_PID=$!
        else
            echo "Warning: Tunnel config not found at $CF_CONFIG"
            echo "Run install-service.sh to configure the named tunnel."
            echo "Falling back to temp tunnel..."
            TUNNEL_MODE="temp"
        fi
    fi

    if [ "$TUNNEL_MODE" = "temp" ]; then
        echo "Starting temp tunnel..."
        cloudflared tunnel --url "http://localhost:$WS_PORT" 2>&1 &
        TUNNEL_PID=$!
        sleep 4

        if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
            echo "Warning: Tunnel failed to start. Relay still running locally."
            TUNNEL_PID=""
        else
            # Extract URL from cloudflared output
            TUNNEL_URL=$(grep -o 'https://[^ ]*\.trycloudflare\.com' /proc/$TUNNEL_PID/fd/1 2>/dev/null || true)
            # Fallback: check recent log output
            if [ -z "$TUNNEL_URL" ]; then
                sleep 2
                echo ""
                echo "Tunnel starting... URL will appear below:"
                echo "(If not visible, check: ps aux | grep cloudflared)"
            fi
        fi
    fi

    if [ "$TUNNEL_MODE" = "none" ]; then
        echo "Tunnel disabled (config: HERDR_TUNNEL_MODE=none)"
    fi
else
    echo "cloudflared not found — running local only."
    echo "Install: brew install cloudflared"
fi

echo ""
echo "Ready. Press Ctrl+C to stop."
echo ""

# Wait for relay (primary process)
wait "$RELAY_PID"
