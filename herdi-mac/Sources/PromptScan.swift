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

/// Choices with no numbers: a list with a cursor, like Claude's folder-trust dialog, or a row of
/// choices side by side, like opencode's "Allow once  Allow always  Reject". The card turns these
/// into buttons and presses arrow keys and Enter to pick one.
struct ChoiceMenu: Equatable {
    let choices: [String]
    let selected: Int
    let horizontal: Bool
}

private let cursorLine = try! NSRegularExpression(pattern: #"^(\s*[❯>›»▶]\s+)(\S.*?)\s*$"#)

/// `plain` is the pane as text. `ansi` is the same pane with its colour codes, which is the only
/// place a row of choices marks the selected one. A numbered menu always wins over this.
func detectChoiceMenu(plain: [String], ansi: [String]) -> ChoiceMenu? {
    guard detectNumberedOptions(plain).labels.isEmpty else { return nil }
    return cursorList(plain) ?? highlightedRow(ansi)
}

/// The arrow keys, then Enter, that move from the selected choice to `index` and pick it.
func keys(toChoose index: Int, in menu: ChoiceMenu) -> [String] {
    let step = index - menu.selected
    let key = menu.horizontal ? (step > 0 ? "Right" : "Left") : (step > 0 ? "Down" : "Up")
    return Array(repeating: key, count: abs(step)) + ["Enter"]
}

/// A cursor line and the lines next to it at the same text column. Known gap: in a pane too
/// narrow for a choice, its wrapped second line reads as one more choice.
private func cursorList(_ lines: [String]) -> ChoiceMenu? {
    for (index, line) in lines.enumerated().reversed() {
        guard let match = matches(cursorLine, line) else { continue }
        let text = group(match, 2, in: line)
        guard text.range(of: #"^\d{1,2}[.)]\s"#, options: .regularExpression) == nil else { continue }
        let column = (group(match, 1, in: line) as NSString).length

        func choice(_ other: String) -> String? {
            let trimmed = other.trimmingCharacters(in: .whitespaces)
            let indent = (other as NSString).length - (String(other.drop(while: { $0.isWhitespace })) as NSString).length
            guard indent == column, !trimmed.isEmpty, matches(cursorLine, other) == nil else { return nil }
            return trimmed
        }

        var above: [String] = []
        var cursor = index - 1
        while cursor >= 0, let text = choice(lines[cursor]) { above.insert(text, at: 0); cursor -= 1 }
        var below: [String] = []
        cursor = index + 1
        while cursor < lines.count, let text = choice(lines[cursor]) { below.append(text); cursor += 1 }

        let choices = above + [text] + below
        return choices.count >= 2 ? ChoiceMenu(choices: choices, selected: above.count, horizontal: false) : nil
    }
    return nil
}

/// A row of two to six short choices, split by two or more spaces, where exactly one has a
/// background unlike the rest of the row. A footer such as "ctrl+f fullscreen  enter confirm" has
/// one background throughout, so it never reads as choices. A wide pane can draw that footer on
/// the same row as the choices, far to the right, so a gap of `groupGap` spaces ends a group.
private func highlightedRow(_ lines: [String]) -> ChoiceMenu? {
    for line in lines.reversed() {
        let (segments, base) = choiceSegments(line)
        for group in segmentGroups(segments) {
            guard (2...6).contains(group.count), group.allSatisfy({ $0.text.count <= 40 }) else { continue }
            let marked = group.indices.filter { group[$0].background != base }
            guard marked.count == 1, let selected = marked.first else { continue }
            return ChoiceMenu(choices: group.map(\.text), selected: selected, horizontal: true)
        }
    }
    return nil
}

/// Spaces between two choices are two or three. A wider gap starts a new group.
private let groupGap = 8

private func segmentGroups(_ segments: [ChoiceSegment]) -> [[ChoiceSegment]] {
    var groups: [[ChoiceSegment]] = []
    for segment in segments {
        if segment.gap < groupGap, !groups.isEmpty {
            groups[groups.count - 1].append(segment)
        } else {
            groups.append([segment])
        }
    }
    return groups
}

private typealias ChoiceSegment = (text: String, background: String?, gap: Int)

/// Split one line of colour-coded text into runs of words, each with the background its first
/// letter has, and give the row's most common background. Runs made only of box-drawing borders
/// are dropped.
private func choiceSegments(_ line: String) -> (segments: [ChoiceSegment], base: String?) {
    var cells: [(Character, String?)] = []
    var background: String?
    var reverse = false
    var rest = Substring(line)
    while let first = rest.first {
        if first == "\u{1B}", rest.dropFirst().first == "[",
           let end = rest.firstIndex(where: { $0.isLetter }) {
            if rest[end] == "m" {
                let params = rest[rest.index(rest.startIndex, offsetBy: 2)..<end].split(separator: ";", omittingEmptySubsequences: false).map(String.init)
                var at = 0
                while at < params.count {
                    let param = params[at]
                    switch param {
                    case "", "0": background = nil; reverse = false
                    case "7": reverse = true
                    case "27": reverse = false
                    case "49": background = nil
                    case "48":
                        let width = params.count > at + 1 && params[at + 1] == "5" ? 2 : 4
                        background = params[(at + 1)..<min(params.count, at + 1 + width)].joined(separator: ";")
                        at += width
                    default:
                        if let code = Int(param), (40...47).contains(code) || (100...107).contains(code) {
                            background = param
                        }
                    }
                    at += 1
                }
            }
            rest = rest[rest.index(after: end)...]
            continue
        }
        cells.append((first, reverse ? "reverse" : background))
        rest = rest.dropFirst()
    }

    var segments: [ChoiceSegment] = []
    var text = ""
    var segmentBackground: String?
    var segmentGap = 0
    var spaces = 0
    func close() {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        let border = trimmed.unicodeScalars.allSatisfy { (0x2500...0x259F).contains($0.value) }
        if !trimmed.isEmpty, !border { segments.append((trimmed, segmentBackground, segmentGap)) }
        text = ""
        segmentBackground = nil
    }
    for (character, cellBackground) in cells {
        if character == " " {
            spaces += 1
            if spaces == 2 { close() }
            if !text.isEmpty { text.append(character) }
            continue
        }
        if text.isEmpty {
            segmentBackground = cellBackground
            segmentGap = spaces
        }
        spaces = 0
        text.append(character)
    }
    close()
    let tally = Dictionary(cells.map { ($0.1 ?? "", 1) }, uniquingKeysWith: +)
    let base = tally.max { $0.value < $1.value }.map { $0.key.isEmpty ? nil : $0.key } ?? nil
    return (segments, base)
}
