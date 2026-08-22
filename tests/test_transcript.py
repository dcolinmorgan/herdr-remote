"""Tests for relay/transcript.py -- the agent-transcript reader behind `get_history`.

Fixtures are cut from the real shapes on this machine (196 transcripts, 24716 turns) rather than
invented: the row types, the envelope tags, the duplicate-row replay and the tool_result folding
are all things claude actually writes.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


TRANSCRIPT_PATH = Path(__file__).resolve().parents[1] / "relay" / "transcript.py"


def load_transcript(environment=None):
    with mock.patch.dict(os.environ, environment or {}, clear=False):
        for name, value in (environment or {}).items():
            if value is None:
                os.environ.pop(name, None)
        spec = importlib.util.spec_from_file_location("herdr_transcript_under_test", TRANSCRIPT_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


transcript = load_transcript()


SESSION = "1b3d9f8a-2c4e-4a6b-8d0f-112233445566"


def row(**fields):
    fields.setdefault("uuid", f"u{fields.pop('_n', 0)}")
    fields.setdefault("timestamp", "2026-08-21T00:00:00.000Z")
    return fields


def user_row(content, uuid="u1", **extra):
    return row(type="user", uuid=uuid, message={"role": "user", "content": content}, **extra)


def assistant_row(blocks, uuid="a1", **extra):
    return row(type="assistant", uuid=uuid, message={"role": "assistant", "content": blocks}, **extra)


def write_transcript(root, rows, session=SESSION, project="-home-someone-project"):
    directory = Path(root) / project
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session}.jsonl"
    with open(path, "w", encoding="utf-8") as handle:
        for item in rows:
            handle.write(json.dumps(item) + "\n")
    return path


def parse(rows):
    return transcript.parse_claude(json.dumps(item) for item in rows)


class ParseTests(unittest.TestCase):
    def test_a_plain_exchange_becomes_two_turns_with_a_title(self):
        turns, title = parse([
            {"type": "ai-title", "aiTitle": "first guess"},
            user_row("does this work?"),
            assistant_row([{"type": "text", "text": "it does"}]),
            {"type": "ai-title", "aiTitle": "final title"},
        ])
        self.assertEqual(title, "final title")  # the last one wins
        self.assertEqual([(t["role"], t["text"]) for t in turns],
                         [("user", "does this work?"), ("assistant", "it does")])
        self.assertEqual(turns[0]["ts"], "2026-08-21T00:00:00.000Z")
        self.assertFalse(turns[0]["truncated"])

    def test_thinking_blocks_are_dropped_and_tool_use_becomes_one_line(self):
        turns, _ = parse([assistant_row([
            {"type": "thinking", "thinking": "a long private deliberation"},
            {"type": "text", "text": "here goes"},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls -la /tmp"}},
        ])])
        self.assertEqual([(t["role"], t["text"]) for t in turns],
                         [("assistant", "here goes"), ("tool", "Bash(ls -la /tmp)")])
        # Two turns off one row still need distinct ids, because ids are pagination cursors.
        self.assertEqual(len({t["uuid"] for t in turns}), 2)

    def test_a_tool_result_folds_into_the_call_it_answers(self):
        turns, _ = parse([
            assistant_row([{"type": "tool_use", "id": "t1", "name": "Read",
                            "input": {"file_path": "/etc/hosts"}}]),
            user_row([{"type": "tool_result", "tool_use_id": "t1",
                       "content": "127.0.0.1 localhost\nmore lines"}], uuid="u2"),
        ])
        self.assertEqual(len(turns), 1)  # the result is not a turn of its own
        self.assertEqual(turns[0]["role"], "tool")
        self.assertEqual(turns[0]["text"], "Read(/etc/hosts) → 127.0.0.1 localhost")

    def test_a_failed_tool_result_is_marked(self):
        turns, _ = parse([
            assistant_row([{"type": "tool_use", "id": "t1", "name": "Bash",
                            "input": {"command": "false"}}]),
            user_row([{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                       "content": [{"type": "text", "text": "exit 1"}]}], uuid="u2"),
        ])
        self.assertEqual(turns[0]["text"], "Bash(false) ! exit 1")

    def test_the_user_channel_envelopes_each_land_where_they_belong(self):
        cases = [
            # (row, expected (role, text) or None when the row is not a turn)
            (user_row("plain words"), ("user", "plain words")),
            (user_row("<system-reminder>context for the model</system-reminder>"), None),
            (user_row("<local-command-caveat>ignore this</local-command-caveat>"), None),
            # isMeta is how claude marks an injected envelope; a skill body arrives on this shape.
            (user_row([{"type": "text", "text": "# Skill\nrules"}], isMeta=True), None),
            (user_row("[Image: original 1440x2337]", isMeta=True), None),
            (user_row("<command-name>/compact</command-name>\n<command-args></command-args>"),
             ("user", "/compact")),
            # The tags come in either order in the wild.
            (user_row("<command-message>grill-me</command-message>\n"
                      "<command-name>/grill-me</command-name>\n<command-args>go deep</command-args>"),
             ("user", "/grill-me go deep")),
            (user_row("<local-command-stdout>Set model to Opus 5</local-command-stdout>"),
             ("note", "Set model to Opus 5")),
            (user_row("<bash-input> git status</bash-input>"), ("user", "! git status")),
            (user_row("<task-notification>\n<task-id>bz3</task-id>\n"
                      "<summary>Monitor event: read the file</summary>\n"
                      "<event>a whole pile of context</event>\n</task-notification>"),
             ("note", "Monitor event: read the file")),
            (user_row([{"type": "text", "text": "[Request interrupted by user]"}],
                      interruptedMessageId="a1"),
             ("note", "[Request interrupted by user]")),
            (user_row("This session is being continued from a previous conversation",
                      isCompactSummary=True),
             ("note", "This session is being continued from a previous conversation")),
            (user_row(""), None),
        ]
        for source, expected in cases:
            with self.subTest(content=json.dumps(source.get("message"))[:60]):
                turns, _ = parse([source])
                actual = (turns[0]["role"], turns[0]["text"]) if turns else None
                self.assertEqual(actual, expected)

    def test_system_rows_keep_only_the_ones_that_carry_meaning(self):
        turns, _ = parse([
            {"type": "system", "uuid": "s1", "subtype": "turn_duration", "durationMs": 1567397},
            {"type": "system", "uuid": "s2", "subtype": "compact_boundary",
             "content": "Conversation compacted"},
            {"type": "system", "uuid": "s3", "subtype": "away_summary",
             "content": "Goal was simplifying the UI; done and committed."},
            {"type": "system", "uuid": "s4", "subtype": "stop_hook_summary", "content": None},
        ])
        self.assertEqual([t["text"] for t in turns],
                         ["Conversation compacted", "Goal was simplifying the UI; done and committed."])
        self.assertTrue(all(t["role"] == "note" for t in turns))

    def test_an_api_error_row_is_a_note_not_the_agent_talking(self):
        turns, _ = parse([assistant_row([{"type": "text", "text": "API Error: overloaded"}],
                                        isApiErrorMessage=True)])
        self.assertEqual(turns[0]["role"], "note")

    def test_sidechain_rows_are_dropped(self):
        turns, _ = parse([
            user_row("what a subagent was told", isSidechain=True),
            assistant_row([{"type": "text", "text": "what it answered"}], isSidechain=True),
            user_row("what the operator said", uuid="u9"),
        ])
        self.assertEqual([t["text"] for t in turns], ["what the operator said"])

    def test_replayed_rows_are_not_replayed_turns(self):
        """A resumed session re-appends what it loaded: 591 of 2602 rows in one real transcript."""
        duplicated = user_row("said once", uuid="dup")
        turns, _ = parse([duplicated, duplicated, assistant_row([{"type": "text", "text": "ack"}])])
        self.assertEqual([t["text"] for t in turns], ["said once", "ack"])

    def test_a_torn_last_line_and_a_non_object_row_are_skipped_not_raised(self):
        lines = [
            json.dumps(user_row("intact")),
            "[1, 2, 3]",
            "not json at all",
            json.dumps(assistant_row([{"type": "text", "text": "also intact"}]))[:40],
        ]
        turns, _ = transcript.parse_claude(lines)
        self.assertEqual([t["text"] for t in turns], ["intact"])

    def test_long_text_is_clipped_and_flagged(self):
        turns, _ = parse([user_row("x" * (transcript.TEXT_LIMIT + 500))])
        self.assertEqual(len(turns[0]["text"]), transcript.TEXT_LIMIT)
        self.assertTrue(turns[0]["truncated"])

    def test_ansi_escapes_never_reach_a_turn(self):
        turns, _ = parse([user_row("<local-command-stdout>\x1b[2mCompacted\x1b[22m</local-command-stdout>")])
        self.assertEqual(turns[0]["text"], "Compacted")


class ToolCallTests(unittest.TestCase):
    """A file edit carries both sides in its own input; none of it used to survive parsing."""

    def tool_turn(self, name, args):
        turns, _ = parse([assistant_row(
            [{"type": "tool_use", "id": "t1", "name": name, "input": args}], uuid="a1")])
        self.assertEqual(len(turns), 1)
        return turns[0]

    def test_an_edit_becomes_a_diff_of_its_two_sides(self):
        turn = self.tool_turn("Edit", {
            "file_path": "/repo/relay/herdr_relay.py",
            "old_string": "def a():\n    return 1\n\n\ndef b():\n    pass\n",
            "new_string": "def a():\n    return 2\n\n\ndef b():\n    pass\n",
        })
        self.assertEqual(turn["tool"], "Edit")
        self.assertEqual(turn["target"], "/repo/relay/herdr_relay.py")
        self.assertEqual(turn["diff"].splitlines(),
                         [" def a():", "-    return 1", "+    return 2", " ", " ", " def b():"])
        self.assertEqual((turn["added"], turn["removed"]), (1, 1))
        self.assertNotIn("diff_clipped", turn)
        # The one-line summary is untouched: every other client renders that string and knows
        # nothing about these fields.
        self.assertEqual(turn["text"], "Edit(/repo/relay/herdr_relay.py)")

    def test_no_line_numbers_are_invented_for_a_fragment(self):
        """old_string is a fragment of the file, so difflib's @@ numbers count from the fragment --
        they would not match the editor the reader is about to open. A gap becomes `...`."""
        lines = [f"line {i:02d}" for i in range(40)]
        changed = list(lines)
        changed[2] = "CHANGED early"
        changed[30] = "CHANGED late"
        turn = self.tool_turn("Edit", {"file_path": "/repo/f", "old_string": "\n".join(lines),
                                       "new_string": "\n".join(changed)})
        self.assertNotIn("@@", turn["diff"])
        self.assertIn(transcript.DIFF_GAP, turn["diff"].splitlines())
        self.assertEqual((turn["added"], turn["removed"]), (2, 2))

    def test_a_removed_line_that_starts_with_dashes_survives(self):
        """It renders as `---flag=1`, which a prefix test for difflib's header would have eaten."""
        turn = self.tool_turn("Edit", {"file_path": "/repo/f",
                                       "old_string": "--flag=1\nkeep\n", "new_string": "keep\n"})
        self.assertEqual(turn["diff"].splitlines(), ["---flag=1", " keep"])
        self.assertEqual((turn["added"], turn["removed"]), (0, 1))

    def test_a_write_is_all_additions_and_only_its_head(self):
        content = "\n".join(f"line {i}" for i in range(200))
        turn = self.tool_turn("Write", {"file_path": "/repo/new.py", "content": content})
        lines = turn["diff"].splitlines()
        self.assertEqual(len(lines), transcript.DIFF_MAX_LINES)
        self.assertTrue(all(line.startswith("+") for line in lines))
        # The count is the whole file even though the body is its head, so a client can say
        # "+200, showing 40" rather than implying the file is 40 lines long.
        self.assertEqual((turn["added"], turn["removed"]), (200, 0))
        self.assertTrue(turn["diff_clipped"])

    def test_a_single_enormous_line_is_capped_by_characters_too(self):
        turn = self.tool_turn("Write", {"file_path": "/repo/bundle.js", "content": "x" * 9000})
        self.assertLessEqual(len(turn["diff"]), transcript.DIFF_MAX_CHARS)
        self.assertTrue(turn["diff_clipped"])

    def test_multiedit_shows_every_edit_separated(self):
        turn = self.tool_turn("MultiEdit", {"file_path": "/repo/f", "edits": [
            {"old_string": "one\n", "new_string": "uno\n"},
            {"old_string": "two\n", "new_string": "dos\n"},
        ]})
        self.assertEqual(turn["diff"].splitlines(),
                         ["-one", "+uno", transcript.DIFF_GAP, "-two", "+dos"])
        self.assertEqual((turn["added"], turn["removed"]), (2, 2))

    def test_a_tool_that_changes_no_file_carries_no_diff(self):
        turn = self.tool_turn("Bash", {"command": "ls -la", "description": "list"})
        self.assertEqual(turn["tool"], "Bash")
        self.assertEqual(turn["target"], "ls -la")
        self.assertNotIn("diff", turn)
        # A search leads with what it searched for, and a tool with none of the known keys falls
        # back to its own JSON rather than showing just its name.
        self.assertEqual(self.tool_turn("ToolSearch", {"query": "select:Read", "max_results": 2})
                         ["target"], "select:Read")
        self.assertEqual(self.tool_turn("Odd", {"weird": 3})["target"], '{"weird": 3}')

    def test_a_malformed_edit_input_does_not_raise(self):
        for args in ({"file_path": "/f"}, {"old_string": None, "new_string": "x"},
                     {"edits": "not a list"}, {"edits": [None, {"old_string": 1}]}, "not a dict"):
            turn = self.tool_turn("Edit", args)
            self.assertNotIn("diff", turn)

    def test_a_failed_tool_call_says_so(self):
        turns, _ = parse([
            assistant_row([{"type": "tool_use", "id": "t1", "name": "Bash",
                            "input": {"command": "false"}}], uuid="a1"),
            user_row([{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                       "content": "exit status 1"}], uuid="u2"),
        ])
        self.assertTrue(turns[0]["error"])
        self.assertIn("!", turns[0]["text"])

    def test_a_successful_tool_call_is_not_flagged(self):
        turns, _ = parse([
            assistant_row([{"type": "tool_use", "id": "t1", "name": "Bash",
                            "input": {"command": "true"}}], uuid="a1"),
            user_row([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}], uuid="u2"),
        ])
        self.assertNotIn("error", turns[0])


