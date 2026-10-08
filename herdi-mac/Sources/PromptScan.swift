import Foundation

/// A numbered menu found on screen. `firstLine` and `lastLine` index into the lines it was read
/// from, so the card can show the question along with its options.
struct NumberedMenu {
    let labels: [String]
    let firstLine: Int?
    let lastLine: Int?

    static let none = NumberedMenu(labels: [], firstLine: nil, lastLine: nil)
}

private let numberedOption = try! NSRegularExpression(
    pattern: #"^(\s*(?:[❯>›»▶]\s*)?)(\d{1,2})[.)]\s+(\S.*?)\s*$"#
)
private let menuRule = try! NSRegularExpression(pattern: #"^[\x{2500}-\x{257f}\x{2014}\x{2013}\-=_]{3,}$"#)
private let checkboxRow = try! NSRegularExpression(
    pattern: #"^\s*(?:[❯>›»▶]\s*)?\d{1,2}[.)]\s+\[[ xX✔✓]?\]"#
)

private func matches(_ regex: NSRegularExpression, _ text: String) -> NSTextCheckingResult? {
    regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text))
}

private func group(_ match: NSTextCheckingResult, _ index: Int, in text: String) -> String {
    Range(match.range(at: index), in: text).map { String(text[$0]) } ?? ""
}

/// A port of the relay's `detect_numbered_options`, rule for rule, so Herdi reads a menu the way
/// the web and Telegram clients do. tests/test_herdr_relay.py and herdi-mac/test.sh check both
/// against the same expected output.
///
/// A run starts at `1.` and counts up by one. A line indented deeper than the numbers continues
/// the label above it, which is how Claude wraps a long option and draws an option's description.
/// A divider inside the menu is skipped. The last complete run of two or more wins.
func detectNumberedOptions(_ lines: [String]) -> NumberedMenu {
    var best = NumberedMenu.none
    var current: [String] = []
    var currentStart = 0
    var numberColumn: Int?

    for (index, line) in lines.enumerated() {
        if let match = matches(numberedOption, line) {
            let number = Int(group(match, 2, in: line)) ?? 0
            if number == 1 {
                current = [group(match, 3, in: line)]
                currentStart = index
                numberColumn = (group(match, 1, in: line) as NSString).length
            } else if !current.isEmpty, number == current.count + 1 {
                current.append(group(match, 3, in: line))
            } else {
                current = []
                numberColumn = nil
            }
            if current.count >= 2 {
                best = NumberedMenu(labels: current, firstLine: currentStart, lastLine: index)
            }
            continue
        }
        let stripped = line.trimmingCharacters(in: .whitespacesAndNewlines)
        if stripped.isEmpty { continue }
        if !current.isEmpty, matches(menuRule, stripped) != nil { continue }
        let indent = (line as NSString).length
            - (String(line.drop(while: { $0.isWhitespace })) as NSString).length
        if !current.isEmpty, let numberColumn, indent > numberColumn {
            current[current.count - 1] += " " + stripped
            if current.count >= 2 {
                best = NumberedMenu(labels: current, firstLine: currentStart, lastLine: index)
            }
            continue
        }
        current = []
        numberColumn = nil
    }
    return best
}

/// The menu Herdi offers buttons for. A multi-select checkbox menu gets none: a number there only
/// ticks a box, and nothing reaches the agent until Submit.
func menuOptions(_ lines: [String]) -> NumberedMenu {
    if lines.contains(where: { matches(checkboxRow, $0) != nil }) { return .none }
    return detectNumberedOptions(lines)
}

/// The answers that approve this one prompt and nothing after it.
private let oneTimeYes: Set<String> = ["yes", "y", "yes, proceed", "yes, single permission"]

private let keyHint = try! NSRegularExpression(
    pattern: #"\s*\((?:[a-z]|esc|enter|tab|shift\+tab|ctrl\+[a-z])\)$"#, options: [.caseInsensitive]
)

/// An option as the card shows it: the agent's own words, without the key hint some agents put
/// at the end -- codex's "(y)", Claude's "(esc)". A bracket that is not a key, such as
/// "(Recommended)", stays.
func optionTitle(_ label: String) -> String {
    let range = NSRange(label.startIndex..., in: label)
    return keyHint.stringByReplacingMatches(in: label, range: range, withTemplate: "")
}

/// Whether an option grants more than the prompt on screen. Any "Yes" other than a plain one counts:
/// Claude words its standing grants many ways -- "Yes, and don't ask again", "Yes, allow all edits
/// during this session", "Yes, and switch to auto mode" -- and a new wording must not slip through
/// as a one-time Allow. Claude prints "don’t" with a curly apostrophe, so both apostrophes count.
func grantsStanding(_ label: String) -> Bool {
    let lower = optionTitle(label).lowercased()
        .replacingOccurrences(of: "\u{2019}", with: "'")
        .replacingOccurrences(of: "\u{2018}", with: "'")
        .trimmingCharacters(in: .whitespaces)
    if oneTimeYes.contains(lower) { return false }
    return lower.hasPrefix("yes") || lower.contains("don't ask again") || lower.contains("dont ask again")
        || lower.contains("always") || lower.contains("trust")
}

