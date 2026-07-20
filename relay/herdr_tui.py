#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["textual>=3.0.0", "websockets>=14.0"]
# ///
"""herdr-remote-tui: terminal dashboard for herdr agents. Connects to herdr-remote-relay via WebSocket."""
import asyncio, json, os, sys

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Static, Input, Button, Label, Rule
from textual.reactive import reactive
from textual.message import Message
from textual import work

from agent_state import apply_agent_message

RELAY_WS = os.environ.get("HERDR_RELAY", "ws://127.0.0.1:8375")


class AgentCard(Static):
    """A single agent card."""

    def __init__(self, agent: dict, **kw):
        super().__init__(**kw)
        self.agent = agent

    def compose(self) -> ComposeResult:
        status = self.agent.get("status", "unknown")
        color = {"blocked": "red", "working": "green", "idle": "dim"}.get(status, "dim")
        name = self.agent.get("agent", "?")
        label = self.agent.get("label", "") or self.agent.get("project", "")
        yield Label(f"[{color}]●[/] {label}/{name} [{color}]{status}[/]", markup=True)


class AgentColumn(Vertical):
    """A kanban column."""

    def __init__(self, title: str, color: str, **kw):
        super().__init__(**kw)
        self.border_title = title
        self.styles.border = ("round", color)
        self.styles.width = "1fr"
        self.styles.height = "100%"
        self.styles.padding = (0, 1)


class ApprovalPanel(Vertical):
    """Shows when an agent is blocked — prompt + buttons."""

    class Responded(Message):
        def __init__(self, pane_id: str, prompt_id: str | None, text: str):
            super().__init__()
            self.pane_id = pane_id
            self.prompt_id = prompt_id
            self.text = text

    class QuestionToggled(Message):
        def __init__(self, pane_id: str, prompt_id: str, option: str):
            super().__init__()
            self.pane_id = pane_id
            self.prompt_id = prompt_id
            self.option = option

    class QuestionSubmitted(Message):
        def __init__(self, pane_id: str, prompt_id: str):
            super().__init__()
            self.pane_id = pane_id
            self.prompt_id = prompt_id

    def __init__(self, agent: dict, **kw):
        super().__init__(**kw)
        self.agent = agent
        self.styles.height = "auto"
        self.styles.border = ("round", "red")
        self.border_title = f"⚠ {agent.get('agent', '?')} — {agent.get('label', '') or agent.get('project', '')}"

    def compose(self) -> ComposeResult:
        prompt = self.agent.get("prompt", "Waiting for input...")
        yield Static(prompt[:400], classes="prompt-text")
        multi = self.agent.get("interaction") == "omp_question" and self.agent.get("multi")
        options = self.agent.get("multi_options") if multi else self.agent.get("options")
        options = options or []
        selected = set(self.agent.get("selected_options") or [])
        for i, opt in enumerate(options):
            color = "green" if "yes" in opt or "approve" in opt else "red" if "no" in opt or "cancel" in opt else "blue"
            label = f"{'☑' if opt in selected else '☐'} {opt}" if multi else opt
            button_id = f"multi-{i}" if multi else f"opt-{i}"
            yield Button(label, id=button_id, variant="success" if color == "green" else "error" if color == "red" else "primary")
        if multi:
            yield Button("Submit", id="multi-submit", variant="success")
        yield Input(placeholder="Custom response…", id="custom-input")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "multi-submit":
            if prompt_id := self.agent.get("prompt_id"):
                self.post_message(self.QuestionSubmitted(self.agent["pane_id"], prompt_id))
            return
        if event.button.id.startswith("multi-"):
            idx = int(event.button.id.split("-")[1])
            options = self.agent.get("multi_options") or []
            if idx < len(options) and (prompt_id := self.agent.get("prompt_id")):
                self.post_message(
                    self.QuestionToggled(self.agent["pane_id"], prompt_id, options[idx])
                )
            return
        idx = int(event.button.id.split("-")[1])
        options = self.agent.get("options") or []
        if idx < len(options):
            self.post_message(self.Responded(self.agent["pane_id"], self.agent.get("prompt_id"), options[idx]))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.post_message(self.Responded(self.agent["pane_id"], self.agent.get("prompt_id"), event.value.strip()))