class PaginationTests(unittest.TestCase):
    def turns(self, count=10, every_other_tool=True):
        rows = []
        for index in range(count):
            if every_other_tool and index % 2:
                rows.append(assistant_row(
                    [{"type": "tool_use", "id": f"t{index}", "name": "Bash",
                      "input": {"command": f"echo {index}"}}], uuid=f"a{index}"))
            else:
                rows.append(user_row(f"message {index}", uuid=f"u{index}"))
        return parse(rows)[0]

    def test_the_newest_page_comes_back_oldest_first(self):
        page, total, has_more = transcript.paginate(self.turns(10), 2, None, False)
        self.assertEqual([t["text"] for t in page], ["message 6", "message 8"])
        self.assertEqual(total, 5)
        self.assertTrue(has_more)

    def test_a_cursor_walks_backwards_without_gaps_or_repeats(self):
        turns = self.turns(20)
        seen, cursor = [], None
        while True:
            page, total, has_more = transcript.paginate(turns, 3, cursor, False)
            seen = page + seen
            if not has_more or not page:
                break
            cursor = page[0]["uuid"]
        self.assertEqual(len(seen), total)
        self.assertEqual(len({t["uuid"] for t in seen}), total)

    def test_an_unknown_cursor_degrades_to_the_newest_page(self):
        turns = self.turns(10)
        newest, _, _ = transcript.paginate(turns, 2, None, False)
        page, _, _ = transcript.paginate(turns, 2, "a-cursor-from-a-rewritten-file", False)
        self.assertEqual(page, newest)

    def test_tool_turns_neither_show_nor_consume_a_slot_by_default(self):
        turns = self.turns(10)
        without, total_without, _ = transcript.paginate(turns, 5, None, False)
        with_tools, total_with, _ = transcript.paginate(turns, 5, None, True)
        self.assertEqual(total_without, 5)
        self.assertEqual(total_with, 10)
        self.assertNotIn("tool", [t["role"] for t in without])
        self.assertIn("tool", [t["role"] for t in with_tools])

    def test_the_byte_budget_cuts_a_page_short_and_says_there_is_more(self):
        turns, _ = parse([user_row("x" * 3000, uuid=f"u{index}") for index in range(200)])
        page, total, has_more = transcript.paginate(turns, 200, None, False)
        self.assertEqual(total, 200)
        self.assertLess(len(page), 200)
        self.assertTrue(has_more)
        self.assertLessEqual(sum(len(t["text"]) for t in page), transcript.PAGE_TEXT_BUDGET)

    def test_one_oversized_turn_is_still_returned(self):
        turns, _ = parse([user_row("x" * transcript.TEXT_LIMIT)])
        with mock.patch.object(transcript, "PAGE_TEXT_BUDGET", 10):
            page, _, has_more = transcript.paginate(turns, 200, None, False)
        self.assertEqual(len(page), 1)
        self.assertFalse(has_more)


