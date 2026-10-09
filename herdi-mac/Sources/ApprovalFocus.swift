import Foundation

/// The menu setting "Give Approvals Keyboard Focus". Off by default: a card that takes the
/// keyboard can catch keys you meant for another app, so you turn it on yourself.
let approvalFocusKey = "approvalKeyboardFocus"

/// Seconds with no key press, in any app, before a card that opens by itself takes the keyboard.
let typingPause: TimeInterval = 2

/// Whether the card takes the keyboard. The shortcut always gives it the keyboard, because you
/// asked for it. A card that opens by itself takes it only when the setting is on, and not while
/// you type in another app: ⌘1 to ⌘9 and ⌘N there would answer the card instead.
func mayTakeKeyboard(enabled: Bool, secondsSinceKeyDown: TimeInterval, askedByShortcut: Bool = false) -> Bool {
    askedByShortcut || (enabled && secondsSinceKeyDown >= typingPause)
}
