#!/bin/bash
# Compile PromptScan.swift, Quiet.swift and ApprovalFocus.swift with their tests and run them.
# Then do the same for PhoneLink.swift, and for the layout of the Connect a Phone window.
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

PHONE_BIN="$(mktemp -d)/phonelink-tests"
swiftc -O -o "$PHONE_BIN" \
    "$SCRIPT_DIR/Sources/PhoneLink.swift" \
    "$SCRIPT_DIR/Tests/PhoneLink/main.swift"
"$PHONE_BIN"

LAYOUT_BIN="$(mktemp -d)/connectphone-layout-tests"
swiftc -O -parse-as-library -o "$LAYOUT_BIN" \
    "$SCRIPT_DIR/Sources/PhoneLink.swift" \
    "$SCRIPT_DIR/Sources/Quiet.swift" \
    "$SCRIPT_DIR/Sources/ConnectPhoneWindow.swift" \
    "$SCRIPT_DIR/Tests/ConnectPhoneLayout/main.swift"
"$LAYOUT_BIN"