class DiffBudgetTests(unittest.TestCase):
    def edits(self, count, lines_each):
        """`count` Edit calls, each with a diff of roughly `lines_each` lines."""
        rows = []
        for index in range(count):
            old = "\n".join(f"old {index} {i}" for i in range(lines_each))
            new = "\n".join(f"new {index} {i}" for i in range(lines_each))
            rows.append(assistant_row(
                [{"type": "tool_use", "id": f"t{index}", "name": "Edit",
                  "input": {"file_path": f"/repo/f{index}", "old_string": old,
                            "new_string": new}}], uuid=f"a{index}"))
        return parse(rows)[0]

    def test_a_page_of_diffs_stays_inside_the_advertised_budget(self):
        turns = self.edits(120, transcript.DIFF_MAX_LINES)
        page, total, has_more = transcript.paginate(turns, 120, None, True)
        self.assertEqual(total, 120)
        payload = sum(len(t["text"]) + len(t.get("diff") or "") for t in page)
        self.assertLessEqual(payload, transcript.PAGE_TEXT_BUDGET)
        # Cut by the budget, not by the limit -- and the rest is still reachable.
        self.assertLess(len(page), 120)
        self.assertTrue(has_more)

    def test_the_newest_turn_is_served_even_if_it_alone_blows_the_budget(self):
        turns = self.edits(1, transcript.DIFF_MAX_LINES)
        turns[0]["text"] = "x" * (transcript.PAGE_TEXT_BUDGET * 2)
        page, _, _ = transcript.paginate(turns, 10, None, True)
        self.assertEqual(len(page), 1)

    def test_diffs_cost_nothing_when_tools_are_filtered_out(self):
        turns = self.edits(120, transcript.DIFF_MAX_LINES)
        page, total, has_more = transcript.paginate(turns, 120, None, False)
        self.assertEqual((page, total, has_more), ([], 0, False))


