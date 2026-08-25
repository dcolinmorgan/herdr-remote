#!/usr/bin/env node
// .pi-kiro-auth.js — Refresh kiro-cli credentials for Pi
//
// The npm pi-provider-kiro reads directly from kiro-cli's SQLite DB,
// so this script just calls kiro-cli to refresh the token.
//
// Usage: node .pi-kiro-auth.js
// Or just: kiro-cli debug refresh-auth-token

const { execFileSync } = require("child_process");

try {
  execFileSync("kiro-cli", ["debug", "refresh-auth-token"], { stdio: "inherit" });
  console.log("\nPi will use the refreshed token on next start.");
  console.log("If Pi is running, restart it.");
} catch (e) {
  console.error("Failed to refresh. Run: kiro-cli login");
  process.exit(1);
}
