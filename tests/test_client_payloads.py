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

    def test_windows_island_paints_its_own_silhouette(self):
        """A Shape here paints nothing, and an unpainted island cannot be hovered.

        Shape seeds its rendered geometry to Geometry.Empty and only drops that cache from
        its own ArrangeOverride, which a geometry derived from RenderSize must override —
        so the island stayed fully transparent. Transparency also punches hit-test holes:
        the pointer falls through to the window, which is not a descendant of Root, so WPF
        raises MouseLeave and collapses the island mid-hover. Root's Background covers the
        gaps its children leave.
        """
        shape = (ROOT / "herdi-win" / "Controls" / "IslandShape.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("class IslandShape : FrameworkElement", shape)
        self.assertNotIn("class IslandShape : Shape", shape)
        self.assertIn("override void OnRender(", shape)
        self.assertIn("DrawGeometry(", shape)

        island = (ROOT / "herdi-win" / "Views" / "IslandWindow.xaml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(island, r'x:Name="Root"[^>]*Background="Transparent"')

    def test_tui_sends_multi_selection_messages_with_prompt_identity(self):
        source = (ROOT / "relay" / "herdr_tui.py").read_text(encoding="utf-8")

        self.assertIn('"type": "question_toggle"', source)
        self.assertIn('"type": "question_submit"', source)
        self.assertIn('"prompt_id": event.prompt_id', source)
        self.assertIn("selected_options", source)


if __name__ == "__main__":
    unittest.main()
