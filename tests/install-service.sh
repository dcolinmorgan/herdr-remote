#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
MOCK_BIN="$TMP/bin"
CALLS="$TMP/calls.log"
mkdir -p "$MOCK_BIN"
: > "$CALLS"

cat > "$MOCK_BIN/uv" <<'EOF'
#!/bin/sh
exit 0
EOF

cat > "$MOCK_BIN/herdr" <<'EOF'
#!/bin/sh
if [ "$1" = "pane" ] && [ "$2" = "list" ]; then
    printf '%s\n' '{"result":{"panes":[]}}'
fi
exit 0
EOF

cat > "$MOCK_BIN/launchctl" <<'EOF'
#!/bin/sh
printf 'launchctl %s\n' "$*" >> "$HERDR_TEST_CALLS"
exit 0
EOF

cat > "$MOCK_BIN/systemctl" <<'EOF'
#!/bin/sh
printf 'systemctl %s\n' "$*" >> "$HERDR_TEST_CALLS"
exit 0
EOF

cat > "$MOCK_BIN/lsof" <<'EOF'
#!/bin/sh
case " $* " in
    *" -t "*) exit 1 ;;
    *) exit 0 ;;
esac
EOF

cat > "$MOCK_BIN/ss" <<'EOF'
#!/bin/sh
exit 1
EOF

cat > "$MOCK_BIN/pgrep" <<'EOF'
#!/bin/sh
if [ "${HERDR_TEST_PGREP:-0}" = "1" ]; then
    printf '%s\n' '99999'
    exit 0
fi
exit 1
EOF

cat > "$MOCK_BIN/curl" <<'EOF'
#!/bin/sh
url=""
for arg in "$@"; do
    case "$arg" in
        https://api.telegram.org/*) url="$arg" ;;
    esac
done
case "$url" in
    */getMe)
        printf '%s\n' '{"ok":true,"result":{"id":42,"username":"installer_test_bot"}}'
        ;;
    */getUpdates)
        printf '%s\n' '{"ok":true,"result":[{"update_id":1,"message":{"chat":{"id":123456,"type":"private","first_name":"Installer"}}}]}'
        ;;
    */sendMessage)
        printf '%s\n' '{"ok":true,"result":{"message_id":1}}'
        ;;
    *)
        exit 1
        ;;
esac
EOF

