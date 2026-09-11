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
    printf 'herdr pane list HERDR_SESSION=%s\n' "${HERDR_SESSION-<unset>}" >> "$HERDR_TEST_CALLS"
fi
if [ "$1" = "session" ] && [ "$2" = "list" ]; then
    if [ -n "${HERDR_TEST_SESSIONS:-}" ]; then
        printf '%s\n' "$HERDR_TEST_SESSIONS"
    else
        printf '%s\n' '{"sessions":[]}'
    fi
fi
exit 0
EOF

cat > "$MOCK_BIN/id" <<'EOF'
#!/bin/sh
if [ "${HERDR_TEST_ROOT:-0}" = "1" ] && [ "${1:-}" = "-u" ]; then
    printf '%s\n' '0'
    exit 0
fi
if [ -n "${HERDR_TEST_NONROOT_UID:-}" ] && [ "${1:-}" = "-u" ]; then
    printf '%s\n' "$HERDR_TEST_NONROOT_UID"
    exit 0
fi
exec /usr/bin/id "$@"
EOF

cat > "$MOCK_BIN/stat" <<'EOF'
#!/bin/sh
case " $* " in
    *" %u "*)
        if [ -n "${HERDR_TEST_OWNER_UID:-}" ]; then
            printf '%s\n' "$HERDR_TEST_OWNER_UID"
            exit 0
        fi
        if [ -n "${HERDR_TEST_NONROOT_UID:-}" ]; then
            printf '%s\n' "$HERDR_TEST_NONROOT_UID"
            exit 0
        fi
        ;;
esac
exec /usr/bin/stat "$@"
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
printf 'curl %s\n' "$*" >> "$HERDR_TEST_CALLS"
for arg in "$@"; do
    case "$arg" in
        https://api.telegram.org/*) url="$arg" ;;
    esac
done
if [ -z "$url" ]; then
    config="$(cat)"
    case "$config" in
        *'/getMe"'*) url="getMe" ;;
        *'/getUpdates"'*) url="getUpdates" ;;
        *'/sendMessage"'*) url="sendMessage" ;;
    esac
fi
case "$url" in
    *getMe)
        printf '%s\n' '{"ok":true,"result":{"id":42,"username":"installer_test_bot"}}'
        ;;
    *getUpdates)
        printf '%s\n' '{"ok":true,"result":[{"update_id":1,"message":{"chat":{"id":123456,"type":"private","first_name":"Installer"}}}]}'
        ;;
    *sendMessage)
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
        HERDR_LOG_DIR= \
        HERDR_RELAY= \
        HERDR_RELAY_TOKEN= \
        HERDR_TG_CHAT_ID= \
        HERDR_TG_CHAT_TYPE= \
        HERDR_TG_ENABLED= \
        HERDR_TG_TOKEN= \
        HERDR_TG_USERNAME= \
        HERDR_SESSION="${HERDR_TEST_ENV_SESSION:-}" \
        HERDR_TEST_SESSIONS="${HERDR_TEST_SESSIONS:-}" \
        HERDR_TEST_NONROOT_UID="${HERDR_TEST_NONROOT_UID:-501}" \
        HERDR_TEST_PGREP="${HERDR_TEST_PGREP:-0}" \
        HERDR_TEST_ROOT="${HERDR_TEST_ROOT:-0}" \
        HERDR_TEST_OWNER_UID="${HERDR_TEST_OWNER_UID:-}" \
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
        HERDR_TEST_NONROOT_UID="${HERDR_TEST_NONROOT_UID:-501}" \
        HERDR_TEST_OWNER_UID="${HERDR_TEST_OWNER_UID:-}" \
        HERDR_TEST_ROOT="${HERDR_TEST_ROOT:-0}" \
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

ROOT_GUARD_HOME="$TMP/root-guard-home"
if HERDR_TEST_ROOT=1 run_install macos "$ROOT_GUARD_HOME" '' > "$TMP/root-guard.log" 2>&1; then
    echo "root installer invocation unexpectedly succeeded" >&2
    exit 1
fi
assert_contains "$TMP/root-guard.log" 'Do not run install-service.sh with sudo or as root'

FOREIGN_HOME="$TMP/foreign-owner-home"
mkdir -p "$FOREIGN_HOME/.config/herdr-remote"
printf '%s\n' 'HERDR_RELAY_TOKEN=' > "$FOREIGN_HOME/.config/herdr-remote/secrets.env"
foreign_uid="$(( $(id -u) + 1 ))"
if HERDR_TEST_OWNER_UID="$foreign_uid" run_install macos "$FOREIGN_HOME" '' > "$TMP/foreign-owner.log" 2>&1; then
    echo "foreign-owned secrets unexpectedly accepted" >&2
    exit 1
