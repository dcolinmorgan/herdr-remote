#!/bin/bash
set -e

LABEL_RELAY="com.herdr-remote.relay"
LABEL_TUNNEL="com.herdr-remote.tunnel"
LABEL_TELEGRAM="com.herdr-remote.telegram"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="$HOME/.config/herdr-remote"
CONFIG_FILE="$CONFIG_DIR/config.env"
SECRETS_FILE="$CONFIG_DIR/secrets.env"
# shellcheck source=config-lib.sh
source "$SCRIPT_DIR/config-lib.sh"

# --- Detect OS ---

detect_os() {
    case "$(uname -s)" in
        Darwin) echo "macos" ;;
        Linux)  echo "linux" ;;
        *)      echo "unsupported" ;;
    esac
}

OS="${HERDR_INSTALL_OS:-$(detect_os)}"
if [ "$OS" = "unsupported" ]; then
    echo "Error: Unsupported OS ($(uname -s)). Only macOS and Linux are supported."
    exit 1
fi

CURRENT_UID="$(id -u)"
if [ "$CURRENT_UID" -eq 0 ]; then
    echo "Error: Do not run install-service.sh with sudo or as root."
    echo "The relay and Telegram run as user services and must own their configuration files."
    exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
    echo "Uninstalling herdr-remote services..."
    if [ "$OS" = "macos" ]; then
        launchctl bootout "gui/$(id -u)/$LABEL_RELAY" 2>/dev/null || true
        launchctl bootout "gui/$(id -u)/$LABEL_TUNNEL" 2>/dev/null || true
        launchctl bootout "gui/$(id -u)/$LABEL_TELEGRAM" 2>/dev/null || true
        rm -f "$HOME/Library/LaunchAgents/$LABEL_RELAY.plist"
        rm -f "$HOME/Library/LaunchAgents/$LABEL_TUNNEL.plist"
        rm -f "$HOME/Library/LaunchAgents/$LABEL_TELEGRAM.plist"
    else
        systemctl --user stop herdr-relay.service 2>/dev/null || true
        systemctl --user stop herdr-tunnel.service 2>/dev/null || true
        systemctl --user stop herdr-telegram.service 2>/dev/null || true
        systemctl --user disable herdr-relay.service 2>/dev/null || true
        systemctl --user disable herdr-tunnel.service 2>/dev/null || true
        systemctl --user disable herdr-telegram.service 2>/dev/null || true
        rm -f "$HOME/.config/systemd/user/herdr-relay.service"
        rm -f "$HOME/.config/systemd/user/herdr-tunnel.service"
        rm -f "$HOME/.config/systemd/user/herdr-telegram.service"
        systemctl --user daemon-reload
    fi
    echo "Done. Configuration and secrets preserved in $CONFIG_DIR"
    exit 0
fi

file_owner_uid() {
    if [ "$(uname -s)" = "Darwin" ]; then
        stat -f '%u' "$1"
    else
        stat -c '%u' "$1"
    fi
}

file_mode() {
    if [ "$(uname -s)" = "Darwin" ]; then
        stat -f '%Lp' "$1"
    else
        stat -c '%a' "$1"
    fi
}

# --- Load existing configuration ---

EXISTING_INSTALL=false
if [ -f "$CONFIG_FILE" ]; then
    CONFIG_OWNER_UID="$(file_owner_uid "$CONFIG_FILE")"
    if [ "$CONFIG_OWNER_UID" != "$CURRENT_UID" ]; then
        echo "Error: $CONFIG_FILE is not owned by the current user."
        echo "Repair it with: sudo chown \"$(id -un):$(id -gn)\" \"$CONFIG_FILE\""
        exit 1
    fi
    CONFIG_MODE="$(file_mode "$CONFIG_FILE")"
    case "$CONFIG_MODE" in
        600|640|644) ;;
        *)
            echo "Error: $CONFIG_FILE must not be group/world writable, found mode 0$CONFIG_MODE."
            echo "Repair it with: chmod 644 \"$CONFIG_FILE\""
            exit 1
            ;;
    esac
    if [ ! -r "$CONFIG_FILE" ]; then
        echo "Error: $CONFIG_FILE is not readable by the current user."
        exit 1
    fi
    # shellcheck disable=SC1090
    load_config_file "$CONFIG_FILE"
    EXISTING_INSTALL=true
fi
if [ -f "$SECRETS_FILE" ]; then
    SECRETS_OWNER_UID="$(file_owner_uid "$SECRETS_FILE")"
    if [ "$SECRETS_OWNER_UID" != "$CURRENT_UID" ]; then
        echo "Error: $SECRETS_FILE is not owned by the current user."
        echo "Repair it with: sudo chown \"$(id -un):$(id -gn)\" \"$SECRETS_FILE\""
        exit 1
    fi
    SECRETS_MODE="$(file_mode "$SECRETS_FILE")"
    if [ "$SECRETS_MODE" != "600" ]; then
        echo "Error: $SECRETS_FILE must have mode 0600, found 0$SECRETS_MODE."
        echo "Repair it with: chmod 600 \"$SECRETS_FILE\""
        exit 1
    fi
    if [ ! -r "$SECRETS_FILE" ]; then
        echo "Error: $SECRETS_FILE is not readable by the current user."
        echo "Repair it with: chmod 600 \"$SECRETS_FILE\""
        exit 1
    fi
    load_config_file "$SECRETS_FILE"
fi

WS_PORT="${HERDR_RELAY_PORT:-8375}"

# --- Log directory (matches relay's _get_log_dir) ---

if [ -n "${HERDR_LOG_DIR:-}" ]; then
    LOG_DIR="$HERDR_LOG_DIR"
elif [ "$OS" = "macos" ]; then
    LOG_DIR="$HOME/Library/Logs/herdr-remote"
elif [ -d "/var/log" ] && [ -w "/var/log" ]; then
    LOG_DIR="/var/log/herdr-remote"
else
    LOG_DIR="$HOME/.local/state/herdr-remote/log"
fi
mkdir -p "$LOG_DIR"

# --- Detect binaries ---

find_binary() {
    local name="$1"
    local found=""

    # 1. Already in PATH
    found="$(command -v "$name" 2>/dev/null || true)"
    [ -n "$found" ] && echo "$found" && return

    # 2. Homebrew (macOS Apple Silicon + Intel)
    for prefix in /opt/homebrew/bin /usr/local/bin; do
        [ -x "$prefix/$name" ] && echo "$prefix/$name" && return
    done

    # 3. Cargo
    [ -x "$HOME/.cargo/bin/$name" ] && echo "$HOME/.cargo/bin/$name" && return

    # 4. Common locations
    for dir in "$HOME/.local/bin" "$HOME/bin" /usr/bin; do
        [ -x "$dir/$name" ] && echo "$dir/$name" && return
    done

    echo ""
}

telegram_service_file() {
    if [ "$OS" = "macos" ]; then
        printf '%s\n' "$HOME/Library/LaunchAgents/$LABEL_TELEGRAM.plist"
    else
        printf '%s\n' "$HOME/.config/systemd/user/herdr-telegram.service"
    fi
}

telegram_service_exists() {
    [ -f "$(telegram_service_file)" ]
}

stop_telegram_service() {
    if [ "$OS" = "macos" ]; then
        launchctl bootout "gui/$(id -u)/$LABEL_TELEGRAM" 2>/dev/null || true
    else
        systemctl --user stop herdr-telegram.service 2>/dev/null || true
    fi
}

restart_existing_telegram_service() {
    telegram_service_exists || return 0
    if [ "$OS" = "macos" ]; then
        launchctl bootstrap "gui/$(id -u)" "$(telegram_service_file)" 2>/dev/null || true
    else
        systemctl --user daemon-reload 2>/dev/null || true
        systemctl --user start herdr-telegram.service 2>/dev/null || true
    fi
}

remove_telegram_service() {
    stop_telegram_service
    if [ "$OS" = "macos" ]; then
        rm -f "$(telegram_service_file)"
    else
        systemctl --user disable herdr-telegram.service 2>/dev/null || true
        rm -f "$(telegram_service_file)"
        systemctl --user daemon-reload
    fi
}

remove_managed_tunnel_service() {
    if [ "$OS" = "macos" ]; then
        launchctl bootout "gui/$(id -u)/$LABEL_TUNNEL" 2>/dev/null || true
        rm -f "$HOME/Library/LaunchAgents/$LABEL_TUNNEL.plist"
    else
        systemctl --user stop herdr-tunnel.service 2>/dev/null || true
        systemctl --user disable herdr-tunnel.service 2>/dev/null || true
        rm -f "$HOME/.config/systemd/user/herdr-tunnel.service"
        systemctl --user daemon-reload
    fi
}

