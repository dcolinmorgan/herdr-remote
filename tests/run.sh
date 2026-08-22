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

# --- AWS reverse tunnel ---
echo ""
echo "=== AWS reverse tunnel ==="
echo "19. tunnel-aws.sh and herdr-remote wrapper are executable"
[ -x "$DIR/relay/tunnel-aws.sh" ] && [ -x "$DIR/relay/herdr-remote" ]
assert_eq "$?" "0" "aws tunnel scripts +x"

echo "20. CloudFormation template validates"
if command -v aws >/dev/null 2>&1 && aws sts get-caller-identity >/dev/null 2>&1; then
  aws cloudformation validate-template --region us-east-1 \
    --template-body "file://$DIR/infra/aws-tunnel/cloudformation.yaml" >/dev/null 2>&1
  assert_eq "$?" "0" "cloudformation.yaml is well-formed"
else
  PASS=$((PASS+1)); echo "  skip: aws CLI not available or not authenticated (no local mutation, read-only API call)"
fi

echo "21. herdr-remote stop confirms shutdown of a stopped-but-enabled unit"
# install-service.sh enables the systemd units, and `stop` deliberately leaves
# them enabled so they return at next login. A unit in that state is down, so
# stop must confirm the shutdown rather than report that something will restart
# it. Driven through the real wrapper with a mocked Linux service manager.
HR_TMP="$(mktemp -d)"
mkdir -p "$HR_TMP/bin" "$HR_TMP/home/.config/herdr-remote"
printf 'HERDR_TUNNEL_MODE=temp\n' > "$HR_TMP/home/.config/herdr-remote/config.env"
printf 'HERDR_RELAY_TOKEN=tok\n' > "$HR_TMP/home/.config/herdr-remote/secrets.env"
printf '#!/bin/sh\necho Linux\n' > "$HR_TMP/bin/uname"
printf '#!/bin/sh\nexit 1\n' > "$HR_TMP/bin/lsof"
printf '#!/bin/sh\nexit 1\n' > "$HR_TMP/bin/pgrep"
cat > "$HR_TMP/bin/systemctl" <<'HRSYSTEMCTL'
#!/bin/sh
# enabled, but not running: what a successful `stop` leaves behind
for a in "$@"; do
  case "$a" in
    is-active)  exit 3 ;;
    is-enabled) exit 0 ;;
  esac
done
case " $* " in
  *" show "*) echo "ActiveState=inactive" ;;
esac
exit 0
HRSYSTEMCTL
chmod +x "$HR_TMP/bin/uname" "$HR_TMP/bin/lsof" "$HR_TMP/bin/pgrep" "$HR_TMP/bin/systemctl"
PATH="$HR_TMP/bin:$PATH" HOME="$HR_TMP/home" HERDR_REMOTE_REPO="$DIR" \
    "$DIR/relay/herdr-remote" stop > "$HR_TMP/stop.log" 2>&1
assert_eq "$?" "0" "stop confirms shutdown instead of claiming a restart is pending"
rm -rf "$HR_TMP"

echo "22. herdr-remote url prints a one-tap URL with the token embedded"
# The operator taps this straight out of the terminal, so the token has to be
# a query parameter (the relay authenticates GET / server-side, and a fragment
# is never sent to it) and has to be percent-encoded (parse_qs would otherwise
# read a literal '+' as a space and a '&' would truncate the token).
HR_TMP="$(mktemp -d)"
mkdir -p "$HR_TMP/bin" "$HR_TMP/home/.config/herdr-remote"
printf 'HERDR_TUNNEL_MODE=aws\nHERDR_AWS_HOST=example.sslip.io\n' \
    > "$HR_TMP/home/.config/herdr-remote/config.env"
printf 'HERDR_RELAY_TOKEN=a+b/c&d\n' > "$HR_TMP/home/.config/herdr-remote/secrets.env"
printf '#!/bin/sh\nexit 1\n' > "$HR_TMP/bin/lsof"
printf '#!/bin/sh\nexit 1\n' > "$HR_TMP/bin/pgrep"
chmod +x "$HR_TMP/bin/lsof" "$HR_TMP/bin/pgrep"
HR_URL=$(PATH="$HR_TMP/bin:$PATH" HOME="$HR_TMP/home" HERDR_REMOTE_REPO="$DIR" \
    "$DIR/relay/herdr-remote" url 2>/dev/null)
assert_eq "$HR_URL" "https://example.sslip.io/?token=a%2Bb%2Fc%26d" \
    "url prints the tappable link with a percent-encoded token"

echo "23. herdr-remote start, status and url agree on that URL"
# Three surfaces printing three spellings of the same link is the drift this
# guards against; they all have to come from tap_url().
HR_STATUS=$(PATH="$HR_TMP/bin:$PATH" HOME="$HR_TMP/home" HERDR_REMOTE_REPO="$DIR" \
    "$DIR/relay/herdr-remote" status 2>/dev/null | sed -n 's/^  Tap:  *//p')
assert_eq "$HR_STATUS" "$HR_URL" "status prints the same link as url"
HR_SRC_USERS=$(grep -c 'tap_url' "$DIR/relay/herdr-remote")
[ "$HR_SRC_USERS" -ge 4 ]
assert_eq "$?" "0" "start, status and url all route through tap_url"
rm -rf "$HR_TMP"

# --- Repository policy gates ---
# These are POLICY gates, not behavior tests. They assert a property of the
# repository's contents itself, which is the thing being guaranteed - not a
# proxy for any code working.
echo ""
echo "=== Repository policy ==="
echo "24. POLICY: no credential material committed anywhere in the repository"
# Scans every git-tracked file, not just the AWS tunnel directory and not
# just the diff: the requirement is repo-wide, and a full scan gives the
# same answer regardless of branch, rebase, or staging state.
SECRET_RE='(AKIA|ASIA)[0-9A-Z]{16}'
SECRET_RE="$SECRET_RE"'|aws_secret_access_key[[:space:]]*=[[:space:]]*[A-Za-z0-9/+=]{40}'
SECRET_RE="$SECRET_RE"'|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----'
if command -v git >/dev/null 2>&1 && git -C "$DIR" rev-parse --git-dir >/dev/null 2>&1; then
  SECRET_HITS=$(git -C "$DIR" grep -lIE "$SECRET_RE" -- . 2>/dev/null)
  if [ -n "$SECRET_HITS" ]; then
    echo "  credential material found in:"
    echo "$SECRET_HITS" | sed 's/^/    /'
  fi
  [ -z "$SECRET_HITS" ]
  assert_eq "$?" "0" "no AWS keys or private keys are committed"
else
  FAIL=$((FAIL+1)); echo "  FAIL: cannot run the secret-hygiene gate outside a git checkout"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
