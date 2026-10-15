import AppKit
import Foundation

/// Whether the panel is allowed to put itself in front of you unasked.
///
/// Two independent switches. `soundEnabled` covers the alert on a blocked agent.
/// `presentationMode` additionally stops the panel expanding on its own, so a demo or a call does
/// not get a running commentary on your agents.
///
/// Presentation mode is manual on purpose. macOS has no public API that reports whether the
/// screen is being shared: NSScreen has no such property, and CGDisplayIsCaptured only covers
/// exclusive display capture, which is not what Zoom, Teams or Meet do. Anything automatic here
/// would be guesswork about which apps are running, and a mode you cannot trust is worse than a
/// switch you flip yourself.
///
/// Only unprompted appearances are suppressed. Hovering the notch still opens the panel, because
/// that is you asking for it.
enum Quiet {
    private static let soundKey = "herdi_sound_enabled"
    private static let presentationKey = "herdi_presentation_mode"

    static var soundEnabled: Bool {
        get { UserDefaults.standard.object(forKey: soundKey) as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: soundKey) }
    }

    /// Not persisted: a session you forget to leave is the failure mode worth avoiding, so this
    /// resets to off every launch.
    static var presentationMode = false

    static var shouldPlaySound: Bool { soundEnabled && !presentationMode }
    static var shouldAutoExpand: Bool { !presentationMode }

    /// The sound Herdi plays itself when macOS will not play one.
    static let ownSoundName = "Glass"

    /// Whether Herdi plays the alert sound itself.
    ///
    /// macOS plays a notification's sound only while Herdi's notifications are allowed and their
    /// sound is on in System Settings. Someone who turns the banner off, because the panel already
    /// opens for a blocked agent, still wants to hear it. So Herdi plays the sound whenever macOS
    /// will not, and the Sound toggle stays the one switch that silences it.
    static func playsOwnSound(macOSPlaysSound: Bool, soundWanted: Bool) -> Bool {
        soundWanted && !macOSPlaysSound
    }
}