class HerdrRemoteTUI(App):
    CSS = """
    #board { height: 1fr; }
    #approvals { height: auto; max-height: 40%; }
    .prompt-text { max-height: 6; overflow-y: auto; color: $text-muted; }
    #status-bar { height: 1; background: $surface; padding: 0 1; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "reconnect", "Reconnect"),
        ("1", "approve_first", "Approve first"),
    ]

    agents: reactive[list] = reactive(list, recompose=True)
    connected: reactive[bool] = reactive(False)
    blocked_agents: reactive[list] = reactive(list)

    def __init__(self):
        super().__init__()
        self._ws = None
        self._agents_data: list[dict] = []
        self._blocked_data: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="board"):
            with AgentColumn("🚨 Blocked", "red", id="col-blocked"):
                for a in self._agents_data:
                    if a.get("status") == "blocked":
                        yield AgentCard(a)
            with AgentColumn("⚡ Working", "green", id="col-working"):
                for a in self._agents_data:
                    if a.get("status") == "working":
                        yield AgentCard(a)
            with AgentColumn("💤 Idle", "grey", id="col-idle"):
                for a in self._agents_data:
                    if a.get("status") in ("idle", "unknown"):
                        yield AgentCard(a)
        with VerticalScroll(id="approvals"):
            for a in self._blocked_data:
                yield ApprovalPanel(a)
        yield Static(
            f"[green]●[/] Connected to {RELAY_WS}" if self.connected else "[red]●[/] Disconnected",
            id="status-bar", markup=True
        )
        yield Footer()

    def on_mount(self) -> None:
        self.title = "herdr-remote"
        self.sub_title = "agent dashboard"
        self.connect_relay()

    @work(exclusive=True, thread=False)
    async def connect_relay(self) -> None:
        import websockets
        while True:
            try:
                async with websockets.connect(RELAY_WS) as ws:
                    self._ws = ws
                    self.connected = True
                    self.mutate_reactive(HerdrRemoteTUI.connected)
                    self.recompose()
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        self._handle_msg(msg)
            except Exception:
                self._ws = None
                self.connected = False
                self.mutate_reactive(HerdrRemoteTUI.connected)
                self.recompose()
                await asyncio.sleep(3)

    def _handle_msg(self, msg: dict):
        if msg.get("type") == "agents":
            self._agents_data = apply_agent_message(self._agents_data, msg)
            # Clear blocked data for agents no longer blocked
            blocked_ids = {a["pane_id"] for a in self._agents_data if a.get("status") == "blocked"}
            self._blocked_data = [b for b in self._blocked_data if b["pane_id"] in blocked_ids]
            self.recompose()
        elif msg.get("type") == "agent_update":
            self._agents_data = apply_agent_message(self._agents_data, msg)
            update = msg.get("agent") or {}
            if update.get("status") != "blocked":
                pane_id = update.get("pane_id")
                self._blocked_data = [b for b in self._blocked_data if b.get("pane_id") != pane_id]
            self.recompose()
        elif msg.get("type") == "blocked":
            # Update or add
            pid = msg.get("pane_id")
            self._blocked_data = [b for b in self._blocked_data if b.get("pane_id") != pid]
            self._blocked_data.append(msg)
            self.recompose()

    def on_approval_panel_responded(self, event: ApprovalPanel.Responded) -> None:
        self._send_response(event.pane_id, event.prompt_id, event.text)

    def on_approval_panel_question_toggled(self, event: ApprovalPanel.QuestionToggled) -> None:
        if self._ws:
            msg = json.dumps({
                "type": "question_toggle",
                "pane_id": event.pane_id,
                "prompt_id": event.prompt_id,
                "option": event.option,
            })
            asyncio.ensure_future(self._ws.send(msg))

    def on_approval_panel_question_submitted(self, event: ApprovalPanel.QuestionSubmitted) -> None:
        if self._ws:
            msg = json.dumps({
                "type": "question_submit",
                "pane_id": event.pane_id,
                "prompt_id": event.prompt_id,
            })
            asyncio.ensure_future(self._ws.send(msg))
            self._blocked_data = [
                blocked for blocked in self._blocked_data
                if blocked.get("pane_id") != event.pane_id
            ]
            self.recompose()

    def _send_response(self, pane_id: str, prompt_id: str | None, text: str):
        if self._ws:
            msg = json.dumps({"type": "respond", "pane_id": pane_id, "prompt_id": prompt_id, "text": text})
            asyncio.ensure_future(self._ws.send(msg))
            # Remove from blocked
            self._blocked_data = [b for b in self._blocked_data if b.get("pane_id") != pane_id]
            self.recompose()

    def action_reconnect(self) -> None:
        self.connect_relay()

    def action_approve_first(self) -> None:
        if self._blocked_data:
            a = self._blocked_data[0]
            options = a.get("options") or []
            if options:
                self._send_response(a["pane_id"], a.get("prompt_id"), options[0])


if __name__ == "__main__":
    HerdrRemoteTUI().run()