telegram_api() {
    local method="$1"
    shift
    curl -fsS --max-time 20 -X POST --config - "$@" <<EOF
url = "https://api.telegram.org/bot${TELEGRAM_TOKEN}/${method}"
EOF
}

validate_telegram_token() {
    local response
    response="$(telegram_api getMe 2>/dev/null)" || return 1
    TELEGRAM_USERNAME="$(printf '%s' "$response" | python3 -c '
import json, sys
data = json.load(sys.stdin)
result = data.get("result", {}) if data.get("ok") else {}
print(result.get("username", ""))
' 2>/dev/null)"
    [ -n "$TELEGRAM_USERNAME" ]
}

discover_telegram_chat() {
    local response choices count pick selected
    response="$(telegram_api getUpdates --data-urlencode "timeout=10" 2>/dev/null)" || return 1
    choices="$(printf '%s' "$response" | python3 -c '
import json, sys
data = json.load(sys.stdin)
seen = set()
for update in data.get("result", []):
    message = update.get("message") or update.get("channel_post") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None or chat_id in seen:
        continue
    seen.add(chat_id)
    kind = str(chat.get("type", "unknown"))
    label = chat.get("title") or " ".join(
        part for part in (chat.get("first_name", ""), chat.get("last_name", "")) if part
    ) or str(chat_id)
    label = str(label).replace("\t", " ").replace("\n", " ")
    print(f"{chat_id}\t{kind}\t{label}")
' 2>/dev/null)" || return 1
    [ -n "$choices" ] || return 1

    echo ""
    echo "  Chats that recently contacted @$TELEGRAM_USERNAME:"
    printf '%s\n' "$choices" | while IFS=$'\t' read -r chat_id chat_type chat_label; do
        printf '    %s) %s (%s, %s)\n' "$(( ${chat_number:-0} + 1 ))" "$chat_label" "$chat_type" "$chat_id"
        chat_number="$(( ${chat_number:-0} + 1 ))"
    done

    count="$(printf '%s\n' "$choices" | sed '/^$/d' | wc -l | tr -d ' ')"
    if [ "$count" = "1" ]; then
        pick=1
    else
        read -p "  Select chat [1-$count]: " pick
    fi
    [[ "$pick" =~ ^[0-9]+$ ]] || return 1
    [ "$pick" -ge 1 ] && [ "$pick" -le "$count" ] || return 1
    selected="$(printf '%s\n' "$choices" | sed -n "${pick}p")"
    TELEGRAM_CHAT_ID="$(printf '%s' "$selected" | cut -f1)"
    TELEGRAM_CHAT_TYPE="$(printf '%s' "$selected" | cut -f2)"
}

read_manual_chat_id() {
    local entered
    read -p "  Chat ID (private or negative group ID): " entered
    [[ "$entered" =~ ^-?[0-9]+$ ]] || {
        echo "  Error: Chat ID must be a signed integer."
        return 1
    }
    TELEGRAM_CHAT_ID="$entered"
    TELEGRAM_CHAT_TYPE="unknown"
}

send_telegram_test() {
    telegram_api sendMessage \
        --data-urlencode "chat_id=$TELEGRAM_CHAT_ID" \
        --data-urlencode "text=herdr-remote Telegram service configured successfully." \
        >/dev/null 2>&1
}

generate_relay_token() {
    python3 -c 'import secrets; print(secrets.token_hex(32))'
}

UV_PATH="$(find_binary uv)"
HERDR_PATH="$(find_binary herdr)"
HERDR_PUSH_PATH="$(find_binary herdr-push)"
if [ "${HERDR_INSTALL_SKIP_CLOUDFLARED:-0}" = "1" ]; then
    CLOUDFLARED_PATH=""
else
    CLOUDFLARED_PATH="$(find_binary cloudflared)"
fi

echo "herdr-remote relay installer"
echo "============================"
echo ""
echo "  OS:          $OS"
echo "  uv:          ${UV_PATH:-NOT FOUND}"
echo "  herdr:       ${HERDR_PATH:-NOT FOUND}"
echo "  herdr-push:  ${HERDR_PUSH_PATH:-NOT FOUND}"
echo "  cloudflared: ${CLOUDFLARED_PATH:-NOT FOUND}"
echo "  relay:       $SCRIPT_DIR/herdr_relay.py"
echo "  config:      $CONFIG_FILE"
echo "  logs:        $LOG_DIR/"
echo "  port:        $WS_PORT"
echo ""

if [ -z "$UV_PATH" ]; then
    echo "Error: uv not found."
    echo "Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

if [ -z "$HERDR_PATH" ]; then
    echo "Warning: herdr binary not found. The relay needs it to poll agents."
    echo "Install options:"
    echo "  brew install herdr"
    echo "  cargo install herdr"
    echo "  curl -fsSL https://herdr.dev/install.sh | sh"
    echo ""
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]] || exit 1
fi

# --- Handle --uninstall ---

# --- Relay authentication ---

RELAY_TOKEN="${HERDR_RELAY_TOKEN:-}"
if [ -z "$RELAY_TOKEN" ]; then
    echo "Relay authentication"
    echo "--------------------"
    if [ "$EXISTING_INSTALL" = true ]; then
        read -p "  Secure the existing relay with a token? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            RELAY_TOKEN="$(generate_relay_token)"
        fi
    else
        read -p "  Secure the relay with an access token? [Y/n] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            RELAY_TOKEN="$(generate_relay_token)"
        fi
    fi
fi

if [ -n "$RELAY_TOKEN" ] && [[ ! "$RELAY_TOKEN" =~ ^[A-Za-z0-9_-]{16,128}$ ]]; then
    echo "Error: Managed relay tokens must contain 16-128 URL-safe characters."
    exit 1
fi

HERDR_RELAY_TOKEN="$RELAY_TOKEN"
HERDR_RELAY="ws://127.0.0.1:$WS_PORT"
[ -n "$HERDR_RELAY_TOKEN" ] && HERDR_RELAY="$HERDR_RELAY?token=$HERDR_RELAY_TOKEN"

# --- Telegram configuration ---

TELEGRAM_TOKEN="${HERDR_TG_TOKEN:-}"
TELEGRAM_CHAT_ID="${HERDR_TG_CHAT_ID:-}"
TELEGRAM_CHAT_TYPE="${HERDR_TG_CHAT_TYPE:-unknown}"
TELEGRAM_USERNAME="${HERDR_TG_USERNAME:-}"
TELEGRAM_ENABLED=false
TELEGRAM_SERVICE_WAS_PRESENT=false
telegram_service_exists && TELEGRAM_SERVICE_WAS_PRESENT=true
TELEGRAM_WAS_ENABLED=false
if [ "${HERDR_TG_ENABLED:-false}" = "true" ] || [ "$TELEGRAM_SERVICE_WAS_PRESENT" = true ]; then
    TELEGRAM_WAS_ENABLED=true
fi

echo ""
echo "Telegram bot setup"
echo "------------------"
if [ -n "$TELEGRAM_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
    [ -n "$TELEGRAM_USERNAME" ] && echo "  Existing bot: @$TELEGRAM_USERNAME"
    echo "  Existing destination: $TELEGRAM_CHAT_ID"
fi
if [ "$TELEGRAM_WAS_ENABLED" = true ]; then
    read -p "  Keep Telegram service enabled? [Y/n] " -n 1 -r
    echo
    [[ ! $REPLY =~ ^[Nn]$ ]] && TELEGRAM_ENABLED=true
else
    read -p "  Enable Telegram bot service? [y/N] " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]] && TELEGRAM_ENABLED=true
fi

