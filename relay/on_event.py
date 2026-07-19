#!/usr/bin/env python3
"""Local plugin hook — pushes event to herdr-remote relay via UDP."""
import json, os, socket

event = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "{}"))
data = event.get("data", {})
try:
    context = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON", "{}"))
except (json.JSONDecodeError, TypeError):
    context = {}
if not isinstance(context, dict):
    context = {}

cwd = next(
    (
        value
        for value in (
            context.get("focused_pane_cwd"),
            context.get("workspace_cwd"),
            data.get("cwd"),
        )
        if isinstance(value, str) and value
    ),
    "",
)

payload = json.dumps({
    "type": "agent_event",
    "pane_id": data.get("pane_id", ""),
    "status": (data.get("agent_status") or "").lower(),
    "agent": (data.get("agent") or data.get("display_agent") or "").lower(),
    "project": os.path.basename(cwd),
    "cwd": cwd,
    "host": socket.gethostname().split(".")[0],
}).encode()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto(payload, ("127.0.0.1", 8376))
sock.close()
