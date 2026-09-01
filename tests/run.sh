#!/bin/sh
# tests/run.sh — tests for herdr-remote
PASS=0; FAIL=0
DIR="$(cd "$(dirname "$0")/.." && pwd)"

if command -v python3 >/dev/null 2>&1 && python3 -c "pass" >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1 && python -c "pass" >/dev/null 2>&1; then
    PYTHON=python
else
    echo "Python 3 is required"
    exit 1
fi

assert_eq() {
  if [ "$1" = "$2" ]; then PASS=$((PASS+1)); echo "  pass: $3"
  else FAIL=$((FAIL+1)); echo "  FAIL: $3 (expected '$2', got '$1')"; fi
}

echo "herdr-remote tests"
echo ""

# --- Relay ---
echo "=== Relay ==="
echo "1. relay syntax"
"$PYTHON" -c "import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))" "$DIR/relay/herdr_relay.py" 2>/dev/null
assert_eq "$?" "0" "herdr_relay.py parses"

echo "1b. relay behavior"
uv run --with 'python-telegram-bot>=21.0' --with 'websockets>=14.0' \
  python -m unittest discover -s "$DIR/tests" -p "test_*.py"
assert_eq "$?" "0" "relay behavior"

echo "2. PEP 723 metadata"
grep -q "requires-python" "$DIR/relay/herdr_relay.py"
assert_eq "$?" "0" "inline deps present"

echo "3. start.sh executable"
[ -x "$DIR/relay/start.sh" ]
assert_eq "$?" "0" "start.sh +x"

# --- Telegram ---
echo ""
echo "=== Telegram bot ==="
echo "4. telegram bot syntax"
"$PYTHON" -c "import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))" "$DIR/relay/herdr_telegram.py" 2>/dev/null
assert_eq "$?" "0" "herdr_telegram.py parses"

echo "5. telegram demo bot syntax"
"$PYTHON" -c "import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))" "$DIR/relay/herdr_telegram_demo.py" 2>/dev/null
assert_eq "$?" "0" "herdr_telegram_demo.py parses"

echo "6. telegram bot has all commands"
for cmd in cmd_start cmd_agents cmd_status cmd_read cmd_send cmd_reply cmd_trust cmd_interrupt; do
  grep -q "async def $cmd" "$DIR/relay/herdr_telegram.py" || { FAIL=$((FAIL+1)); echo "  FAIL: missing $cmd"; continue; }
done
PASS=$((PASS+1)); echo "  pass: all 8 commands present"

echo "7. telegram bot env vars documented"
grep -q "HERDR_TG_TOKEN" "$DIR/relay/herdr_telegram.py" && grep -q "HERDR_TG_CHAT_ID" "$DIR/relay/herdr_telegram.py"
assert_eq "$?" "0" "env vars referenced"

# --- TUI ---
echo ""
echo "=== TUI ==="
echo "8. TUI syntax"
"$PYTHON" -c "import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))" "$DIR/relay/herdr_tui.py" 2>/dev/null
assert_eq "$?" "0" "herdr_tui.py parses"

# --- Web app ---
echo ""
echo "=== Web app ==="
echo "9. web app key elements"
WEB="$DIR/web/index.html"
grep -q "WebSocket" "$WEB" && grep -q "theme" "$WEB" && grep -q "sendKey" "$WEB"
assert_eq "$?" "0" "has WebSocket, themes, keyboard"

echo "9b. web app has session selector"
grep -q 'id="sessionSelector"' "$WEB" && \
grep -q "session_switch" "$WEB"
assert_eq "$?" "0" "web app has session selector"

echo "9c. web app has no duplicate function declarations"
DUP_FUNCS=$(grep -oE '^[[:space:]]*function [A-Za-z0-9_]+\(' "$WEB" | grep -oE '[A-Za-z0-9_]+\(' | sort | uniq -d)
[ -z "$DUP_FUNCS" ]
assert_eq "$?" "0" "no duplicate function declarations"

echo "9d. web app behaviour in a real browser"
if uv run --with playwright python -c "import playwright" >/dev/null 2>&1; then
  uv run --with playwright python -m unittest discover -s "$DIR/tests" -p "test_web_*.py"
  assert_eq "$?" "0" "web app behaviour"
else
  PASS=$((PASS+1)); echo "  skip: playwright not available"
fi

echo "10. web app no hardcoded secrets"
! grep -q "c4a2385e" "$WEB" && ! grep -q "graffold" "$WEB"
assert_eq "$?" "0" "no secrets in web app"

# --- macOS app ---
echo ""
echo "=== macOS app ==="
echo "11. Swift sources parse"
if command -v swiftc >/dev/null 2>&1; then
  swiftc -parse "$DIR/herdi-mac/Sources/"*.swift 2>/dev/null && \
  swiftc -parse "$DIR/herdi-ios/Sources/"*.swift "$DIR/herdi-ios/Sources/Models/"*.swift "$DIR/herdi-ios/Sources/Services/"*.swift "$DIR/herdi-ios/Sources/Views/"*.swift 2>/dev/null
  assert_eq "$?" "0" "Swift clients parse"
else
  PASS=$((PASS+1)); echo "  skip: swiftc not available"
fi

echo "12. build.sh and dmg.sh present"
[ -x "$DIR/herdi-mac/build.sh" ] && [ -f "$DIR/herdi-mac/dmg.sh" ]
assert_eq "$?" "0" "build scripts present"

echo "13. updater points to correct repo"
grep -q "dcolinmorgan/herdr-remote" "$DIR/herdi-mac/Sources/Updater.swift"
assert_eq "$?" "0" "updater repo correct"

# --- Demo worker ---
echo ""
echo "=== Demo worker ==="
echo "14. demo worker syntax"
if [ -f "$DIR/demo-worker/src/index.js" ]; then
  node --check "$DIR/demo-worker/src/index.js" 2>/dev/null
  assert_eq "$?" "0" "demo worker parses"
else
  PASS=$((PASS+1)); echo "  skip: not present"
fi

# --- Integration ---
echo ""
echo "=== Integration ==="
echo "15. README links to herdr-demo.pages.dev"
grep -q "herdr-demo.pages.dev" "$DIR/README.md"
assert_eq "$?" "0" "demo URL correct"

echo "16. README links to herdr-push"
grep -q "dcolinmorgan/herdr-push" "$DIR/README.md"
assert_eq "$?" "0" "plugin link present"

echo "17. installer service behavior"
"$DIR/tests/install-service.sh"
assert_eq "$?" "0" "installer handles Telegram service lifecycle"

echo "18. LICENSE is AGPL"
grep -q "GNU AFFERO GENERAL PUBLIC LICENSE" "$DIR/LICENSE"
assert_eq "$?" "0" "AGPL license"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