if [ "$TELEGRAM_ENABLED" = true ]; then
    ORIGINAL_TELEGRAM_TOKEN="$TELEGRAM_TOKEN"
    ORIGINAL_TELEGRAM_CHAT_ID="$TELEGRAM_CHAT_ID"
    TOKEN_CHANGED=false

    if [ -n "$TELEGRAM_TOKEN" ]; then
        read -p "  Keep the existing BotFather token? [Y/n] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Nn]$ ]]; then
            read -p "  BotFather token: " -s TELEGRAM_TOKEN
            echo
            TOKEN_CHANGED=true
        fi
    else
        echo "  Create a bot with @BotFather using /newbot, then paste its token."
        read -p "  BotFather token: " -s TELEGRAM_TOKEN
        echo
        TOKEN_CHANGED=true
    fi

    if [[ ! "$TELEGRAM_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
        echo "  Error: Invalid BotFather token format."
        exit 1
    fi

    echo "  Validating bot token..."
    if ! validate_telegram_token; then
        echo "  Error: Telegram rejected the bot token or is unreachable."
        exit 1
    fi
    echo "  [ok] Connected as @$TELEGRAM_USERNAME"

    KEEP_CHAT=false
    if [ "$TOKEN_CHANGED" = false ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
        read -p "  Keep destination $TELEGRAM_CHAT_ID? [Y/n] " -n 1 -r
        echo
        [[ ! $REPLY =~ ^[Nn]$ ]] && KEEP_CHAT=true
    fi

    if [ "$KEEP_CHAT" = false ]; then
        [ "$TELEGRAM_SERVICE_WAS_PRESENT" = true ] && stop_telegram_service
        echo ""
        echo "  Open @$TELEGRAM_USERNAME in Telegram and send /start."
        echo "  For a group, add the bot and send /start@$TELEGRAM_USERNAME."
        read -p "  Press Enter after sending /start... " -r
        if ! discover_telegram_chat; then
            echo "  No unambiguous recent chat found; enter the Chat ID manually."
            if ! read_manual_chat_id; then
                [ "$TELEGRAM_SERVICE_WAS_PRESENT" = true ] && restart_existing_telegram_service
                exit 1
            fi
        fi
    fi

    [[ "$TELEGRAM_CHAT_ID" =~ ^-?[0-9]+$ ]] || {
        echo "  Error: Chat ID must be a signed integer."
        [ "$TELEGRAM_SERVICE_WAS_PRESENT" = true ] && restart_existing_telegram_service
        exit 1
    }

    read -p "  Send a test message to $TELEGRAM_CHAT_ID? [Y/n] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        if send_telegram_test; then
            echo "  [ok] Test message delivered"
        else
            echo "  Error: Telegram could not deliver to that chat."
            TELEGRAM_TOKEN="$ORIGINAL_TELEGRAM_TOKEN"
            TELEGRAM_CHAT_ID="$ORIGINAL_TELEGRAM_CHAT_ID"
            [ "$TELEGRAM_SERVICE_WAS_PRESENT" = true ] && restart_existing_telegram_service
            exit 1
        fi
    fi

    HERDR_TG_TOKEN="$TELEGRAM_TOKEN"
    HERDR_TG_CHAT_ID="$TELEGRAM_CHAT_ID"
    HERDR_TG_CHAT_TYPE="$TELEGRAM_CHAT_TYPE"
    HERDR_TG_USERNAME="$TELEGRAM_USERNAME"
fi

# --- Cloudflared check and install ---

TUNNEL_MODE="none"

# HERDR_TUNNEL_MODE=aws (set by hand in config.env — see infra/aws-tunnel/)
# replaces the whole interactive Cloudflare flow below with the reverse SSH
# tunnel service. It was already sourced from $CONFIG_FILE at the top of
# this script if present.
if [ "${HERDR_TUNNEL_MODE:-}" = "aws" ]; then
    TUNNEL_MODE="aws"
    echo "AWS reverse tunnel"
    echo "-------------------"
    echo "  HERDR_TUNNEL_MODE=aws in $CONFIG_FILE — installing the reverse"
    echo "  SSH tunnel service instead of Cloudflare Tunnel."

    if [ -z "$HERDR_RELAY_TOKEN" ]; then
        echo ""
        echo "  Error: the AWS reverse tunnel requires HERDR_RELAY_TOKEN."
        echo "  Unlike the Cloudflare path, this one publishes the relay on the"
        echo "  public internet over HTTPS, and the relay grants whoever reaches"
        echo "  it full control of your agents — read output, send keys, and"
        echo "  trust all tools for a blocked agent. A token is mandatory here"
        echo "  and there is no way to skip it."
        echo ""
        echo "  Re-run this installer and accept the token prompt, or set"
        echo "  HERDR_RELAY_TOKEN in the environment before running it."
        exit 1
    fi

    [ -n "${HERDR_AWS_HOST:-}" ] || echo "  WARNING: HERDR_AWS_HOST is not set — the tunnel will fail to start."
    echo "  See infra/aws-tunnel/README.md for the EC2-side setup."
    echo ""
elif [ -z "$CLOUDFLARED_PATH" ]; then
    echo "Cloudflare tunnel"
    echo "-----------------"
    echo "  cloudflared not found."
    echo ""
    read -p "  Install cloudflared? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if [ "$OS" = "macos" ] && command -v brew >/dev/null 2>&1; then
            echo "  Running: brew install cloudflared"
            brew install cloudflared
        else
            # cloudflared release assets use Go arch names (amd64/arm64/...),
            # not uname -m output (x86_64/aarch64/...). Linux assets are bare
            # binaries; macOS assets are .tgz archives.
            case "$(uname -m)" in
                x86_64)        CF_ARCH="amd64" ;;
                aarch64|arm64) CF_ARCH="arm64" ;;
                armv7l|armv6l) CF_ARCH="arm" ;;
                i686|i386)     CF_ARCH="386" ;;
                *)             CF_ARCH="$(uname -m)" ;;
            esac
            CF_OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
            CF_ASSET="cloudflared-$CF_OS-$CF_ARCH"
            [ "$CF_OS" = "darwin" ] && CF_ASSET="$CF_ASSET.tgz"
            echo "  Running: curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/$CF_ASSET -o /tmp/$CF_ASSET"
            curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/$CF_ASSET" -o "/tmp/$CF_ASSET"
            if [ "$CF_OS" = "darwin" ]; then
                tar -xzf "/tmp/$CF_ASSET" -C /tmp
                rm -f "/tmp/$CF_ASSET"
            else
                mv "/tmp/$CF_ASSET" /tmp/cloudflared
            fi
            chmod +x /tmp/cloudflared
            if [ -w /usr/local/bin ]; then
                mv /tmp/cloudflared /usr/local/bin/cloudflared
            else
                mkdir -p "$HOME/.local/bin"
                mv /tmp/cloudflared "$HOME/.local/bin/cloudflared"
            fi
        fi
        CLOUDFLARED_PATH="$(find_binary cloudflared)"
        if [ -n "$CLOUDFLARED_PATH" ]; then
            echo "  Installed: $CLOUDFLARED_PATH"
        else
            echo "  Warning: Install succeeded but cloudflared not found in PATH."
        fi
    else
        echo "  Skipping tunnel setup (local access only)."
        echo ""
    fi
fi

# --- Tunnel configuration ---

