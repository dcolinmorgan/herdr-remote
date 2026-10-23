// Layout tests for the Connect a Phone window. Run with herdi-mac/test.sh.
import AppKit
import SwiftUI

var failures = 0

func check(_ name: String, _ ok: Bool, _ detail: @autoclosure () -> String = "") {
    if ok {
        print("  pass: \(name)")
    } else {
        failures += 1
        print("  FAIL: \(name) \(detail())")
    }
}

let rowWidth: CGFloat = 340

@MainActor func render(_ view: some View) -> NSBitmapImageRep {
    let host = NSHostingView(rootView: view.frame(width: rowWidth))
    host.frame = NSRect(origin: .zero, size: host.fittingSize)
    host.layoutSubtreeIfNeeded()
    let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds)!
    host.cacheDisplay(in: host.bounds, to: rep)
    return rep
}

/// True when any pixel in the column at `x` has paint on it.
func columnHasPaint(_ rep: NSBitmapImageRep, x: Int) -> Bool {
    (0..<rep.pixelsHigh).contains { y in (rep.colorAt(x: x, y: y)?.alphaComponent ?? 0) > 0.1 }
}

@main
struct ConnectPhoneLayoutTests {
    static func main() {
        MainActor.assumeIsolated {
            _ = NSApplication.shared
            let focused = render(DisclosureRowLabel(title: "Change Setup", isExpanded: false, focused: true))
            let plain = render(DisclosureRowLabel(title: "Change Setup", isExpanded: false, focused: false))
            let scale = CGFloat(focused.pixelsWide) / rowWidth
            let height = CGFloat(focused.pixelsHigh) / scale

            check("makes the Change Setup row at least 28 points tall", height >= DisclosureRowLabel.minHeight,
                  "got \(height)")
            check("makes the Change Setup row as wide as the window", focused.pixelsWide == Int(rowWidth * scale))
            let rightEdge = focused.pixelsWide - Int(2 * scale)
            check("draws the focus ring out to the right edge of the row", columnHasPaint(focused, x: rightEdge))
            check("draws no ring on the row without focus", !columnHasPaint(plain, x: rightEdge))
        }

        check("gives a temp tunnel only the step that opens the link",
              phoneSteps(temporary: true) == ["Open the link in Chrome (Android) or Safari (iPhone)."])
        check("gives your own domain all 3 phone steps", phoneSteps(temporary: false) == [
            "Open the link in Chrome (Android) or Safari (iPhone).",
            "If Cloudflare asks, enter your email and the PIN it sends you.",
            "Add the page to the home screen, and open Herdi from that icon.",
        ])
        check("shows a single step with no number", stepNumber(0, of: 1) == nil)
        check("numbers each step of a longer list", (0..<3).map { stepNumber($0, of: 3) } == ["1.", "2.", "3."])
        check("tells the free setup to answer N to the Cloudflare login",
              freeSetupSteps[2].text == "Answer Y to the token question and N to the Cloudflare login. At the tunnel question, choose 2 (temp).")
        check("tells your own domain to answer Y to the Cloudflare login",
              ownDomainSetupSteps[2].text.contains("Y to the token question and to the Cloudflare login"))
        print(failures == 0 ? "ConnectPhoneLayout: all passed" : "ConnectPhoneLayout: \(failures) failed")
        exit(failures == 0 ? 0 : 1)
    }
}