chmod +x "$MOCK_BIN"/*

run_install() {
    local os="$1"
    local home="$2"
    local input="$3"
    mkdir -p "$home"
    printf '%s' "$input" | env \
        HOME="$home" \
        PATH="$MOCK_BIN:/usr/bin:/bin:/usr/sbin:/sbin" \
        HERDR_TEST_CALLS="$CALLS" \
        HERDR_INSTALL_OS="$os" \
        HERDR_INSTALL_SKIP_CLOUDFLARED=1 \
        HERDR_INSTALL_SKIP_WEBSOCKET_SMOKE=1 \
        HERDR_INSTALL_SETTLE_SECONDS=0 \
        HERDR_INSTALL_SERVICE_DELAY=0 \
        HERDR_TEST_PGREP="${HERDR_TEST_PGREP:-0}" \
        bash "$ROOT/relay/install-service.sh"
}

run_uninstall() {
    local os="$1"
    local home="$2"
    env \
        HOME="$home" \
        PATH="$MOCK_BIN:/usr/bin:/bin:/usr/sbin:/sbin" \
        HERDR_TEST_CALLS="$CALLS" \
        HERDR_INSTALL_OS="$os" \
        HERDR_INSTALL_SKIP_CLOUDFLARED=1 \
        bash "$ROOT/relay/install-service.sh" --uninstall
}

assert_file() {
    [ -f "$1" ] || { echo "missing file: $1" >&2; exit 1; }
}

assert_contains() {
    grep -q "$2" "$1" || { echo "missing '$2' in $1" >&2; exit 1; }
}

assert_not_contains() {
    ! grep -q "$2" "$1" || { echo "unexpected '$2' in $1" >&2; exit 1; }
}

MAC_HOME="$TMP/mac-home"
run_install macos "$MAC_HOME" $'yy123456:ABC_def\n\nyn' > "$TMP/mac-new.log" || {
    cat "$TMP/mac-new.log"
    exit 1
}
assert_file "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.relay.plist"
assert_file "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.telegram.plist"
assert_file "$MAC_HOME/.config/herdr-remote/secrets.env"
assert_contains "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.telegram.plist" 'herdr_telegram.py'
assert_contains "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.relay.plist" 'secrets.env'
assert_not_contains "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.telegram.plist" '123456:ABC_def'
python3 -c 'import os, stat, sys; mode = stat.S_IMODE(os.stat(sys.argv[1]).st_mode); raise SystemExit(0 if mode == 0o600 else 1)' "$MAC_HOME/.config/herdr-remote/secrets.env"
assert_contains "$TMP/mac-new.log" 'Telegram bot verified as @installer_test_bot'

run_install macos "$MAC_HOME" 'yyyyn' > "$TMP/mac-retain.log" || {
    cat "$TMP/mac-retain.log"
    exit 1
}
assert_contains "$MAC_HOME/.config/herdr-remote/secrets.env" 'HERDR_TG_CHAT_ID=123456'
assert_contains "$TMP/mac-retain.log" 'Existing destination: 123456'

run_install macos "$MAC_HOME" 'nn' > "$TMP/mac-disable.log" || {
    cat "$TMP/mac-disable.log"
    exit 1
}
[ ! -f "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.telegram.plist" ] || {
    echo "Telegram LaunchAgent was not removed" >&2
    exit 1
}
assert_contains "$MAC_HOME/.config/herdr-remote/secrets.env" 'HERDR_TG_TOKEN=123456:ABC_def'

run_install macos "$MAC_HOME" 'nn' > "$TMP/mac-still-disabled.log" || {
    cat "$TMP/mac-still-disabled.log"
    exit 1
}
[ ! -f "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.telegram.plist" ] || {
    echo "disabled Telegram service was re-enabled by default" >&2
    exit 1
}

SKIP_HOME="$TMP/skip-home"
run_install macos "$SKIP_HOME" 'ynn' > "$TMP/skip.log"
[ ! -f "$SKIP_HOME/Library/LaunchAgents/com.herdr-remote.telegram.plist" ] || {
    echo "Telegram LaunchAgent created for skipped setup" >&2
    exit 1
}

LINUX_HOME="$TMP/linux-home"
run_install linux "$LINUX_HOME" $'yy123456:ABC_def\n\nyn' > "$TMP/linux.log"
assert_file "$LINUX_HOME/.config/systemd/user/herdr-relay.service"
assert_file "$LINUX_HOME/.config/systemd/user/herdr-telegram.service"
assert_contains "$LINUX_HOME/.config/systemd/user/herdr-relay.service" 'EnvironmentFile='
assert_contains "$LINUX_HOME/.config/systemd/user/herdr-telegram.service" 'After=network-online.target herdr-relay.service'
assert_not_contains "$LINUX_HOME/.config/systemd/user/herdr-telegram.service" '123456:ABC_def'

run_uninstall linux "$LINUX_HOME" > "$TMP/linux-uninstall.log"
[ ! -f "$LINUX_HOME/.config/systemd/user/herdr-relay.service" ] || {
    echo "relay unit was not removed by uninstall" >&2
    exit 1
}
[ ! -f "$LINUX_HOME/.config/systemd/user/herdr-telegram.service" ] || {
    echo "Telegram unit was not removed by uninstall" >&2
    exit 1
}
assert_file "$LINUX_HOME/.config/herdr-remote/secrets.env"
assert_contains "$TMP/linux-uninstall.log" 'Configuration and secrets preserved'

CONFLICT_HOME="$TMP/conflict-home"
if HERDR_TEST_PGREP=1 run_install macos "$CONFLICT_HOME" $'yy123456:ABC_def\n\nyn' > "$TMP/conflict.log" 2>&1; then
    echo "duplicate Telegram poller unexpectedly succeeded" >&2
    exit 1
fi
assert_contains "$TMP/conflict.log" 'Another Telegram bot process is already running'

INVALID_HOME="$TMP/invalid-home"
if run_install linux "$INVALID_HOME" $'yybad-token\n' > "$TMP/invalid.log" 2>&1; then
    echo "invalid Telegram token unexpectedly succeeded" >&2
    exit 1
fi
assert_contains "$TMP/invalid.log" 'Invalid BotFather token format'
[ ! -f "$INVALID_HOME/.config/herdr-remote/secrets.env" ] || {
    echo "invalid credentials were persisted" >&2
    exit 1
}

assert_contains "$MAC_HOME/.config/herdr-remote/secrets.env" 'HERDR_RELAY_TOKEN='
assert_contains "$TMP/calls.log" 'launchctl bootstrap'
assert_contains "$TMP/calls.log" 'systemctl --user enable herdr-telegram.service'

echo "installer service tests passed"