fi
assert_contains "$TMP/foreign-owner.log" 'is not owned by the current user'

FOREIGN_CONFIG_HOME="$TMP/foreign-config-home"
mkdir -p "$FOREIGN_CONFIG_HOME/.config/herdr-remote"
printf '%s\n' 'HERDR_RELAY_PORT=8375' > "$FOREIGN_CONFIG_HOME/.config/herdr-remote/config.env"
if HERDR_TEST_OWNER_UID="$foreign_uid" run_install macos "$FOREIGN_CONFIG_HOME" '' > "$TMP/foreign-config.log" 2>&1; then
    echo "foreign-owned config unexpectedly accepted" >&2
    exit 1
fi
assert_contains "$TMP/foreign-config.log" 'config.env is not owned by the current user'

OPEN_CONFIG_HOME="$TMP/open-config-home"
mkdir -p "$OPEN_CONFIG_HOME/.config/herdr-remote"
printf '%s\n' 'HERDR_RELAY_PORT=8375' > "$OPEN_CONFIG_HOME/.config/herdr-remote/config.env"
chmod 666 "$OPEN_CONFIG_HOME/.config/herdr-remote/config.env"
if run_install macos "$OPEN_CONFIG_HOME" '' > "$TMP/open-config.log" 2>&1; then
    echo "writable config unexpectedly accepted" >&2
    exit 1
fi
assert_contains "$TMP/open-config.log" 'must not be group/world writable'

OPEN_MODE_HOME="$TMP/open-mode-home"
mkdir -p "$OPEN_MODE_HOME/.config/herdr-remote"
printf '%s\n' 'HERDR_RELAY_TOKEN=' > "$OPEN_MODE_HOME/.config/herdr-remote/secrets.env"
chmod 644 "$OPEN_MODE_HOME/.config/herdr-remote/secrets.env"
if run_install macos "$OPEN_MODE_HOME" '' > "$TMP/open-mode.log" 2>&1; then
    echo "overly permissive secrets unexpectedly accepted" >&2
    exit 1
fi
assert_contains "$TMP/open-mode.log" 'must have mode 0600'

BROKEN_UNINSTALL_HOME="$TMP/broken-uninstall-home"
mkdir -p "$BROKEN_UNINSTALL_HOME/.config/herdr-remote" "$BROKEN_UNINSTALL_HOME/Library/LaunchAgents"
printf '%s\n' 'HERDR_RELAY_TOKEN=' > "$BROKEN_UNINSTALL_HOME/.config/herdr-remote/secrets.env"
touch "$BROKEN_UNINSTALL_HOME/Library/LaunchAgents/com.herdr-remote.relay.plist"
touch "$BROKEN_UNINSTALL_HOME/Library/LaunchAgents/com.herdr-remote.telegram.plist"
HERDR_TEST_OWNER_UID="$foreign_uid" run_uninstall macos "$BROKEN_UNINSTALL_HOME" > "$TMP/broken-uninstall.log"
[ ! -f "$BROKEN_UNINSTALL_HOME/Library/LaunchAgents/com.herdr-remote.relay.plist" ] || {
    echo "relay LaunchAgent was not removed with foreign-owned secrets" >&2
    exit 1
}
assert_file "$BROKEN_UNINSTALL_HOME/.config/herdr-remote/secrets.env"
assert_contains "$TMP/broken-uninstall.log" 'Configuration and secrets preserved'

