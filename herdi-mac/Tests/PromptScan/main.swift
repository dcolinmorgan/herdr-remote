// Tests for PromptScan.swift. Run with herdi-mac/test.sh.
//
// expected.json holds what the relay's detect_numbered_options returns for each fixture, and
// tests/test_herdr_relay.py checks the relay against the same file. One file keeps the two
// scanners from drifting apart.
import Foundation

let root = URL(fileURLWithPath: CommandLine.arguments[1])
var failures = 0

func check(_ name: String, _ ok: Bool, _ detail: @autoclosure () -> String = "") {
    if ok {
        print("  pass: \(name)")
    } else {
        failures += 1
        print("  FAIL: \(name) \(detail())")
    }
}

func fixture(_ name: String) -> [String] {
    let url = root.appendingPathComponent("fixtures/\(name).txt")
    return (try? String(contentsOf: url, encoding: .utf8))?.components(separatedBy: .newlines) ?? []
}

let expectedData = (try? Data(contentsOf: root.appendingPathComponent("expected.json"))) ?? Data()
let expected = (try? JSONDecoder().decode([String: [String]].self, from: expectedData)) ?? [:]
check("expected.json loads", !expected.isEmpty)

for (name, labels) in expected.sorted(by: { $0.key < $1.key }) {
    let found = detectNumberedOptions(fixture(name)).labels
    check("matches the relay on \(name)", found == labels, "got \(found)")
}

check("offers no options on a checkbox menu, where a number only ticks a box",
      menuOptions(fixture("checkbox")).labels.isEmpty)

let grant = menuOptions(fixture("wrapped-grant")).labels
check("sees the standing grant in a wrapped option", grant.count == 3 && grantsStanding(grant[1]))
check("sees no standing grant in a plain yes", grant.first.map { !grantsStanding($0) } ?? false)
let curly = menuOptions(fixture("wrapped-grant-curly")).labels
check("sees the standing grant behind a curly apostrophe, as Claude prints it",
      curly.count == 3 && grantsStanding(curly[1]), "got \(curly)")

let session = menuOptions(fixture("edit-session-grant")).labels
check("treats every yes but a plain yes as a grant",
      session.map(grantsStanding) == [false, true, true, false], "got \(session.map(grantsStanding))")
check("treats no as no grant", !grantsStanding("No, and tell Claude what to do differently (esc)"))

let codex = menuOptions(fixture("codex-approval")).labels
check("treats codex's yes, proceed as a one-time answer, and its don't ask again as a grant",
      codex.map(grantsStanding) == [false, true, false], "got \(codex.map(grantsStanding))")
check("shows an option without its key hint", optionTitle("Yes, proceed (y)") == "Yes, proceed")
check("keeps a bracket that is not a key hint", optionTitle("Blue (Recommended)") == "Blue (Recommended)")

for (label, kind) in [("Yes", OptionKind.once), ("Yes, proceed (y)", .once), ("Allow once", .once),
                      ("Allow always", .grant), ("Yes, and don’t ask again", .grant),
                      ("No, and tell Claude what to do differently (esc)", .refuse),
                      ("Don’t allow", .refuse), ("Do not allow", .refuse), ("Disallow", .refuse),
                      ("Deny", .refuse), ("Reject", .refuse), ("Green", .other)] {
    check("reads \"\(label)\" as \(kind)", optionKind(label) == kind, "got \(optionKind(label))")
}

func shortcuts(_ options: [(String, Int?)]) -> [String] {
    optionShortcuts(options.map { (label: $0.0, number: $0.1) }).map { shortcut in
        shortcut.map { ($0.shift ? "⇧" : "") + String($0.key) } ?? "-"
    }
}

let codexKeys = shortcuts([("Yes, proceed (y)", 1), ("Yes, and don't ask again (p)", 2),
                           ("No, and tell Codex what to do differently (esc)", 3)])
check("gives codex's options the keys codex prints, with Shift on the grant",
      codexKeys == ["y", "⇧p", "."], "got \(codexKeys)")
check("gives a numbered option with no hint its number, and a grant ⌘⇧Y",
      shortcuts([("Yes", 1), ("Yes, and don't ask again", 2), ("No", 3)]) == ["1", "⇧y", "3"])
check("never gives a grant a key without Shift, whatever key the screen prints",
      shortcuts([("Yes", nil), ("Yes, and don't ask again (y)", nil)]) == ["y", "⇧y"])
check("falls back to the kind's key when the agent shows no key",
      shortcuts([("Allow once", nil), ("Allow always", nil), ("Reject", nil)]) == ["y", "⇧y", "n"])
check("never gives ⌘N or ⌘. to an option that is not a no",
      shortcuts([("Yes, proceed (n)", 1), ("Continue (esc)", 2), ("Yes (n)", nil)]) == ["1", "2", "y"])
check("never gives ⌘Y to an option that is not a yes",
      shortcuts([("Approve for this session (y)", 2), ("No (y)", 3)]) == ["2", "3"])
check("leaves a key the reply field needs to the reply field",
      shortcuts([("Accept all (a)", 4)]) == ["4"])
check("binds a key only once",
      shortcuts([("No (n)", nil), ("Reject (n)", nil)]) == ["n", "-"])
check("gives an option past 9 with no hint no key", shortcuts([("Green", 12)]) == ["-"])

let question = fixture("question-with-descriptions")
let excerpt = promptExcerpt(question, menuOptions(question))
check("starts the card text below the divider",
      excerpt.hasPrefix("←  ☐ Colour"), "got \(excerpt.prefix(40))")
check("keeps every option in the card text", excerpt.contains("Chat about this"))

check("sees the same prompt still on screen", promptStillShown(excerpt, lines: question))
check("sees that a new prompt replaced the one on the card",
      !promptStillShown(excerpt, lines: fixture("second-question")))
check("sees that the prompt is gone", !promptStillShown(excerpt, lines: ["$ "]))
check("never matches a card with no prompt", !promptStillShown(nil, lines: question))

print(failures == 0 ? "PromptScan tests passed" : "PromptScan tests: \(failures) failed")
exit(failures == 0 ? 0 : 1)