if [ "$TUNNEL_MODE" != "aws" ] && [ -n "$CLOUDFLARED_PATH" ]; then
    echo ""
    echo "Cloudflare tunnel setup"
    echo "-----------------------"

    # Check if cloudflared is authenticated
    CF_CERT="$HOME/.cloudflared/cert.pem"
    CF_AUTHENTICATED=false

    if [ -f "$CF_CERT" ]; then
        CF_AUTHENTICATED=true
        echo "  Auth: logged in (cert found)"
    elif "$CLOUDFLARED_PATH" tunnel list >/dev/null 2>&1; then
        CF_AUTHENTICATED=true
        echo "  Auth: logged in"
    else
        echo "  Auth: NOT logged in"
        echo ""
        echo "  Named tunnels require authentication."
        echo "  Temp tunnels work without auth."
        echo ""
        read -p "  Login to Cloudflare now? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo "  Opening browser for Cloudflare login..."
            "$CLOUDFLARED_PATH" tunnel login
            if [ -f "$CF_CERT" ]; then
                CF_AUTHENTICATED=true
                echo "  Login successful."
            else
                echo "  Login failed or was cancelled."
            fi
        fi
    fi

    echo ""
    echo "  1) named   — persistent URL via your domain (requires auth)"
    echo "  2) temp    — random trycloudflare.com URL (changes on restart)"
    echo "  3) none    — no tunnel, local access only"
    echo ""

    # Load existing config if available
    if [ -f "$CONFIG_FILE" ]; then
        source "$CONFIG_FILE"
        if [ -n "$HERDR_TUNNEL_MODE" ]; then
            echo "  Current config: mode=$HERDR_TUNNEL_MODE"
            [ -n "$HERDR_TUNNEL_NAME" ] && echo "                  tunnel=$HERDR_TUNNEL_NAME"
            [ -n "$HERDR_TUNNEL_HOSTNAME" ] && echo "                  hostname=$HERDR_TUNNEL_HOSTNAME"
            echo ""
        fi
    fi

    read -p "  Tunnel mode [1/2/3]: " -n 1 -r TUNNEL_CHOICE
    echo ""

    case "$TUNNEL_CHOICE" in
        1)
            if [ "$CF_AUTHENTICATED" = false ]; then
                echo ""
                echo "  Error: Named tunnels require authentication."
                echo "  Run: cloudflared tunnel login"
                echo ""
                read -p "  Fall back to temp tunnel? [Y/n] " -n 1 -r
                echo
                if [[ $REPLY =~ ^[Nn]$ ]]; then
                    TUNNEL_MODE="none"
                else
                    TUNNEL_MODE="temp"
                fi
            else
                TUNNEL_MODE="named"
                echo ""

                # Detect existing tunnels
                echo "  Checking existing tunnels..."
                TUNNEL_LIST=$("$CLOUDFLARED_PATH" tunnel list --output json 2>/dev/null || echo "[]")
                TUNNEL_COUNT=$(echo "$TUNNEL_LIST" | python3 -c '
import sys, json
print(len(json.loads(sys.stdin.read())))
' 2>/dev/null || echo "0")

                if [ "$TUNNEL_COUNT" -gt 0 ]; then
                    echo ""
                    echo "  Found $TUNNEL_COUNT existing tunnel(s):"
                    echo "$TUNNEL_LIST" | python3 -c '
import sys, json
tunnels = json.loads(sys.stdin.read())
for i, t in enumerate(tunnels, 1):
    name = t.get("name", "unnamed")
    tid = t.get("id", "?")[:8]
    conns = len(t.get("connections", []))
    status = "active" if conns > 0 else "inactive"
    print(f"    {i}) {name} (id: {tid}...) [{status}, {conns} conn(s)]")
' 2>/dev/null || "$CLOUDFLARED_PATH" tunnel list 2>/dev/null | head -10
                    echo ""

                    # Check if any tunnel is already installed as a system service
                    EXISTING_SERVICE=false
                    EXISTING_SERVICE_AUTO=false
                    CF_PLIST=""
                    if [ "$OS" = "macos" ]; then
                        # Check both user agents AND system daemons
                        CF_PLIST=$(find "$HOME/Library/LaunchAgents" /Library/LaunchDaemons /Library/LaunchAgents 2>/dev/null -name "*cloudflare*" -o -name "*cloudflared*" | head -1)
                        if [ -n "$CF_PLIST" ]; then
                            EXISTING_SERVICE=true
                            echo "  Found service: $CF_PLIST"
                        fi
                        # Also check if it's actually loaded (running)
                        if launchctl list 2>/dev/null | grep -qi "cloudflare"; then
                            EXISTING_SERVICE=true
                        fi
                        if sudo launchctl list 2>/dev/null | grep -qi "cloudflare"; then
                            EXISTING_SERVICE=true
                            # System daemon is always auto-start
                            EXISTING_SERVICE_AUTO=true
                        fi
                        # Check plist for RunAtLoad/KeepAlive
                        if [ -n "$CF_PLIST" ] && [ "$EXISTING_SERVICE_AUTO" = false ]; then
                            if grep -q "KeepAlive" "$CF_PLIST" 2>/dev/null || \
                               (grep -q "RunAtLoad" "$CF_PLIST" 2>/dev/null && grep -A1 "RunAtLoad" "$CF_PLIST" | grep -q "true"); then
                                EXISTING_SERVICE_AUTO=true
                            fi
                        fi
                    else
                        # Check systemd (user + system level)
                        if systemctl --user is-enabled cloudflared.service >/dev/null 2>&1 || \
                           systemctl is-enabled cloudflared.service >/dev/null 2>&1; then
                            EXISTING_SERVICE=true
                            EXISTING_SERVICE_AUTO=true
                        elif systemctl --user list-units 2>/dev/null | grep -qi cloudflared || \
                             systemctl list-units 2>/dev/null | grep -qi cloudflared; then
                            EXISTING_SERVICE=true
                        fi
                    fi

                    if [ "$EXISTING_SERVICE" = true ]; then
                        if [ "$EXISTING_SERVICE_AUTO" = true ]; then
                            echo "  A cloudflared service is already installed and set to start automatically."
                            echo ""
                            read -p "  Use existing service (skip tunnel install)? [Y/n] " -n 1 -r
                            echo
                            if [[ ! $REPLY =~ ^[Nn]$ ]]; then
                                echo "  Using existing cloudflared service."
                                # Still need tunnel name/hostname for config
                                TUNNEL_NAME=$(echo "$TUNNEL_LIST" | python3 -c '
import sys, json
t = json.loads(sys.stdin.read())
print(t[0]["name"] if t else "")
' 2>/dev/null)
                                TUNNEL_HOSTNAME="${HERDR_TUNNEL_HOSTNAME:-}"
                                if [ -z "$TUNNEL_HOSTNAME" ]; then
                                    read -p "  What hostname does it serve? (e.g. relay.yourdomain.com): " TUNNEL_HOSTNAME
                                fi
                                TUNNEL_MODE="named-external"
                                # skip our own tunnel service install later
                            fi
                        else
                            echo "  A cloudflared service exists but is NOT set to start automatically."
                            echo ""
                            read -p "  Make it automatic (start on boot)? [Y/n] " -n 1 -r
                            echo
                            if [[ ! $REPLY =~ ^[Nn]$ ]]; then
                                if [ "$OS" = "macos" ]; then
                                    if [ -n "$CF_PLIST" ]; then
                                        # Inject RunAtLoad if missing, or set to true
                                        if grep -q "RunAtLoad" "$CF_PLIST"; then
                                            sed -i '' 's|<false/>|<true/>|' "$CF_PLIST" 2>/dev/null
                                        else
                                            sed -i '' '/<dict>/a\
    <key>RunAtLoad</key>\
    <true/>' "$CF_PLIST" 2>/dev/null
                                        fi
                                        echo "  Updated plist to start on boot."
                                    fi
                                else
                                    systemctl --user enable cloudflared.service 2>/dev/null || \
                                        sudo systemctl enable cloudflared.service 2>/dev/null
                                    echo "  Enabled cloudflared service."
                                fi
                                TUNNEL_NAME=$(echo "$TUNNEL_LIST" | python3 -c '
import sys, json
t = json.loads(sys.stdin.read())
print(t[0]["name"] if t else "")
' 2>/dev/null)
                                TUNNEL_HOSTNAME="${HERDR_TUNNEL_HOSTNAME:-}"
                                if [ -z "$TUNNEL_HOSTNAME" ]; then
                                    read -p "  What hostname does it serve? (e.g. relay.yourdomain.com): " TUNNEL_HOSTNAME
                                fi
                                TUNNEL_MODE="named-external"
                            fi
                        fi
                    fi

                    # If not using external service, pick or create a tunnel
                    if [ "$TUNNEL_MODE" = "named" ]; then
                        echo ""
                        EXISTING_NAME="${HERDR_TUNNEL_NAME:-}"

                        if [ -n "$EXISTING_NAME" ]; then
                            read -p "  Tunnel name [$EXISTING_NAME]: " TUNNEL_NAME
                            TUNNEL_NAME="${TUNNEL_NAME:-$EXISTING_NAME}"
                        else
                            read -p "  Pick tunnel (number, name, or 'new' to create): " TUNNEL_PICK
                            # If it's a number, resolve to name
                            if [[ "$TUNNEL_PICK" =~ ^[0-9]+$ ]]; then
                                TUNNEL_NAME=$(echo "$TUNNEL_LIST" | PICK="$TUNNEL_PICK" python3 -c '
import sys, json, os
tunnels = json.loads(sys.stdin.read())
idx = int(os.environ["PICK"]) - 1
print(tunnels[idx]["name"] if 0 <= idx < len(tunnels) else "")
' 2>/dev/null)
                                if [ -z "$TUNNEL_NAME" ]; then
                                    echo "  Invalid selection."
                                    TUNNEL_NAME="$TUNNEL_PICK"
                                else
                                    echo "  Selected: $TUNNEL_NAME"
                                fi
                            else
                                TUNNEL_NAME="$TUNNEL_PICK"
                            fi
                        fi

                        # Create tunnel if requested
                        if [ "$TUNNEL_NAME" = "new" ]; then
                            read -p "  New tunnel name [herdr-relay]: " NEW_NAME
                            TUNNEL_NAME="${NEW_NAME:-herdr-relay}"
                            echo "  Creating tunnel '$TUNNEL_NAME'..."
                            "$CLOUDFLARED_PATH" tunnel create "$TUNNEL_NAME" || {
                                echo "  Error creating tunnel. It may already exist."
                                read -p "  Use existing '$TUNNEL_NAME'? [Y/n] " -n 1 -r
                                echo
                                [[ $REPLY =~ ^[Nn]$ ]] && exit 1
                            }
                        fi

                        EXISTING_HOST="${HERDR_TUNNEL_HOSTNAME:-}"
                        if [ -n "$EXISTING_HOST" ]; then
                            read -p "  Hostname [$EXISTING_HOST]: " TUNNEL_HOSTNAME
                            TUNNEL_HOSTNAME="${TUNNEL_HOSTNAME:-$EXISTING_HOST}"
                        else
                            read -p "  Hostname (e.g. relay.yourdomain.com): " TUNNEL_HOSTNAME
                        fi

                        if [ -z "$TUNNEL_NAME" ] || [ -z "$TUNNEL_HOSTNAME" ]; then
                            echo "  Error: Both tunnel name and hostname are required."
                            echo ""
                            read -p "  Fall back to temp tunnel? [Y/n] " -n 1 -r
                            echo
                            if [[ $REPLY =~ ^[Nn]$ ]]; then
                                TUNNEL_MODE="none"
                            else
                                TUNNEL_MODE="temp"
                            fi
                        else
                            # Route DNS if needed
                            echo "  Routing DNS: $TUNNEL_HOSTNAME -> $TUNNEL_NAME"
                            "$CLOUDFLARED_PATH" tunnel route dns "$TUNNEL_NAME" "$TUNNEL_HOSTNAME" 2>/dev/null || {
                                echo "  Note: DNS route may already exist. Continuing..."
                            }
                        fi
                    fi
                else
                    # No existing tunnels — create one
                    echo ""
                    echo "  No existing tunnels found. Creating one..."
                    read -p "  Tunnel name [herdr-relay]: " TUNNEL_NAME
                    TUNNEL_NAME="${TUNNEL_NAME:-herdr-relay}"
                    echo "  Creating tunnel '$TUNNEL_NAME'..."
                    "$CLOUDFLARED_PATH" tunnel create "$TUNNEL_NAME" || {
                        echo "  Error creating tunnel."
                        read -p "  Fall back to temp tunnel? [Y/n] " -n 1 -r
                        echo
                        if [[ $REPLY =~ ^[Nn]$ ]]; then
                            TUNNEL_MODE="none"
                        else
                            TUNNEL_MODE="temp"
                        fi
                        TUNNEL_NAME=""
                    }

                    if [ "$TUNNEL_MODE" = "named" ] && [ -n "$TUNNEL_NAME" ]; then
                        read -p "  Hostname (e.g. relay.yourdomain.com): " TUNNEL_HOSTNAME
                        if [ -z "$TUNNEL_HOSTNAME" ]; then
                            echo "  Error: Hostname required."
                            TUNNEL_MODE="temp"
                        else
                            echo "  Routing DNS: $TUNNEL_HOSTNAME -> $TUNNEL_NAME"
                            "$CLOUDFLARED_PATH" tunnel route dns "$TUNNEL_NAME" "$TUNNEL_HOSTNAME" 2>/dev/null || {
                                echo "  Note: DNS route may already exist. Continuing..."
                            }
                        fi
                    fi
                fi
            fi
            ;;
        2)
            TUNNEL_MODE="temp"
            ;;
        *)
            TUNNEL_MODE="none"
            ;;
    esac
fi

# --- Save config ---

# Normalize mode for config (named-external is still "named" at runtime)
CONFIG_TUNNEL_MODE="$TUNNEL_MODE"
[ "$CONFIG_TUNNEL_MODE" = "named-external" ] && CONFIG_TUNNEL_MODE="named"

HERDR_TG_TOKEN="${HERDR_TG_TOKEN:-$TELEGRAM_TOKEN}"
HERDR_TG_CHAT_ID="${HERDR_TG_CHAT_ID:-$TELEGRAM_CHAT_ID}"
HERDR_TG_CHAT_TYPE="${HERDR_TG_CHAT_TYPE:-$TELEGRAM_CHAT_TYPE}"
HERDR_TG_USERNAME="${HERDR_TG_USERNAME:-$TELEGRAM_USERNAME}"

[ -z "$HERDR_TG_TOKEN" ] || [[ "$HERDR_TG_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] || {
    echo "Error: Refusing to persist an invalid Telegram token."
    exit 1
}
[ -z "$HERDR_TG_CHAT_ID" ] || [[ "$HERDR_TG_CHAT_ID" =~ ^-?[0-9]+$ ]] || {
    echo "Error: Refusing to persist an invalid Telegram chat ID."
    exit 1
}

mkdir -p "$CONFIG_DIR"
CONFIG_TMP="$CONFIG_FILE.tmp.$$"
SECRETS_TMP="$SECRETS_FILE.tmp.$$"
cleanup_config_temps() {
    rm -f "$CONFIG_TMP" "$SECRETS_TMP"
}
trap cleanup_config_temps EXIT

(
    umask 022
    cat > "$CONFIG_TMP" <<EOF
# herdr-remote configuration (generated by install-service.sh)
HERDR_RELAY_PORT=$WS_PORT
HERDR_BIN=${HERDR_PATH:-herdr}
HERDR_LOG_DIR=$LOG_DIR
HERDR_TUNNEL_MODE=$CONFIG_TUNNEL_MODE
HERDR_TUNNEL_NAME=${TUNNEL_NAME:-}
HERDR_TUNNEL_HOSTNAME=${TUNNEL_HOSTNAME:-}
HERDR_RELAY_DIR=$SCRIPT_DIR
HERDR_UV_PATH=$UV_PATH
HERDR_CLOUDFLARED_PATH=${CLOUDFLARED_PATH:-}
HERDR_TG_ENABLED=$TELEGRAM_ENABLED
HERDR_TG_USERNAME=${HERDR_TG_USERNAME:-}
HERDR_TG_CHAT_TYPE=${HERDR_TG_CHAT_TYPE:-unknown}
HERDR_AWS_HOST=${HERDR_AWS_HOST:-}
HERDR_AWS_SSH_USER=${HERDR_AWS_SSH_USER:-}
HERDR_AWS_SSH_PORT=${HERDR_AWS_SSH_PORT:-}
HERDR_AWS_SSH_KEY=${HERDR_AWS_SSH_KEY:-}
HERDR_AWS_TUNNEL_PORT=${HERDR_AWS_TUNNEL_PORT:-}
EOF
)
chmod 644 "$CONFIG_TMP"
mv "$CONFIG_TMP" "$CONFIG_FILE"

(
    umask 077
    cat > "$SECRETS_TMP" <<EOF
# herdr-remote secrets (generated by install-service.sh)
HERDR_RELAY_TOKEN=${HERDR_RELAY_TOKEN:-}
HERDR_TG_TOKEN=${HERDR_TG_TOKEN:-}
HERDR_TG_CHAT_ID=${HERDR_TG_CHAT_ID:-}
HERDR_RELAY=$HERDR_RELAY
EOF
)
chmod 600 "$SECRETS_TMP"
mv "$SECRETS_TMP" "$SECRETS_FILE"
trap - EXIT

echo ""
echo "Config saved to $CONFIG_FILE"
echo "Secrets saved to $SECRETS_FILE (mode 0600)"
echo ""

# --- launchd helpers (macOS) ---

# Wait until a launchd label is fully torn down. Bootstrapping a label that is
# still unloading fails with the opaque "Bootstrap failed: 5: Input/output error".
# HERDR_INSTALL_SERVICE_DELAY=0 skips the wait entirely (used by the test suite).
wait_for_label_gone() {
    label="$1"
    [ "${HERDR_INSTALL_SERVICE_DELAY:-1}" = "0" ] && return 0
    for _i in $(seq 1 25); do
        launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 || return 0
        sleep 0.2
    done
    return 1
}

# Bootstrap with retries; launchd can still report the label as busy briefly
# even after `launchctl print` stops seeing it.
bootstrap_with_retry() {
    plist="$1"
    label="$2"
    _err=""
    for _i in 1 2 3 4 5; do
        if _err=$(launchctl bootstrap "gui/$(id -u)" "$plist" 2>&1); then
            return 0
        fi
        [ "${HERDR_INSTALL_SERVICE_DELAY:-1}" = "0" ] && break
        sleep 1
    done
    echo "  launchctl bootstrap failed for $label: $_err" >&2
    return 1
}

# --- Build PATH for the service ---

SERVICE_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
[ -d "$HOME/.cargo/bin" ] && SERVICE_PATH="$HOME/.cargo/bin:$SERVICE_PATH"
[ -d "$HOME/.local/bin" ] && SERVICE_PATH="$HOME/.local/bin:$SERVICE_PATH"

# --- Install relay service ---

# Check if port is already in use by another process
EXISTING_PID=$(lsof -iTCP:"$WS_PORT" -sTCP:LISTEN -t 2>/dev/null || true)
if [ -n "$EXISTING_PID" ]; then
    EXISTING_CMD=$(ps -p "$EXISTING_PID" -o command= 2>/dev/null || echo "unknown")
    echo "Port $WS_PORT is already in use:"
    echo "  PID $EXISTING_PID: $EXISTING_CMD"
    echo ""
    read -p "  Kill it and proceed? [Y/n] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        echo "  Aborting. Stop the existing process first."
        exit 1
    fi
    # If the port holder is our own managed relay, bootout the job first.
    # Killing the PID directly leaves launchd restarting it (KeepAlive) and
    # racing the bootstrap below.
    if [ "$OS" = "macos" ] && launchctl print "gui/$(id -u)/$LABEL_RELAY" >/dev/null 2>&1; then
        echo "  Stopping managed relay service..."
        launchctl bootout "gui/$(id -u)/$LABEL_RELAY" 2>/dev/null || true
        wait_for_label_gone "$LABEL_RELAY" || true
    fi

    # Try graceful shutdown first (SIGTERM)
    kill "$EXISTING_PID" 2>/dev/null
    for i in 1 2 3 4 5; do
        if ! kill -0 "$EXISTING_PID" 2>/dev/null; then
            break
        fi
        sleep 1
    done
    # Force kill if still alive
    if kill -0 "$EXISTING_PID" 2>/dev/null; then
        echo "  Process didn't exit gracefully, sending SIGKILL..."
        kill -9 "$EXISTING_PID" 2>/dev/null || true
        sleep 1
    fi
    # Final check on port (socket may linger briefly)
    for i in 1 2 3; do
        if ! lsof -iTCP:"$WS_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done
    if lsof -iTCP:"$WS_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "  Error: Port $WS_PORT still in use after killing PID $EXISTING_PID."
        echo "  Try manually: kill -9 $EXISTING_PID"
        exit 1
    fi
    echo "  Stopped."
fi

echo "Installing relay service..."

if [ "$OS" = "macos" ]; then
    PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL_RELAY.plist"
    mkdir -p "$HOME/Library/LaunchAgents"

    # Preserve the current plist so a failed install can be rolled back rather
    # than leaving the machine with no relay at all.
    PLIST_BACKUP=""
    if [ -f "$PLIST_PATH" ]; then
        PLIST_BACKUP="$PLIST_PATH.prev"
        cp "$PLIST_PATH" "$PLIST_BACKUP"
    fi

    launchctl bootout "gui/$(id -u)/$LABEL_RELAY" 2>/dev/null || true
    wait_for_label_gone "$LABEL_RELAY" || true

    cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL_RELAY</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-lc</string>
        <string>set -e; set -a; source "\$HOME/.config/herdr-remote/config.env"; source "\$HOME/.config/herdr-remote/secrets.env"; set +a; exec "\$HERDR_UV_PATH" run "\$HERDR_RELAY_DIR/herdr_relay.py"</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/relay-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/relay-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$SERVICE_PATH</string>
    </dict>
</dict>
</plist>
EOF

    if ! bootstrap_with_retry "$PLIST_PATH" "$LABEL_RELAY"; then
        echo "  ERROR: could not start the relay service." >&2
        if [ -n "$PLIST_BACKUP" ] && [ -f "$PLIST_BACKUP" ]; then
            echo "  Restoring previous relay service..." >&2
            cp "$PLIST_BACKUP" "$PLIST_PATH"
            wait_for_label_gone "$LABEL_RELAY" || true
            if bootstrap_with_retry "$PLIST_PATH" "$LABEL_RELAY"; then
                echo "  Previous relay restored and running." >&2
            else
                echo "  Could not restore the previous relay either." >&2
                echo "  Start it manually with:" >&2
                echo "    launchctl bootstrap gui/$(id -u) $PLIST_PATH" >&2
            fi
        fi
        exit 1
    fi
    rm -f "$PLIST_PATH.prev"

else
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"

    systemctl --user stop herdr-relay.service 2>/dev/null || true

    cat > "$UNIT_DIR/herdr-relay.service" <<EOF
[Unit]
Description=herdr-remote relay
After=network.target

[Service]
ExecStart=$UV_PATH run $SCRIPT_DIR/herdr_relay.py
WorkingDirectory=$SCRIPT_DIR
Restart=always
RestartSec=5
Environment=PATH=$SERVICE_PATH
EnvironmentFile=$CONFIG_FILE
EnvironmentFile=$SECRETS_FILE

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable herdr-relay.service
    systemctl --user start herdr-relay.service
fi

echo "  Relay service installed."

# --- Install Telegram service (if configured) ---

if [ "$TELEGRAM_ENABLED" = true ]; then
    echo "Installing Telegram service..."
    stop_telegram_service
    sleep "${HERDR_INSTALL_SERVICE_DELAY:-1}"
    if command -v pgrep >/dev/null 2>&1; then
        UNMANAGED_TELEGRAM_PIDS="$(pgrep -f 'herdr_telegram\.py' 2>/dev/null || true)"
        if [ -n "$UNMANAGED_TELEGRAM_PIDS" ]; then
            echo "  Error: Another Telegram bot process is already running."
            echo "  Stop the foreground herdr_telegram.py process, then rerun this installer."
            echo "  Conflicting process IDs: $(printf '%s' "$UNMANAGED_TELEGRAM_PIDS" | tr '\n' ' ')"
            exit 1
        fi
    fi

    if [ "$OS" = "macos" ]; then
        PLIST_TELEGRAM="$(telegram_service_file)"
        mkdir -p "$HOME/Library/LaunchAgents"
        cat > "$PLIST_TELEGRAM" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL_TELEGRAM</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-lc</string>
        <string>set -e; set -a; source "\$HOME/.config/herdr-remote/config.env"; source "\$HOME/.config/herdr-remote/secrets.env"; set +a; exec "\$HERDR_UV_PATH" run "\$HERDR_RELAY_DIR/herdr_telegram.py"</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/telegram-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/telegram-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$SERVICE_PATH</string>
    </dict>
</dict>
</plist>
EOF
        wait_for_label_gone "$LABEL_TELEGRAM" || true
        bootstrap_with_retry "$PLIST_TELEGRAM" "$LABEL_TELEGRAM" || true
    else
        UNIT_TELEGRAM="$UNIT_DIR/herdr-telegram.service"
        cat > "$UNIT_TELEGRAM" <<EOF
[Unit]
Description=herdr-remote Telegram bot
After=network-online.target herdr-relay.service
Wants=network-online.target herdr-relay.service

[Service]
ExecStart=$UV_PATH run $SCRIPT_DIR/herdr_telegram.py
WorkingDirectory=$SCRIPT_DIR
Restart=always
RestartSec=5
Environment=PATH=$SERVICE_PATH
EnvironmentFile=$CONFIG_FILE
EnvironmentFile=$SECRETS_FILE

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable herdr-telegram.service
        systemctl --user start herdr-telegram.service
    fi
    echo "  Telegram service installed."
else
    if telegram_service_exists; then
        echo "Removing disabled Telegram service..."
        remove_telegram_service
        echo "  Telegram service removed; credentials preserved."
    fi
fi

# --- Install tunnel service (if configured) ---

if [ "$TUNNEL_MODE" = "none" ] || [ "$TUNNEL_MODE" = "named-external" ]; then
    remove_managed_tunnel_service
    if [ "$TUNNEL_MODE" = "named-external" ]; then
        echo "  Tunnel: using existing cloudflared service (not managed by herdr-remote)."
        echo "  Hostname: ${TUNNEL_HOSTNAME:-unknown}"
    fi
elif [ "$TUNNEL_MODE" = "aws" ]; then
    if [ -z "$HERDR_RELAY_TOKEN" ]; then
        echo "Error: refusing to install the AWS reverse tunnel service without"
        echo "  HERDR_RELAY_TOKEN. This tunnel exposes the relay publicly over the"
        echo "  internet, so a token is mandatory and cannot be skipped."
        exit 1
    fi

    echo "Installing AWS reverse tunnel service..."
    TUNNEL_SCRIPT="$SCRIPT_DIR/tunnel-aws.sh"

    if [ "$OS" = "macos" ]; then
        PLIST_TUNNEL="$HOME/Library/LaunchAgents/$LABEL_TUNNEL.plist"

        launchctl bootout "gui/$(id -u)/$LABEL_TUNNEL" 2>/dev/null || true
        sleep 1

        cat > "$PLIST_TUNNEL" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL_TUNNEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$TUNNEL_SCRIPT</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/tunnel-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/tunnel-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$SERVICE_PATH</string>
    </dict>
</dict>
</plist>
EOF

        wait_for_label_gone "$LABEL_TUNNEL" || true
        bootstrap_with_retry "$PLIST_TUNNEL" "$LABEL_TUNNEL" || true
    else
        systemctl --user stop herdr-tunnel.service 2>/dev/null || true

        cat > "$UNIT_DIR/herdr-tunnel.service" <<EOF
[Unit]
Description=herdr-remote AWS reverse tunnel
After=herdr-relay.service network-online.target
Wants=network-online.target
Requires=herdr-relay.service

[Service]
ExecStart=$TUNNEL_SCRIPT
Restart=always
RestartSec=10
Environment=PATH=$SERVICE_PATH

[Install]
WantedBy=default.target
EOF

        systemctl --user daemon-reload
        systemctl --user enable herdr-tunnel.service
        systemctl --user start herdr-tunnel.service
    fi

    echo "  Tunnel service installed."
    echo "  URL: https://${HERDR_AWS_HOST:-<HERDR_AWS_HOST not set>}"
elif [ "$TUNNEL_MODE" != "none" ] && [ -n "$CLOUDFLARED_PATH" ]; then
    echo "Installing tunnel service (mode: $TUNNEL_MODE)..."

    if [ "$TUNNEL_MODE" = "named" ]; then
        TUNNEL_ARGS="tunnel run $TUNNEL_NAME"
        # Write ingress config for named tunnel
        CF_CONFIG_DIR="$HOME/.cloudflared"
        mkdir -p "$CF_CONFIG_DIR"
        CF_CONFIG="$CF_CONFIG_DIR/config-herdr.yml"
        # cloudflared writes credentials to <UUID>.json, not <name>.json —
        # resolve the tunnel's UUID so the config points at a file that exists.
        TUNNEL_UUID=$("$CLOUDFLARED_PATH" tunnel list --output json 2>/dev/null \
            | NAME="$TUNNEL_NAME" python3 -c '
import json, os, sys
name = os.environ["NAME"]
try:
    for t in json.load(sys.stdin):
        if t.get("name") == name:
            print(t.get("id", ""))
            break
except Exception:
    pass
')
        if [ -n "$TUNNEL_UUID" ] && [ -f "$CF_CONFIG_DIR/${TUNNEL_UUID}.json" ]; then
            CF_CREDS="$CF_CONFIG_DIR/${TUNNEL_UUID}.json"
        else
            CF_CREDS="$CF_CONFIG_DIR/${TUNNEL_NAME}.json"
            echo "  WARNING: could not resolve credentials file for tunnel '$TUNNEL_NAME'." >&2
            echo "           Falling back to $CF_CREDS (tunnel may fail to start)." >&2
        fi
        cat > "$CF_CONFIG" <<EOF
tunnel: $TUNNEL_NAME
credentials-file: $CF_CREDS

ingress:
  - hostname: $TUNNEL_HOSTNAME
    service: http://localhost:$WS_PORT
  - service: http_status:404
EOF
        TUNNEL_ARGS="tunnel --config $CF_CONFIG run $TUNNEL_NAME"
        echo "  Tunnel config: $CF_CONFIG"
    else
        TUNNEL_ARGS="tunnel --url http://localhost:$WS_PORT"
    fi

    if [ "$OS" = "macos" ]; then
        PLIST_TUNNEL="$HOME/Library/LaunchAgents/$LABEL_TUNNEL.plist"

        launchctl bootout "gui/$(id -u)/$LABEL_TUNNEL" 2>/dev/null || true
        sleep 1

        # Build ProgramArguments array
        ARGS_XML=""
        for arg in $TUNNEL_ARGS; do
            ARGS_XML="$ARGS_XML        <string>$arg</string>
"
        done

        cat > "$PLIST_TUNNEL" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL_TUNNEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$CLOUDFLARED_PATH</string>
$ARGS_XML    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/tunnel-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/tunnel-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$SERVICE_PATH</string>
    </dict>
</dict>
</plist>
EOF

        wait_for_label_gone "$LABEL_TUNNEL" || true
        bootstrap_with_retry "$PLIST_TUNNEL" "$LABEL_TUNNEL" || true

    else
        systemctl --user stop herdr-tunnel.service 2>/dev/null || true

        cat > "$UNIT_DIR/herdr-tunnel.service" <<EOF
[Unit]
Description=herdr-remote Cloudflare tunnel
After=herdr-relay.service
Requires=herdr-relay.service

[Service]
ExecStart=$CLOUDFLARED_PATH $TUNNEL_ARGS
Restart=always
RestartSec=10
Environment=PATH=$SERVICE_PATH

[Install]
WantedBy=default.target
EOF

        systemctl --user daemon-reload
        systemctl --user enable herdr-tunnel.service
        systemctl --user start herdr-tunnel.service
    fi

    echo "  Tunnel service installed."

    if [ "$TUNNEL_MODE" = "temp" ]; then
        echo ""
        echo "  Temp tunnel URL will appear in: $LOG_DIR/tunnel-stderr.log"
        echo "  Run: grep trycloudflare $LOG_DIR/tunnel-stderr.log"
    else
        echo "  Named tunnel: wss://$TUNNEL_HOSTNAME"
    fi
fi

echo ""
echo "Services installed and started."
echo ""

# --- Install the operator-facing CLI wrapper ---
#
# relay/herdr-remote is the canonical start/stop/status wrapper. It is what the
# operator actually types, and it is the only thing that knows how to boot the
# launchd jobs out (a plain kill of the relay just gets restarted by KeepAlive).
# If it is not installed onto PATH, the operator falls back to whatever stale
# copy is already in ~/.local/bin - historically a pre-AWS, Cloudflare-only
# wrapper whose `stop` greps for a cloudflared process, finds none under AWS,
# kills only the relay, and reports "Stopped." while the public tunnel stays up.
# We symlink (not copy) so the installed CLI can never fall behind the versioned
# wrapper it points at, and warn loudly when a different file is already there.
install_cli_wrapper() {
    WRAPPER_SRC="$SCRIPT_DIR/herdr-remote"
    BIN_DIR="$HOME/.local/bin"
    WRAPPER_DEST="$BIN_DIR/herdr-remote"

    if [ ! -f "$WRAPPER_SRC" ]; then
        echo "  [warn] Canonical wrapper not found at $WRAPPER_SRC; skipping CLI install."
        return 0
    fi

    chmod +x "$WRAPPER_SRC" 2>/dev/null || true
    mkdir -p "$BIN_DIR"

    if [ -L "$WRAPPER_DEST" ] && [ "$(readlink "$WRAPPER_DEST")" = "$WRAPPER_SRC" ]; then
        echo "  [ok] CLI already linked: $WRAPPER_DEST -> $WRAPPER_SRC"
    else
        if [ -e "$WRAPPER_DEST" ] || [ -L "$WRAPPER_DEST" ]; then
            # Something is already here that is not our symlink. Warn if it
            # differs from the canonical wrapper (it almost always will - that
            # stale copy is the whole reason this step exists), and preserve it.
            if [ -L "$WRAPPER_DEST" ]; then
                echo "  [warn] Replacing an existing herdr-remote at $WRAPPER_DEST"
                echo "         (symlink -> $(readlink "$WRAPPER_DEST")) with a link to the canonical wrapper."
            elif cmp -s "$WRAPPER_DEST" "$WRAPPER_SRC"; then
                echo "  [note] $WRAPPER_DEST is an identical copy; converting it to a symlink so it stays in sync."
            else
                echo "  [warn] A DIFFERENT herdr-remote is already installed at $WRAPPER_DEST."
                echo "         It does not match the canonical wrapper in this repo and is very likely a"
                echo "         stale copy that mismanages the current tunnel backend (e.g. a Cloudflare-only"
                echo "         'stop' that leaves an AWS tunnel serving publicly). Replacing it with a"
                echo "         symlink to the versioned wrapper: $WRAPPER_SRC"
                BACKUP="$WRAPPER_DEST.replaced-by-installer"
                if cp -p "$WRAPPER_DEST" "$BACKUP" 2>/dev/null; then
                    echo "         Previous file backed up to: $BACKUP"
                fi
            fi
        fi
        ln -sf "$WRAPPER_SRC" "$WRAPPER_DEST"
        echo "  CLI installed: $WRAPPER_DEST -> $WRAPPER_SRC"
    fi

    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *)
            echo "  [note] $BIN_DIR is not on your PATH. Add it so 'herdr-remote' resolves, e.g.:"
            echo "         export PATH=\"$BIN_DIR:\$PATH\""
            ;;
    esac
}
install_cli_wrapper

echo ""

# --- Smoke test ---

echo "Running smoke test..."
sleep "${HERDR_INSTALL_SETTLE_SECONDS:-3}"

# 1. Check port is listening
if ! lsof -iTCP:"$WS_PORT" -sTCP:LISTEN >/dev/null 2>&1 && \
   ! ss -tlnp 2>/dev/null | grep -q ":$WS_PORT "; then
    echo ""
    echo "  FAIL: Port $WS_PORT is not listening after 3 seconds."
    echo "  Check logs: tail -20 $LOG_DIR/relay.log"
    exit 1
fi
echo "  [ok] Port $WS_PORT is listening"

# 2. WebSocket connect and receive agents broadcast
if [ "${HERDR_INSTALL_SKIP_WEBSOCKET_SMOKE:-0}" = "1" ]; then
    SMOKE_RESULT="ws_ok:skip"
else
    SMOKE_RESULT=$(WS_PORT="$WS_PORT" RELAY_TOKEN="${HERDR_RELAY_TOKEN:-}" python3 -c '
import asyncio, json, os, urllib.parse
async def test():
    port = os.environ["WS_PORT"]
    token = os.environ.get("RELAY_TOKEN", "")
    url = f"ws://127.0.0.1:{port}"
    if token:
        url += "?token=" + urllib.parse.quote(token, safe="")
    try:
        import websockets
    except ImportError:
        print("ws_ok:skip")
        return
    try:
        async with websockets.connect(url, open_timeout=5) as ws:
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(msg)
            if data.get("type") == "agents":
                agents = data.get("agents", [])
                print(f"ws_ok:agents:{len(agents)}")
            else:
                print(f"ws_ok:msg:{data.get('type', 'unknown')}")
    except Exception as e:
        print(f"ws_fail:{e}")
asyncio.run(test())
' 2>/dev/null || echo "ws_fail:python_error")
fi

case "$SMOKE_RESULT" in
    ws_ok:agents:*)
        COUNT="${SMOKE_RESULT##*:}"
        echo "  [ok] WebSocket connected, received agents broadcast ($COUNT agent(s))"
        ;;
    ws_ok:msg:*)
        TYPE="${SMOKE_RESULT##*:}"
        echo "  [ok] WebSocket connected, received message (type: $TYPE)"
        ;;
    ws_ok:skip)
        echo "  [ok] WebSocket connect skipped (websockets not importable outside relay env)"
        ;;
    ws_fail:*)
        ERR="${SMOKE_RESULT#ws_fail:}"
        echo "  FAIL: WebSocket test failed: $ERR"
        echo "  Check logs: $LOG_DIR/relay.log"
        exit 1
        ;;
