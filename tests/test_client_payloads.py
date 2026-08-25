import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ClientPayloadTests(unittest.TestCase):
    def test_web_preserves_and_sends_omp_question_state(self):
        source = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

        for field in (
            "prompt_id",
            "multi_options",
            "selected_options",
            "interaction",
            "multi",
        ):
            self.assertIn(field, source)
        self.assertIn("type:'question_toggle'", source)
        self.assertIn("type:'question_submit'", source)
        self.assertIn("prompt_id:a.prompt_id", source)

    def test_swift_models_decode_omp_question_state(self):
        for relative_path in (
            "herdi-ios/Sources/Models/Agent.swift",
            "herdi-mac/Sources/Agent.swift",
        ):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            for field in (
                "prompt_id",
                "multi_options",
                "selected_options",
                "interaction",
                "multi",
            ):
                self.assertIn(field, source)
            self.assertIn('let type = "question_toggle"', source)
            self.assertIn('let type = "question_submit"', source)

    def test_native_clients_render_and_send_multi_selection_state(self):
        ios = (ROOT / "herdi-ios" / "Sources" / "Views" / "ApprovalView.swift").read_text(
            encoding="utf-8"
        )
        mac = (ROOT / "herdi-mac" / "Sources" / "NotchContentView.swift").read_text(
            encoding="utf-8"
        )

        for source in (ios, mac):
            self.assertIn("selectedOptions.contains(option)", source)
            self.assertIn("toggleQuestionOption", source)
            self.assertIn("submitQuestion", source)

    def test_native_question_actions_are_connection_methods(self):
        for relative_path in (
            "herdi-ios/Sources/Services/RelayConnection.swift",
            "herdi-mac/Sources/RelayConnection.swift",
        ):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            for method in ("toggleQuestionOption", "submitQuestion"):
                declaration = next(
                    line for line in source.splitlines() if f"func {method}(" in line
                )
                self.assertTrue(
                    declaration.startswith("    func "),
                    f"{method} must be declared at RelayConnection class scope",
                )

    def test_windows_client_carries_omp_question_state(self):
        protocol = (ROOT / "herdi-win" / "Models" / "Protocol.cs").read_text(encoding="utf-8")
        agent = (ROOT / "herdi-win" / "Models" / "Agent.cs").read_text(encoding="utf-8")

        for field in (
            "prompt_id",
            "multi_options",
            "selected_options",
            "interaction",
            "multi",
        ):
            self.assertIn(field, protocol)
        self.assertIn('"question_toggle"', protocol)
        self.assertIn('"question_submit"', protocol)

        for member in ("MultiOptions", "SelectedOptions", "Interaction", "IsMultiSelect"):
            self.assertIn(member, agent)

    def test_windows_question_actions_are_connection_methods(self):
        source = (ROOT / "herdi-win" / "Services" / "RelayConnection.cs").read_text(
            encoding="utf-8"
        )
        for method in ("ToggleQuestionOption", "SubmitQuestion"):
            declaration = next(
                line for line in source.splitlines() if f"public void {method}(" in line
            )
            self.assertTrue(
                declaration.startswith("    public void "),
                f"{method} must be declared at RelayConnection class scope",
            )

    def test_windows_client_respects_relay_allowlists(self):
        """Free text must not go out as `respond`, and interrupt must spell C-c."""
        protocol = (ROOT / "herdi-win" / "Models" / "Protocol.cs").read_text(encoding="utf-8")
        connection = (ROOT / "herdi-win" / "Services" / "RelayConnection.cs").read_text(
            encoding="utf-8"
        )

        self.assertIn('InterruptKey = "C-c"', protocol)
        self.assertIn("SafeResponses", protocol)
        # Respond() picks agent_prompt for anything outside SAFE_RESPONSES.
        self.assertIn("Protocol.SafeResponses.Contains", connection)
        self.assertIn("Protocol.AgentPrompt", connection)

    def test_windows_client_renders_multi_selection_state(self):
        card = (ROOT / "herdi-win" / "Views" / "ApprovalCardView.xaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("MultiOptions", card)
        self.assertIn("ToggleOptionCommand", card)
        self.assertIn("SubmitQuestionCommand", card)

    def test_windows_sections_bind_to_notifying_flags(self):
        """A section bound straight at its collection would never become visible.

        IslandViewModel hands back the same ObservableCollection instance forever and
        raises no PropertyChanged for it, so `{Binding Blocked, Converter=NonEmptyToVis}`
        is evaluated once — while the list is still empty — and stays collapsed for the
        rest of the run. Visibility has to ride a flag Rebuild() notifies.
        """
        for view in ("SessionListView.xaml", "IslandWindow.xaml"):
            xaml = (ROOT / "herdi-win" / "Views" / view).read_text(encoding="utf-8")
            for collection in ("Blocked", "Working", "Idle"):
                self.assertNotIn(
                    f"{{Binding {collection}, Converter",
                    xaml,
                    f"{view} binds {collection} itself; bind Has{collection} instead",
                )

        sections = (ROOT / "herdi-win" / "Views" / "SessionListView.xaml").read_text(
            encoding="utf-8"
        )
        view_model = (ROOT / "herdi-win" / "ViewModels" / "IslandViewModel.cs").read_text(
            encoding="utf-8"
        )
        for flag in ("HasBlocked", "HasWorking", "HasIdle"):
            self.assertIn(f"Binding {flag}, Converter", sections)
            self.assertIn(f"public bool {flag} =>", view_model)
            self.assertIn(f"OnPropertyChanged(nameof({flag}))", view_model)

    def test_clients_offer_the_same_two_sources(self):
        """Direct mode is the mac app's second half; the Windows client must have it too."""
        mac = (ROOT / "herdi-mac" / "Sources" / "RelayConnection.swift").read_text(
            encoding="utf-8"
        )
        self.assertIn("case direct", mac)
        self.assertIn("case relay", mac)

        modes = (ROOT / "herdi-win" / "Services" / "ConnectionMode.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("Relay,", modes)
        self.assertIn("Direct,", modes)

        # Both persist the choice and the SSH host list across launches.
        self.assertIn("herdi_remotes", mac)
        store = (ROOT / "herdi-win" / "Services" / "SettingsStore.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("public ConnectionMode Mode", store)
        self.assertIn("public IReadOnlyList<string> Remotes", store)

        # And both route outbound commands by mode rather than always down the socket.
        connection = (ROOT / "herdi-win" / "Services" / "RelayConnection.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("Mode == ConnectionMode.Direct", connection)

    def test_windows_direct_mode_reaches_hosts_on_the_relays_terms(self):
        """A host the relay can poll must be reachable from the client unchanged.

        The relay is the reference implementation for driving herdr over SSH
        (herdr_relay.py:175). Diverging on the flags, the remote binary name or the prompt
        window would make the same blocked pane read differently depending on which side
        fetched it.
        """
        relay = (ROOT / "relay" / "herdr_relay.py").read_text(encoding="utf-8")
        cli = (ROOT / "herdi-win" / "Services" / "HerdrCli.cs").read_text(encoding="utf-8")
        poller = (ROOT / "herdi-win" / "Services" / "HerdrPoller.cs").read_text(
            encoding="utf-8"
        )

        for flag in ("ConnectTimeout=5", "BatchMode=yes"):
            self.assertIn(flag, relay)
            self.assertIn(f'"{flag}"', cli)
        self.assertIn("HERDR_REMOTE_BIN", relay)
        self.assertIn("HERDR_REMOTE_BIN", cli)

        self.assertIn('"pane", "list"', poller)

        # Both sides build the preview out of the tail of the pane: read, drop chrome, keep
        # the last lines, cut to the last 500 characters. Only that final cut has to agree
        # -- taking it off the front would keep the scrollback and drop the question, and
        # with it the options each side parses back out of the same text. How much is read
        # to get there differs on purpose: 100 lines for a pane view the relay also serves,
        # 50 for a toast.
        self.assertIn('"--lines", "100"', relay)
        self.assertIn("PromptReadLines = 50", poller)
        self.assertIn("lines[-50:]", relay)
        self.assertIn("PromptKeepLines = 20", poller)
        self.assertIn('"prompt": content[-500:]', relay)
        self.assertIn("PromptMaxChars = 500", poller)
        self.assertIn("prompt[^PromptMaxChars..]", poller)

    def test_windows_direct_mode_namespaces_remote_pane_ids(self):
        """Two hosts can hand out the same pane id, so ids carry their host in direct mode.

        The prefix is a client-side namespace and must be stripped again before the id goes
        back to the host that issued it.
        """
        poller = (ROOT / "herdi-win" / "Services" / "HerdrPoller.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn('host + ":" + paneId', poller)
        self.assertIn("StartsWith(prefix", poller)
        # Interrupt keeps the one spelling the relay proved the CLI accepts.
        self.assertIn("Protocol.InterruptKey", poller)

    def test_windows_direct_mode_keeps_panes_of_a_host_that_failed(self):
        """A poll that failed says nothing about that host's panes.

        Pruning them anyway would make a flapping SSH connection empty and refill the list
        every couple of seconds, and every blocked agent on it would toast again each time
        it came back.
        """
        poller = (ROOT / "herdi-win" / "Services" / "HerdrPoller.cs").read_text(
            encoding="utf-8"
        )
        connection = (ROOT / "herdi-win" / "Services" / "RelayConnection.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("HostsAnswered", poller)
        self.assertIn("ApplySnapshot(result.Agents, result.HostsAnswered)", connection)
        self.assertIn("hostsCovered.Contains(agent.Host)", connection)

    def test_windows_every_expanded_row_opens_something(self):
        """A row that swallows a click is why the expanded island read as inert.

        Only NEEDS YOU was wired, and with no blocked agents that left a list where
        nothing at all responded to a click. macOS taps every row — blocked ones open the
        approval card, the rest jump to the pane in the terminal app.
        """
        sessions = (ROOT / "herdi-win" / "Views" / "SessionListView.xaml").read_text(
            encoding="utf-8"
        )
        # One handler per section: Blocked, Working, Idle.
        self.assertEqual(sessions.count('PreviewMouseLeftButtonUp="OnRowClicked"'), 3)

        view_model = (ROOT / "herdi-win" / "ViewModels" / "IslandViewModel.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("public void OpenAgent(Agent agent)", view_model)
        self.assertIn("if (agent.IsBlocked) ShowApproval(agent);", view_model)
        self.assertIn("public void ShowPane(Agent agent)", view_model)

        # The click must not also fire for the row's own action buttons, which sit under
        # the same tunnelling handler.
        code_behind = (ROOT / "herdi-win" / "Views" / "SessionListView.xaml.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("if (node is ButtonBase) return;", code_behind)

    def test_windows_pane_view_reads_and_submits_over_both_transports(self):
        """The pane view is this client's answer to macOS's "jump to terminal".

        Focusing a window is meaningless for an agent an SSH hop away, so the row opens
        the terminal here instead. Reading and submitting have to work identically from
        the relay and from the CLI, or the surface is only half a feature.
        """
        pane = (ROOT / "herdi-win" / "Views" / "PaneView.xaml").read_text(encoding="utf-8")
        self.assertIn("Binding PaneContent", pane)
        self.assertIn("SendPaneInputCommand", pane)
        self.assertIn("InterruptCommand", pane)

        connection = (ROOT / "herdi-win" / "Services" / "RelayConnection.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("public void SendPrompt(Agent agent, string text)", connection)
        self.assertIn("_direct.PromptAsync(agent, trimmed)", connection)
        self.assertIn("Protocol.AgentPrompt(agent.Id, trimmed)", connection)

        # Both sides submit with `herdr agent prompt`, the verb the relay's agent_prompt
        # handler runs — not by typing into the pane and hoping Enter takes. The relay reaches
        # it through a worker thread, so the subprocess never stalls the event loop.
        relay = (ROOT / "relay" / "herdr_relay.py").read_text(encoding="utf-8")
        self.assertIn('run_herdr, "agent", "prompt"', relay)
        poller = (ROOT / "herdi-win" / "Services" / "HerdrPoller.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn('"agent", "prompt"', poller)

    def test_windows_rows_answer_a_right_click(self):
        sessions = (ROOT / "herdi-win" / "Views" / "SessionListView.xaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("<Border.ContextMenu>", sessions)
        code_behind = (ROOT / "herdi-win" / "Views" / "SessionListView.xaml.cs").read_text(
            encoding="utf-8"
        )
        for handler in ("OnMenuAnswer", "OnMenuOpenPane", "OnMenuInterrupt", "OnMenuCopyPaneId"):
            self.assertIn(f'Click="{handler}"', sessions)
            self.assertIn(f"private void {handler}(", code_behind)

        # A menu opens in its own window, which deactivates the flyout — and the flyout
        # dismisses itself on deactivation, so it would go away out from under the menu it
        # just opened.
        island = (ROOT / "herdi-win" / "Views" / "IslandWindow.xaml.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("_menuOpen = true;", island)
        self.assertIn("if (_menuOpen || _previewing) return;", island)

    def test_windows_flyout_is_absent_rather_than_transparent(self):
        """Nothing of ours sits over somebody else's window while nothing is happening.

        The top-edge island had to hand its input back through WS_EX_TRANSPARENT, because
        a capsule pinned above every window covers tab strips and title bars whether or not
        anyone wants it there. The tray flyout meets the same requirement by not being on
        screen at all: hidden until the tray icon is clicked, out of the taskbar and out of
        Alt+Tab, and not even constructed until something asks for it.
        """
        xaml = (ROOT / "herdi-win" / "Views" / "IslandWindow.xaml").read_text(encoding="utf-8")
        self.assertIn('ShowInTaskbar="False"', xaml)
        self.assertIn('ShowActivated="False"', xaml)

        island = (ROOT / "herdi-win" / "Views" / "IslandWindow.xaml.cs").read_text(
            encoding="utf-8"
        )
        # WS_EX_TOOLWINDOW, the same intent as NSWindow's .ignoresCycle on the mac panel.
        self.assertIn("WsExToolWindow = 0x00000080", island)
        self.assertIn("exStyle | WsExToolWindow", island)

        # Built on first use, so a session that never opens it never pays for a WPF window.
        app = (ROOT / "herdi-win" / "App.xaml.cs").read_text(encoding="utf-8")
        self.assertIn("private IslandWindow? _island;", app)

    def test_tui_sends_multi_selection_messages_with_prompt_identity(self):
        source = (ROOT / "relay" / "herdr_tui.py").read_text(encoding="utf-8")

        self.assertIn('"type": "question_toggle"', source)
        self.assertIn('"type": "question_submit"', source)
        self.assertIn('"prompt_id": event.prompt_id', source)
        self.assertIn("selected_options", source)


if __name__ == "__main__":
    unittest.main()
