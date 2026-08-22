#!/bin/bash
# Reverse SSH tunnel from the Mac to the herdr-remote AWS rendezvous host
# (see infra/aws-tunnel/). The Mac dials out; nothing inbound is ever
# needed on the Mac or the home router.
#
# Config (from ~/.config/herdr-remote/config.env or the environment):
#   HERDR_AWS_HOST          required - rendezvous host's stable hostname
#   HERDR_AWS_SSH_USER      default: herdr-tunnel
#   HERDR_AWS_SSH_PORT      default: 22
#   HERDR_AWS_SSH_KEY       default: ~/.ssh/herdr-remote-tunnel
#   HERDR_AWS_TUNNEL_PORT   default: 9375 (loopback port on the EC2 host)
#   HERDR_RELAY_PORT        default: 8375 (local relay port to forward)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=config-lib.sh
source "$SCRIPT_DIR/config-lib.sh"

CONFIG_FILE="$HOME/.config/herdr-remote/config.env"
SECRETS_FILE="$HOME/.config/herdr-remote/secrets.env"

load_config_file "$CONFIG_FILE"
load_config_file "$SECRETS_FILE"

HOST="${HERDR_AWS_HOST:-}"
SSH_USER="${HERDR_AWS_SSH_USER:-herdr-tunnel}"
SSH_PORT="${HERDR_AWS_SSH_PORT:-22}"
SSH_KEY="${HERDR_AWS_SSH_KEY:-$HOME/.ssh/herdr-remote-tunnel}"
TUNNEL_PORT="${HERDR_AWS_TUNNEL_PORT:-9375}"
RELAY_PORT="${HERDR_RELAY_PORT:-8375}"

die() { printf 'tunnel-aws: %s\n' "$1" >&2; exit 1; }

[ -n "$HOST" ] || die "HERDR_AWS_HOST is not set (add it to $CONFIG_FILE)"
[ -r "$SSH_KEY" ] || die "SSH key not readable: $SSH_KEY"

if [ -z "${HERDR_RELAY_TOKEN:-}" ]; then
  die "refusing to open the reverse tunnel without HERDR_RELAY_TOKEN.
  This tunnel publishes the relay on the public internet over HTTPS, and the
  relay grants whoever reaches it full control of your agents - read output,
  send keys, and trust all tools for a blocked agent. A token is mandatory
  for this path and there is no way to skip it.
  Set HERDR_RELAY_TOKEN in $SECRETS_FILE, or re-run relay/install-service.sh."
fi

SSH_OPTS=(
  -N
  -o ExitOnForwardFailure=yes
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=3
  -o StrictHostKeyChecking=accept-new
  -o BatchMode=yes
  -i "$SSH_KEY"
  -p "$SSH_PORT"
  -R "127.0.0.1:${TUNNEL_PORT}:127.0.0.1:${RELAY_PORT}"
  "${SSH_USER}@${HOST}"
)

echo "tunnel-aws: forwarding ${HOST}:${TUNNEL_PORT} -> 127.0.0.1:${RELAY_PORT}"

if command -v autossh >/dev/null 2>&1; then
  # Foreground (no -f): this script is meant to run under a service
  # supervisor (launchd/systemd), which needs to hold the PID to detect
  # and restart a fully-dead autossh, not just a dropped SSH session.
  export AUTOSSH_GATETIME=0
  exec autossh -M 0 -N \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 \
    -o StrictHostKeyChecking=accept-new \
    -o BatchMode=yes \
    -i "$SSH_KEY" \
    -p "$SSH_PORT" \
    -R "127.0.0.1:${TUNNEL_PORT}:127.0.0.1:${RELAY_PORT}" \
    "${SSH_USER}@${HOST}"
fi

echo "tunnel-aws: autossh not found, falling back to a supervised retry loop"
echo "tunnel-aws: install autossh for faster reconnects (brew install autossh)"

# ssh runs in the background so a signal can reach it. Left in the
# foreground it would outlive this script when a supervisor (start.sh's
# cleanup trap, or `herdr-remote stop`) kills us, and the orphan keeps
# the remote forward bound - which makes the next connection fail
# against ExitOnForwardFailure with no visible reason.
SSH_PID=""
cleanup() {
  trap - INT TERM EXIT
  if [ -n "$SSH_PID" ]; then
    kill "$SSH_PID" 2>/dev/null
    wait "$SSH_PID" 2>/dev/null
  fi
  exit 0
}
trap cleanup INT TERM EXIT

BACKOFF=1
while true; do
  ssh "${SSH_OPTS[@]}" &
  SSH_PID=$!
  wait "$SSH_PID" 2>/dev/null
  SSH_PID=""
  echo "tunnel-aws: connection dropped, retrying in ${BACKOFF}s"
  sleep "$BACKOFF"
  BACKOFF=$(( BACKOFF < 30 ? BACKOFF * 2 : 30 ))
done