esac

# 3. Check herdr can poll
if [ -n "$HERDR_PATH" ]; then
    if "$HERDR_PATH" pane list >/dev/null 2>&1; then
        echo "  [ok] herdr pane list works"
    else
        echo "  [warn] herdr pane list failed (tmux may not be running)"
    fi
fi

# 4. Check Telegram identity and managed service
if [ "$TELEGRAM_ENABLED" = true ]; then
    TELEGRAM_ACTIVE=false
    if [ "$OS" = "macos" ]; then
        launchctl print "gui/$(id -u)/$LABEL_TELEGRAM" >/dev/null 2>&1 && TELEGRAM_ACTIVE=true
    else
        systemctl --user is-active --quiet herdr-telegram.service && TELEGRAM_ACTIVE=true
    fi
    if [ "$TELEGRAM_ACTIVE" != true ]; then
        echo "  FAIL: Telegram service is not active."
        echo "  Check logs: $LOG_DIR/telegram-stderr.log"
        exit 1
    fi
    echo "  [ok] Telegram service is active"

    if ! validate_telegram_token; then
        echo "  FAIL: Telegram bot identity could not be verified."
        echo "  Check logs: $LOG_DIR/telegram-stderr.log"
        exit 1
    fi
    echo "  [ok] Telegram bot verified as @$TELEGRAM_USERNAME"
