"""Real Iris, Herdr, relay and Pi. Run only in the documented network-none container."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

from local_relay import Relay
from model_fixture import ModelServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="1")
    args = parser.parse_args()
    if not Path("/.dockerenv").exists():
        raise SystemExit("Run this fixture in the disposable Docker container.")
    root = Path("/tmp/iris-herdr-e2e") / args.attempt
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    home = root / "home"
    home.mkdir()
    workspace = Path("/tmp/test-workspace")
    workspace.mkdir(exist_ok=True)
    (workspace / "marker.txt").write_text("IRIS_TOOL_OK\n")
    hashtree = root / "hashtree"
    hashtree.mkdir()
    (hashtree / "config.toml").write_text('[blossom]\nservers=[]\nread_servers=["http://127.0.0.1:9"]\nwrite_servers=["http://127.0.0.1:9"]\n')
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "TERM": "xterm-256color", "SHELL": "/bin/bash",
           "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local/share"),
           "XDG_CACHE_HOME": str(home / ".cache"), "HTREE_CONFIG_DIR": str(hashtree),
           "IRIS_DEMO_RELAYS": "", "IRIS_FIPS_WEBSOCKET_SEED_URLS": "", "IRIS_UPDATE_RELAYS": "",
           "IRIS_CHAT_FIPS_WEBSOCKET_BIND_ADDR": "", "IRIS_CHAT_FIPS_UDP_BIND_ADDR": "",
           "IRIS_CHAT_FIPS_ENABLE_WEBRTC": "0", "IRIS_CHAT_FIPS_STATIC_PEERS": "",
           "IRIS_CHAT_FIPS_ROUTED_PEERS": "", "IRIS_CHAT_FIPS_TRUSTED_RATERS": "",
           "IRIS_CHAT_FIPS_LOCAL_RENDEZVOUS_ADDR": "", "IRIS_CHAT_SAME_HOST_HASHTREE": "0",
           "IRIS_UPDATE_BLOSSOM_SERVERS": "http://127.0.0.1:9", "NO_COLOR": "1",
           "HERDR_LOG_DIR": str(root / "relay-logs"), "HERDR_REMOTES": "", "HERDR_RELAY_TOKEN": "throwaway-relay-token",
           "HERDR_IRIS_STATE_DIR": str(root / "agent"), "HERDR_RELAY": "ws://127.0.0.1:8375"}
    model_config = home / ".pi/agent"
    model_config.mkdir(parents=True)
    (model_config / "models.json").write_text(json.dumps({"providers": {"fixture": {
        "baseUrl": "http://127.0.0.1:18778/v1", "api": "openai-completions", "apiKey": "fixture",
        "models": [{"id": "iris-fixture", "name": "Fixture", "reasoning": False, "input": ["text"],
                    "contextWindow": 65536, "maxTokens": 4096,
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}]}}}))
    processes, logs = [], []
    nostr = Relay(("127.0.0.1", 18777))
    model = ModelServer(("127.0.0.1", 18778))
    for server in (nostr, model):
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def start(name, command):
        log = (root / (name + ".log")).open("wb")
        logs.append(log)
        process = subprocess.Popen(command, env=env, cwd=workspace, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        processes.append(process)
        return process

    def run(command, data=None, timeout=45):
        result = subprocess.run(command, env=env, cwd=workspace, input=data, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            # The full response stays private in this disposable container.
            (root / "command-failure.log").write_text(result.stdout + result.stderr)
            raise RuntimeError(f"Fixture command failed: {command[0:2]}")
        return result.stdout

    def cli(profile, *arguments):
        result = json.loads(run(["iris", "--json", "--no-background-sync", "--data-dir", str(profile), *arguments]))
        assert result.get("status") == "ok"
        return result["data"]

    def herdr(*arguments):
        return json.loads(run(["herdr", *arguments]))["result"]

    def wait(check, seconds=60):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = check()
            if result:
                return result
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("A fixture process exited; inspect its container-only log.")
            time.sleep(.3)
        raise TimeoutError("Fixture readiness or delivery timed out.")

    def ready(name):
        for line in (root / (name + ".log")).read_text().splitlines():
            try:
                if json.loads(line).get("data", {}).get("ready"):
                    return True
            except (ValueError, AttributeError):
                pass
        return False

    profiles = {"agent": root / "agent/profile", "owner": root / "owner", "stranger": root / "stranger"}
    started = time.monotonic()
    try:
        for name, profile in profiles.items():
            start(name, ["iris", "--json", "--data-dir", str(profile), "service", "run"])
            wait(lambda: ready(name), 30)
            cli(profile, "relay", "set", "ws://127.0.0.1:18777")
            cli(profile, "account", "create", "--name", "Disposable " + name)
        owner = cli(profiles["owner"], "whoami")["user_id"]
        agent = cli(profiles["agent"], "whoami")["user_id"]
        invite = cli(profiles["owner"], "invite", "create", "--full")["url"]
        run([sys.executable, "/test/relay/herdr_iris.py", "setup", "--json"], json.dumps({"owner": owner, "invite": invite}))
        run([sys.executable, "/test/relay/herdr_iris.py", "setup"])
        # An additional accepted chat still must not gain access to the control channel.
        stranger_invite = cli(profiles["stranger"], "invite", "create", "--full")["url"]
        cli(profiles["agent"], "invite", "accept", stranger_invite)
        print("PASS: throwaway identities paired; repeat setup preserves identity", flush=True)

        start("herdr", ["herdr", "server"])
        wait(lambda: subprocess.run(["herdr", "workspace", "list"], env=env, capture_output=True).returncode == 0)
        run(["herdr", "integration", "install", "pi"])
        pane = herdr("workspace", "create", "--cwd", str(workspace), "--label", "fixture", "--no-focus")["root_pane"]["pane_id"]
        wait(lambda: bool(herdr("pane", "list")["panes"]))
        herdr("agent", "start", "fixture", "--kind", "pi", "--pane", pane,
              "--", "--provider", "fixture", "--model", "iris-fixture", "--no-session",
              "--extension", "/test/tests/iris/question.mjs")
        start("relay", [sys.executable, "/test/relay/herdr_relay.py"])
        bridge = start("bridge", [sys.executable, "/test/relay/herdr_iris.py"])

        def messages(profile="owner"):
            return cli(profiles[profile], "read", agent, "--limit", "200")["messages"]

        def incoming(contains):
            return [m for m in messages() if m.get("is_outgoing") is False and contains in m.get("body", "")]

        wait(lambda: incoming("Herdr through Iris Chat"))
        def command(text, expect):
            prior = {m["id"] for m in incoming(expect)}
            cli(profiles["owner"], "send", agent, text)
            return wait(lambda: [m for m in incoming(expect) if m["id"] not in prior])

        command("/agents", pane)
        command(f"/read {pane}", "iris-fixture")
        print("PASS: encrypted /agents and /read through real Herdr and authenticated relay", flush=True)

        cli(profiles["stranger"], "send", agent, f"/send {pane} UNAUTHORIZED")
        time.sleep(4)
        assert not model.requests, "Unapproved contact reached the agent."
        assert not any(m.get("is_outgoing") is False for m in messages("stranger")), "Output leaked to another contact."
        print("PASS: third throwaway identity cannot command the agent or receive output", flush=True)

        command(f"/send {pane} Read the marker file and report the result.", "Submitted")
        wait(lambda: incoming("finished"), 90)
        command(f"/read {pane}", "IRIS_TEST_OK")
        assert len(model.requests) >= 2
        assert any(m.get("role") == "tool" and "IRIS_TOOL_OK" in str(m.get("content"))
                   for req in model.requests for m in req.get("messages", [])), "Real file tool was not executed."
        wait(lambda: any(m.get("is_outgoing") and m.get("body", "").startswith("/send") and m.get("delivery") == "seen"
                         for m in messages()))
        print("PASS: prompt -> real Pi file tool -> completion notification -> encrypted output and seen receipt", flush=True)

        count = len(model.requests)
        bridge.terminate()
        bridge.wait(15)
        processes.remove(bridge)
        with sqlite3.connect(root / "agent/bridge.sqlite3") as db:
            db.execute("INSERT INTO jobs(id,body,status,created) VALUES (?,?,?,?)",
                       ("interrupted-test", f"/send {pane} DO_NOT_REPLAY", "running", time.time()))
        bridge = start("bridge-restart", [sys.executable, "/test/relay/herdr_iris.py"])
        wait(lambda: incoming("not replayed"))
        command("/agents", pane)
        assert len(model.requests) == count
        print("PASS: restart preserves pairing and does not replay claimed commands", flush=True)

        command(f"/send {pane} /iris-test-question", "Submitted")
        wait(lambda: incoming("needs your attention"))
        command(f"/send {pane} yes", "Handle the request")
        assert len(model.requests) == count
        run(["herdr", "agent", "send-keys", pane, "esc"])
        print("PASS: blocked notification and refusal of remote approvals", flush=True)
        command(f"/interrupt {pane}", "Submitted")
        print("PASS: encrypted interrupt reaches real Herdr", flush=True)
        print(f"PASS: live Docker flow ({time.monotonic() - started:.1f}s); no external network or paid model", flush=True)
    finally:
        subprocess.run(["herdr", "server", "stop"], env=env, capture_output=True, timeout=10)
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()
        for server in (nostr, model):
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
