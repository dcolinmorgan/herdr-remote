import Foundation

/// How much larger than its drawn size the expanded panel is shown.
///
/// The notch bar is left alone: its height and width come from the screen, and a bar that no
/// longer lines up with the physical notch looks broken. Only the content below the divider is
/// scaled, together with the window that holds it, so nothing is clipped at the larger steps.
///
/// The scale multiplies the laid-out content rather than each font size. The panel's type sizes
/// are literals in dozens of places, and `Dynamic Type` does not reach `Font.system(size:)`, so
/// magnifying the finished layout is the one way to resize all of it consistently.
enum TextSize {
    private static let key = "herdi_ui_scale"

    static let `default`: Double = 1.2
    static let steps: [Double] = stride(from: 1.0, through: 2.0, by: 0.1).map { onStep($0) }

    /// Stored scale, rounded to a step and clamped to the range the menu offers. A value written
    /// by hand with `defaults write` cannot push the panel off the screen.
    static var current: Double {
        get {
            let stored = UserDefaults.standard.double(forKey: key)
            guard stored > 0 else { return `default` }
            return min(max(onStep(stored), steps.first ?? 1.0), steps.last ?? 2.0)
        }
        set { UserDefaults.standard.set(onStep(newValue), forKey: key) }
    }

    /// The next step up, or nil at the largest.
    static var larger: Double? { steps.first { $0 > current } }

    /// The next step down, or nil at the smallest.
    static var smaller: Double? { steps.last { $0 < current } }

    static func percent(_ scale: Double) -> String { "\(Int((scale * 100).rounded()))%" }

    /// One decimal, so a stored 1.2000000000000002 still matches the 1.2 step.
    private static func onStep(_ scale: Double) -> Double { (scale * 10).rounded() / 10 }
}
