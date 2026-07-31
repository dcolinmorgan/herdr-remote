"""Shared relay agent snapshot and incremental-update helpers."""
from typing import Optional

AGENT_EVENT_FIELDS = ("pane_id", "agent", "status", "cwd", "project", "host")
REQUIRED_AGENT_FIELDS = ("pane_id", "agent", "status", "cwd", "project")


def agent_update_message(event: dict, local_hostname: Optional[str] = None) -> dict:
    """Build a single-pane update without masquerading as a full snapshot."""
    agent = {field: event[field] for field in AGENT_EVENT_FIELDS if field in event}
    if agent.get("host") in (None, ""):
        agent.pop("host", None)
    host = str(agent.get("host") or "").lower().split(".", 1)[0]
    local_host = str(local_hostname or "").lower().split(".", 1)[0]
    if host and local_host and host == local_host:
        agent["host"] = "local"
    return {"type": "agent_update", "agent": agent}


def complete_agent_update_message(event: dict, current: Optional[dict] = None, local_hostname: Optional[str] = None):
    """Enrich a sparse event and return None until it describes a usable agent."""
    merged = dict(current or {})
    for field, value in event.items():
        if field in (*REQUIRED_AGENT_FIELDS, "host") and value in (None, "") and merged.get(field):
            continue
        merged[field] = value
    message = agent_update_message(merged, local_hostname=local_hostname)
    if not all(message["agent"].get(field) not in (None, "") for field in REQUIRED_AGENT_FIELDS):
        return None
    return message


def apply_agent_message(current: list[dict], message: dict) -> list[dict]:
    """Replace on snapshots and merge one pane on incremental updates."""
    if message.get("type") == "agents":
        return [dict(agent) for agent in message.get("agents", [])]

    if message.get("type") != "agent_update":
        return current

    update = message.get("agent") or {}
    pane_id = update.get("pane_id")
    if not pane_id:
        return current

    result = [dict(agent) for agent in current]
    for agent in result:
        if agent.get("pane_id") == pane_id:
            agent.update(update)
            return result

    result.append(dict(update))
    return result