class LocateTests(unittest.TestCase):
    def test_a_uuid_is_required_before_any_path_is_built(self):
        rejected = [
            "../../../etc/passwd",
            "1b3d9f8a-2c4e-4a6b-8d0f-112233445566/../../secret",
            "1b3d9f8a2c4e4a6b8d0f112233445566",
            "zzzzzzzz-2c4e-4a6b-8d0f-112233445566",
            "1b3d9f8a-2c4e-4a6b-8d0f-11223344556",
            "",
        ]
        for value in rejected:
            with self.subTest(value=value):
                self.assertIsNone(transcript.UUID_RE.match(value))
                body = transcript.history({"kind": "id", "value": value}, agent="claude")
                self.assertEqual(body["unavailable"], "no-session")
        self.assertTrue(transcript.UUID_RE.match(SESSION))

    def test_roots_are_searched_in_order(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            write_transcript(second, [user_row("in the second root")])
            self.assertIsNone(transcript.locate_claude(SESSION, roots=[first]))
            found = transcript.locate_claude(SESSION, roots=[first, second])
            self.assertTrue(found.startswith(second))

    def test_a_path_style_session_ref_is_refused_until_a_harness_needs_it(self):
        body = transcript.history({"kind": "path", "value": "/home/x/.pi/session.jsonl"},
                                  agent="claude")
        self.assertEqual(body["unavailable"], "no-session")

    def test_a_harness_with_no_reader_says_unsupported_not_no_session(self):
        body = transcript.history({"kind": "id", "value": SESSION, "agent": "opencode"})
        self.assertEqual(body["unavailable"], "unsupported")
        self.assertEqual(body["agent"], "opencode")


class ReadAndCacheTests(unittest.TestCase):
    def setUp(self):
        transcript.cache_clear()
        self.addCleanup(transcript.cache_clear)

    def test_a_second_call_reuses_the_parse(self):
        with tempfile.TemporaryDirectory() as root:
            write_transcript(root, [user_row("cached?")])
            with mock.patch.object(transcript, "LOCAL_ROOTS", [root]), \
                 mock.patch.object(transcript, "read_local", wraps=transcript.read_local) as reader:
                first = transcript.history({"kind": "id", "value": SESSION}, agent="claude")
                second = transcript.history({"kind": "id", "value": SESSION}, agent="claude")
            self.assertEqual(reader.call_count, 1)
            self.assertEqual(first["messages"], second["messages"])

    def test_a_grown_file_is_reparsed(self):
        with tempfile.TemporaryDirectory() as root:
            path = write_transcript(root, [user_row("first")])
            with mock.patch.object(transcript, "LOCAL_ROOTS", [root]):
                before = transcript.history({"kind": "id", "value": SESSION}, agent="claude")
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(user_row("second", uuid="u2")) + "\n")
                after = transcript.history({"kind": "id", "value": SESSION}, agent="claude")
        self.assertEqual([m["text"] for m in before["messages"]], ["first"])
        self.assertEqual([m["text"] for m in after["messages"]], ["first", "second"])

    def test_the_cache_holds_a_bounded_number_of_sessions(self):
        for index in range(transcript.CACHE_SIZE + 2):
            transcript.cache_put((None, f"/p{index}"), (index,), ([], "", False))
        self.assertEqual(len(transcript._cache), transcript.CACHE_SIZE)
        self.assertEqual(transcript.cache_peek_size((None, "/p0")), 0)  # evicted

    def test_only_the_tail_of_an_oversized_file_is_read(self):
        with tempfile.TemporaryDirectory() as root:
            rows = [user_row(f"message {index}", uuid=f"u{index}") for index in range(50)]
            path = write_transcript(root, rows)
            size = os.path.getsize(path)
            with mock.patch.object(transcript, "MAX_BYTES", size // 2), \
                 mock.patch.object(transcript, "TAIL_BYTES", size // 4):
                lines, file_truncated = transcript.read_local(str(path))
        self.assertTrue(file_truncated)
        turns, _ = transcript.parse_claude(lines)
        self.assertLess(len(turns), 50)
        self.assertEqual(turns[-1]["text"], "message 49")
        # The tail starts mid-line; that fragment must not reach the parser as a torn row.
        self.assertTrue(all(t["text"].startswith("message ") for t in turns))


class DegradationTests(unittest.TestCase):
    def test_the_switch_turns_it_off(self):
        disabled = load_transcript({"HERDR_TRANSCRIPT": "0"})
        self.assertFalse(disabled.ENABLED)
        self.assertEqual(disabled.history({"kind": "id", "value": SESSION})["unavailable"],
                         "disabled")

    def test_a_pane_with_no_session_ref(self):
        self.assertEqual(transcript.history(None)["unavailable"], "no-session")
        self.assertEqual(transcript.history({})["unavailable"], "no-session")

    def test_a_session_whose_file_is_not_there_yet(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(transcript, "LOCAL_ROOTS", [root]):
                body = transcript.history({"kind": "id", "value": SESSION}, agent="claude")
        self.assertEqual(body["unavailable"], "no-log")

    def test_a_read_failure_reports_error_without_leaking_the_exception(self):
        logger = mock.Mock()
        with tempfile.TemporaryDirectory() as root:
            write_transcript(root, [user_row("unreadable")])
            with mock.patch.object(transcript, "LOCAL_ROOTS", [root]), \
                 mock.patch.object(transcript, "read_local",
                                   side_effect=OSError("/secret/path is not readable")):
                body = transcript.history({"kind": "id", "value": SESSION}, agent="claude", log=logger)
        self.assertEqual(body["unavailable"], "error")
        self.assertEqual(body["messages"], [])
        self.assertNotIn("secret", json.dumps(body))
        logger.exception.assert_called_once()

    def test_a_nonsense_limit_falls_back_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as root:
            write_transcript(root, [user_row("still answered")])
            with mock.patch.object(transcript, "LOCAL_ROOTS", [root]):
                transcript.cache_clear()
                for limit in ("lots", None, 0, -5, 10 ** 9, {"n": 3}):
                    with self.subTest(limit=limit):
                        body = transcript.history({"kind": "id", "value": SESSION},
                                                  agent="claude", limit=limit)
                        self.assertEqual([m["text"] for m in body["messages"]], ["still answered"])

    def test_a_bad_byte_limit_in_the_environment_does_not_stop_the_relay(self):
        module = load_transcript({"HERDR_TRANSCRIPT_MAX_BYTES": "not a number"})
        self.assertEqual(module.MAX_BYTES, 64 * 1024 * 1024)


class RemoteTests(unittest.TestCase):
    def setUp(self):
        transcript.cache_clear()
        self.addCleanup(transcript.cache_clear)

    def rows_blob(self, rows, size=None):
        body = "".join(json.dumps(item) + "\n" for item in rows).encode()
        return f"SIZE {size if size is not None else len(body)}\n".encode() + body

    def test_a_size_frame_and_a_tail_become_turns(self):
        calls = []

        def runner(remote, script, ssh_args=()):
            calls.append((remote, script, ssh_args))
            return 0, self.rows_blob([user_row("from the far side")])

        body = transcript.history({"kind": "id", "value": SESSION}, remote="build-box",
                                 agent="claude", ssh_args=("-o", "BatchMode=yes"),
                                 remote_runner=runner)
        self.assertEqual([m["text"] for m in body["messages"]], ["from the far side"])
        self.assertFalse(body["file_truncated"])
        self.assertEqual(len(calls), 1)
        remote, script, ssh_args = calls[0]
        self.assertEqual(remote, "build-box")
        self.assertEqual(ssh_args, ("-o", "BatchMode=yes"))
        # The only interpolated value is a validated uuid, and the far side needs nothing but sh.
        self.assertIn(f"{SESSION}.jsonl", script)
        self.assertNotIn(";rm", script)
        for word in ("ls", "wc", "tail", "head"):
            self.assertIn(word, script)

    def test_nofile_is_no_log(self):
        body = transcript.history({"kind": "id", "value": SESSION}, remote="build-box",
                                 agent="claude", remote_runner=lambda *a, **k: (0, b"NOFILE\n"))
        self.assertEqual(body["unavailable"], "no-log")

    def test_short_bytes_against_the_reported_size_means_truncated(self):
        rows = [user_row(f"message {index}", uuid=f"u{index}") for index in range(5)]
        blob = self.rows_blob(rows, size=10 ** 7)
        body = transcript.history({"kind": "id", "value": SESSION}, remote="build-box",
                                 agent="claude", remote_runner=lambda *a, **k: (0, blob))
        self.assertTrue(body["file_truncated"])
        # The first line of a tail is a fragment and is dropped, so message 0 is gone.
        self.assertEqual([m["text"] for m in body["messages"]],
                         ["message 1", "message 2", "message 3", "message 4"])

    def test_an_unchanged_size_answers_cached_and_moves_no_bytes(self):
        blob = self.rows_blob([user_row("only fetched once")])
        scripts = []

        def runner(remote, script, ssh_args=()):
            scripts.append(script)
            if "CACHED" in script and len(scripts) > 1:
                return 0, b"CACHED\n"
            return 0, blob

        first = transcript.history({"kind": "id", "value": SESSION}, remote="build-box",
                                  agent="claude", remote_runner=runner)
        second = transcript.history({"kind": "id", "value": SESSION}, remote="build-box",
                                   agent="claude", remote_runner=runner)
        self.assertEqual(first["messages"], second["messages"])
        self.assertNotIn("CACHED", scripts[0])  # nothing cached yet, no shortcut offered
        self.assertIn("CACHED", scripts[1])     # second probe offers the size it already has

    def test_cached_after_an_eviction_refetches_instead_of_returning_nothing(self):
        blob = self.rows_blob([user_row("still here")])
        replies = [(0, blob), (0, b"CACHED\n"), (0, blob)]

        def runner(remote, script, ssh_args=()):
            return replies.pop(0)

        transcript.history({"kind": "id", "value": SESSION}, remote="box", agent="claude",
                           remote_runner=runner)
        transcript.cache_clear()  # evicted between the probe and the read
        body = transcript.history({"kind": "id", "value": SESSION}, remote="box", agent="claude",
                                  remote_runner=runner)
        self.assertEqual([m["text"] for m in body["messages"]], ["still here"])
        self.assertEqual(replies, [])

    def test_a_failed_ssh_is_an_error_not_an_empty_history(self):
        body = transcript.history({"kind": "id", "value": SESSION}, remote="box", agent="claude",
                                  remote_runner=lambda *a, **k: (255, b""))
        self.assertEqual(body["unavailable"], "error")

    def test_an_unexpected_frame_is_an_error(self):
        body = transcript.history({"kind": "id", "value": SESSION}, remote="box", agent="claude",
                                  remote_runner=lambda *a, **k: (0, b"bash: ls: command not found\n"))
        self.assertEqual(body["unavailable"], "error")

    def test_a_root_that_would_not_survive_a_shell_is_dropped(self):
        with self.assertRaises(ValueError):
            transcript.remote_probe_script(SESSION, ["$HOME/x; rm -rf /"], 0, 4096)
        script = transcript.remote_probe_script(SESSION, ["$HOME/.claude/projects"], 0, 4096)
        self.assertIn("$HOME/.claude/projects", script)


if __name__ == "__main__":
    unittest.main()
