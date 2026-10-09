#!/bin/bash
# Compile PromptScan.swift, Quiet.swift and ApprovalFocus.swift with their tests and run them.
#
# This uses swiftc directly, not `swift test`, because XCTest ships with Xcode only. The Command
# Line Tools alone can run this script.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN="$(mktemp -d)/promptscan-tests"

swiftc -O -o "$BIN" \
    "$SCRIPT_DIR/Sources/PromptScan.swift" \
    "$SCRIPT_DIR/Sources/Quiet.swift" \
    "$SCRIPT_DIR/Sources/ApprovalFocus.swift" \
    "$SCRIPT_DIR/Tests/PromptScan/main.swift"
"$BIN" "$SCRIPT_DIR/Tests/PromptScan"