/// What an option does: a one-time yes, a grant that outlives this prompt, a no, or something else
/// such as an answer to a question. A "no" is checked before a yes, so "Don't allow" and
/// "Disallow" are a no and never a one-time yes. A yes must start with "Yes" or "Allow".
enum OptionKind: Hashable {
    case grant, once, refuse, other
}

func optionKind(_ label: String) -> OptionKind {
    let lower = optionTitle(label).lowercased()
        .replacingOccurrences(of: "\u{2019}", with: "'")
        .trimmingCharacters(in: .whitespaces)
    let refusals = ["no", "n", "deny", "reject", "disallow", "don't", "dont", "do not", "cancel", "exit"]
    if refusals.contains(where: { lower == $0 || lower.hasPrefix($0 + " ") || lower.hasPrefix($0 + ",") }) {
        return .refuse
    }
    if grantsStanding(label) || lower.hasPrefix("approve all") || lower.hasPrefix("allow always") {
        return .grant
    }
    if lower.hasPrefix("yes") || lower == "y" || lower.hasPrefix("allow") {
        return .once
    }
    return .other
}

/// A card shortcut: Command, plus Shift when `shift` is set, plus `key`.
struct OptionShortcut: Equatable {
    let key: Character
    var shift = false
}

/// Keys the card's reply field needs for editing, so no option takes them.
private let editingKeys: Set<Character> = ["a", "c", "v", "x", "z"]

/// The key the agent prints for an option, such as codex's "(p)", as a key Herdi can bind with
/// Command. "(esc)" becomes ".", because ⌘. is the Mac's cancel.
func hintKey(_ label: String) -> Character? {
    let range = NSRange(label.startIndex..., in: label)
    guard let match = keyHint.firstMatch(in: label, range: range),
          let hintRange = Range(match.range, in: label) else { return nil }
    let hint = label[hintRange].trimmingCharacters(in: CharacterSet(charactersIn: " ()")).lowercased()
    if hint == "esc" { return "." }
    guard hint.count == 1, let key = hint.first, !editingKeys.contains(key) else { return nil }
    return key
}

/// The shortcut for each option, so the card answers to the key the terminal answers to. An
/// option takes the key the agent prints, else its menu number, else the key for its kind:
/// ⌘Y for a one-time yes, ⌘⇧Y for a grant, ⌘N for a no. A rebound key in the agent shows in its
/// hint, so the card follows it. A key goes to the first option that asks for it.
///
/// The screen picks the key, so the key alone cannot say what an option grants. Shift does: a
/// grant always takes Shift, and only a grant does. A grant with only a number takes ⌘⇧Y. In the
/// same way, ⌘Y is always a yes and ⌘N and ⌘. are always a no, whatever the screen prints. A hint
/// for one of those keys on any other option is dropped.
func optionShortcuts(_ options: [(label: String, number: Int?)]) -> [OptionShortcut?] {
    var taken = Set<String>()
    return options.map { option in
        guard let shortcut = preferredShortcut(option.label, number: option.number) else { return nil }
        return taken.insert("\(shortcut.shift)\(shortcut.key)").inserted ? shortcut : nil
    }
}

private func preferredShortcut(_ label: String, number: Int?) -> OptionShortcut? {
    let kind = optionKind(label)
    let hint = hintKey(label).flatMap { keyFits($0, kind) ? $0 : nil }
    guard let key = hint ?? numberKey(number) ?? kindKey(kind) else { return nil }
    guard kind == .grant else { return OptionShortcut(key: key) }
    return OptionShortcut(key: key.isNumber ? "y" : key, shift: true)
}

/// Keys that say what an option does, and the kinds of option that may take them.
private let reservedKeys: [Character: Set<OptionKind>] = [
    "y": [.once, .grant], "n": [.refuse], ".": [.refuse],
]

private func keyFits(_ key: Character, _ kind: OptionKind) -> Bool {
    reservedKeys[key]?.contains(kind) ?? true
}

private func numberKey(_ number: Int?) -> Character? {
    guard let number, (1...9).contains(number) else { return nil }
    return Character(String(number))
}

private func kindKey(_ kind: OptionKind) -> Character? {
    switch kind {
    case .once, .grant: "y"
    case .refuse: "n"
    case .other: nil
    }
}

/// The text the approval card shows: the question with its options.
///
/// It starts up to six lines above the first option, but never above a divider. Claude draws a
/// divider over each question and the lines above it are earlier output.
func promptExcerpt(_ lines: [String], _ menu: NumberedMenu) -> String {
    guard let start = menu.firstLine, let end = menu.lastLine else {
        return String(lines.suffix(14).joined(separator: "\n").prefix(1500))
    }
    let floor = max(0, start - 6)
    let divider = lines[floor..<start].lastIndex { line in
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        return !trimmed.isEmpty && trimmed.allSatisfy { "─━═-".contains($0) }
    }
    let from = divider.map { $0 + 1 } ?? floor
    return String(lines[from..<min(lines.count, end + 2)].joined(separator: "\n").prefix(1500))
}

/// Whether the prompt a card shows is still the one on screen. A menu number selects whatever
/// menu is up when it lands, so a stale card would answer a prompt you never saw.
func promptStillShown(_ shown: String?, lines: [String]) -> Bool {
    guard let shown else { return false }
    return promptExcerpt(lines, menuOptions(lines)) == shown
}