fi

# 5. Check tunnel is up (for named tunnels)
if [ "$TUNNEL_MODE" = "named" ] && [ -n "$TUNNEL_HOSTNAME" ]; then
    sleep 2
    if curl -s -o /dev/null -w "%{http_code}" "https://$TUNNEL_HOSTNAME" 2>/dev/null | grep -q "^[23]"; then
        echo "  [ok] Tunnel reachable at https://$TUNNEL_HOSTNAME"
    else
        echo "  [warn] Tunnel not reachable yet at https://$TUNNEL_HOSTNAME (may take a moment)"
    fi
elif [ "$TUNNEL_MODE" = "temp" ]; then
    sleep 3
    TUNNEL_URL=$(grep -o 'https://[^ ]*\.trycloudflare\.com' "$LOG_DIR/tunnel-stderr.log" 2>/dev/null | tail -1)
    if [ -n "$TUNNEL_URL" ]; then
        echo "  [ok] Temp tunnel active: $TUNNEL_URL"
        echo "       WebSocket: wss://$(echo "$TUNNEL_URL" | sed 's|https://||')"
    else
        echo "  [warn] Temp tunnel URL not found yet. Check: grep trycloudflare $LOG_DIR/tunnel-stderr.log"
    fi
fi

echo ""
echo "Smoke test complete."
echo ""
echo "=== Summary ==="
if [ -n "${HERDR_RELAY_TOKEN:-}" ]; then
    echo "  Relay:      running on :$WS_PORT, token protected"