MAC_HOME="$TMP/mac-home"
mkdir -p "$MAC_HOME/Library/LaunchAgents"
touch "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.tunnel.plist"
run_install macos "$MAC_HOME" $'yy123456:ABC_def\n\nyn' > "$TMP/mac-new.log" || {
    cat "$TMP/mac-new.log"
    exit 1
}
assert_file "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.relay.plist"
assert_file "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.telegram.plist"
assert_file "$MAC_HOME/.config/herdr-remote/secrets.env"
[ ! -f "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.tunnel.plist" ] || {
    echo "stale macOS tunnel service was not removed" >&2
    exit 1
}
assert_contains "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.telegram.plist" 'herdr_telegram.py'
assert_contains "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.relay.plist" 'secrets.env'
assert_contains "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.relay.plist" 'set -e; set -a; source'
assert_contains "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.telegram.plist" 'set -e; set -a; source'
assert_not_contains "$MAC_HOME/Library/LaunchAgents/com.herdr-remote.telegram.plist" '123456:ABC_def'
python3 -c 'import os, stat, sys; info = os.stat(sys.argv[1]); mode = stat.S_IMODE(info.st_mode); raise SystemExit(0 if mode == 0o600 and info.st_uid == os.getuid() else 1)' "$MAC_HOME/.config/herdr-remote/secrets.env"
python3 -c 'import os, stat, sys; info = os.stat(sys.argv[1]); mode = stat.S_IMODE(info.st_mode); raise SystemExit(0 if mode == 0o644 and info.st_uid == os.getuid() else 1)' "$MAC_HOME/.config/herdr-remote/config.env"
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
mkdir -p "$LINUX_HOME/.config/systemd/user"
touch "$LINUX_HOME/.config/systemd/user/herdr-tunnel.service"
run_install linux "$LINUX_HOME" $'yy123456:ABC_def\n\nyn' > "$TMP/linux.log"
assert_file "$LINUX_HOME/.config/systemd/user/herdr-relay.service"
assert_file "$LINUX_HOME/.config/systemd/user/herdr-telegram.service"
[ ! -f "$LINUX_HOME/.config/systemd/user/herdr-tunnel.service" ] || {
    echo "stale Linux tunnel service was not removed" >&2
    exit 1
}
assert_contains "$LINUX_HOME/.config/systemd/user/herdr-relay.service" 'EnvironmentFile='
assert_not_contains "$LINUX_HOME/.config/systemd/user/herdr-relay.service" 'EnvironmentFile=-'
assert_not_contains "$LINUX_HOME/.config/systemd/user/herdr-telegram.service" 'EnvironmentFile=-'
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
assert_contains "$TMP/calls.log" 'launchctl bootout gui/501/com.herdr-remote.tunnel'
assert_contains "$TMP/calls.log" 'systemctl --user enable herdr-telegram.service'
assert_contains "$TMP/calls.log" 'systemctl --user disable herdr-tunnel.service'
assert_not_contains "$TMP/calls.log" '123456:ABC_def'

# The relay polls one herdr session and reads it from HERDR_SESSION in its own
# environment. A managed service inherits nothing from the installing shell, so
# config.env has to carry it or the relay polls the default session's socket and
# reports no agents.
SESSION_ONE_HOME="$TMP/session-one-home"
HERDR_TEST_SESSIONS='{"sessions":[{"name":"default","running":false,"default":true},{"name":"main","running":true,"default":false}]}' \
    run_install linux "$SESSION_ONE_HOME" $'y\nn\n' > "$TMP/session-one.log"
assert_contains "$SESSION_ONE_HOME/.config/herdr-remote/config.env" '^HERDR_SESSION=main$'
assert_contains "$TMP/session-one.log" 'the only named session running'
assert_contains "$CALLS" 'herdr pane list HERDR_SESSION=main'

# No named session running: leave it unset. The default session's socket sits at
# the config root rather than under sessions/<name>/, so pinning it by name would
# point the relay at a path that does not exist.
SESSION_NONE_HOME="$TMP/session-none-home"
HERDR_TEST_SESSIONS='{"sessions":[{"name":"default","running":true,"default":true}]}' \
    run_install linux "$SESSION_NONE_HOME" $'y\nn\n' > "$TMP/session-none.log"
assert_contains "$SESSION_NONE_HOME/.config/herdr-remote/config.env" '^HERDR_SESSION=$'

# An installer run from inside a session pins that one without asking.
SESSION_ENV_HOME="$TMP/session-env-home"
HERDR_TEST_ENV_SESSION=work \
HERDR_TEST_SESSIONS='{"sessions":[{"name":"work","running":true,"default":false},{"name":"other","running":true,"default":false}]}' \
    run_install linux "$SESSION_ENV_HOME" $'y\nn\n' > "$TMP/session-env.log"
assert_contains "$SESSION_ENV_HOME/.config/herdr-remote/config.env" '^HERDR_SESSION=work$'
assert_contains "$TMP/session-env.log" 'inherited from this shell'

# Several named sessions and nothing inherited: the operator picks one.
SESSION_PICK_HOME="$TMP/session-pick-home"
HERDR_TEST_SESSIONS='{"sessions":[{"name":"alpha","running":true,"default":false},{"name":"beta","running":true,"default":false}]}' \
    run_install linux "$SESSION_PICK_HOME" $'y2\nnn' > "$TMP/session-pick.log"
assert_contains "$SESSION_PICK_HOME/.config/herdr-remote/config.env" '^HERDR_SESSION=beta$'

echo "installer service tests passed"