else
    echo "  Relay:      running on :$WS_PORT, no token"
fi
if [ "$TELEGRAM_ENABLED" = true ]; then
    echo "  Telegram:   running as @$TELEGRAM_USERNAME"
    echo "  Destination: $TELEGRAM_CHAT_ID (${TELEGRAM_CHAT_TYPE:-unknown})"
else
    echo "  Telegram:   disabled"
fi
[ "$TUNNEL_MODE" != "none" ] && echo "  Tunnel:     $TUNNEL_MODE"
[ "$TUNNEL_MODE" = "named" ] && echo "  URL:        wss://$TUNNEL_HOSTNAME"
echo "  Logs:       $LOG_DIR/"
echo "  Config:     $CONFIG_FILE"
echo "  Secrets:    $SECRETS_FILE"
[ -e "$HOME/.local/bin/herdr-remote" ] && echo "  CLI:        $HOME/.local/bin/herdr-remote (start | stop | status | url | token)"
echo ""
echo "Commands:"
echo "  Relay log:    tail -f $LOG_DIR/relay.log"
[ "$TELEGRAM_ENABLED" = true ] && echo "  Telegram log: tail -f $LOG_DIR/telegram-stderr.log"
if [ "$OS" = "macos" ]; then
    echo "  Relay status:    launchctl print gui/$(id -u)/$LABEL_RELAY"
    echo "  Relay stop:      launchctl bootout gui/$(id -u)/$LABEL_RELAY"
    echo "  Relay start:     launchctl bootstrap gui/$(id -u) $HOME/Library/LaunchAgents/$LABEL_RELAY.plist"
    if [ "$TELEGRAM_ENABLED" = true ]; then
        echo "  Telegram status: launchctl print gui/$(id -u)/$LABEL_TELEGRAM"
        echo "  Telegram stop:   launchctl bootout gui/$(id -u)/$LABEL_TELEGRAM"
        echo "  Telegram start:  launchctl bootstrap gui/$(id -u) $HOME/Library/LaunchAgents/$LABEL_TELEGRAM.plist"
    fi
else
    echo "  Relay status:    systemctl --user status herdr-relay"
    echo "  Relay restart:   systemctl --user restart herdr-relay"
    if [ "$TELEGRAM_ENABLED" = true ]; then
        echo "  Telegram status: systemctl --user status herdr-telegram"
        echo "  Telegram restart: systemctl --user restart herdr-telegram"
    fi
fi
echo "  Uninstall: $0 --uninstall"
