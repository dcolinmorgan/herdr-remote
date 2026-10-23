import AppKit
import CoreImage.CIFilterBuiltins
import SwiftUI

/// The guide the window points to when there is no fixed address to give the phone yet.
private let remoteAccessGuide = URL(string: "https://github.com/dcolinmorgan/herdr-remote/blob/main/REMOTE_ACCESS.md")!

@MainActor
final class ConnectPhoneModel: ObservableObject {
    @Published var link: PhoneLink = .noTunnel
    /// The code is the same as a password, so it stays hidden until you ask for it. That keeps it
    /// off a shared screen you forgot about.
    @Published var showCode = false
    @Published var presenting = false
    /// The setup the window explains on a Mac with no tunnel yet.
    @Published var setupChoice: SetupChoice = .free
    @Published var changeSetupOpen = false
}

enum SetupChoice: String, CaseIterable, Identifiable {
    case free = "Free"
    case ownDomain = "Your Own Domain"
    var id: Self { self }
}

/// The window behind "Connect a Phone…" in the status menu.
@MainActor
final class ConnectPhoneWindowController: NSObject, NSWindowDelegate {
    private let model = ConnectPhoneModel()
    private var window: NSWindow?

    func show() {
        model.link = currentPhoneLink()
        model.showCode = false
        model.changeSetupOpen = false
        model.presenting = Quiet.presentationMode

        let window = self.window ?? makeWindow()
        self.window = window
        window.center()
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    /// Presentation Mode hides the code at once, even with the window open.
    func presentationModeChanged() {
        model.presenting = Quiet.presentationMode
        if model.presenting { model.showCode = false }
    }

    private func makeWindow() -> NSWindow {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 380, height: 520),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = "Connect a Phone"
        window.isReleasedWhenClosed = false
        window.delegate = self
        // The controller resizes the window to the view, so it grows when Show Code or Change
        // Setup adds content, and shrinks back when that content goes.
        let controller = NSHostingController(rootView: ConnectPhoneView(model: model))
        controller.sizingOptions = .preferredContentSize
        window.contentViewController = controller
        return window
    }

    func windowWillClose(_ notification: Notification) {
        model.showCode = false
    }
}

struct ConnectPhoneView: View {
    @ObservedObject var model: ConnectPhoneModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            switch model.link {
            case .ready(let link, let address):
                ready(link: link, address: address, temporary: false)
            case .temporary(let link, let address):
                ready(link: link, address: address, temporary: true)
            case .noToken(let address):
                noToken(address: address)
            case .tempTunnel:
                tempTunnel
            case .noTunnel:
                noTunnel
            }
        }
        .padding(20)
        .frame(width: 380, alignment: .topLeading)
        .fixedSize(horizontal: false, vertical: true)
    }

    private func ready(link: String, address: String, temporary: Bool) -> some View {
        Group {
            Text("Scan this code with your phone to open Herdi there.")
            // Not selectable: the plain address fails with "Invalid token" in a browser that has no
            // cookie yet. Copy Link gives the link that works.
            Text(address).font(.system(.body, design: .monospaced))
            if temporary {
                Label("This address changes when the tunnel or the Mac restarts. Then open this window again and scan the new code.",
                      systemImage: "clock.arrow.circlepath")
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack {
                Spacer()
                codeArea(link: link)
                Spacer()
            }

            steps(phoneSteps(temporary: temporary))
            Text("Use a normal tab. A private tab forgets the login when you close it.")
                .font(.callout).foregroundStyle(.secondary)
            changeSetup(to: temporary ? .ownDomain : .free)
        }
    }

    /// A Mac that is already set up shows only the other choice, folded away under the code.
    private func changeSetup(to choice: SetupChoice) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            DisclosureRow(title: "Change Setup", isExpanded: $model.changeSetupOpen)
            if model.changeSetupOpen {
                VStack(alignment: .leading, spacing: 10) {
                    Text(choice == .free ? "Switch to the free setup" : "Switch to your own domain")
                        .font(.headline)
                    Text("The installer replaces your current tunnel.")
                        .font(.callout).foregroundStyle(.secondary)
                    setupSteps(choice)
                }
                .padding(.top, 8)
            }
        }
    }

    @ViewBuilder
    private func codeArea(link: String) -> some View {
        if model.presenting {
            VStack(spacing: 8) {
                placeholder("Presentation Mode is on. Turn it off to show the code.")
                CopyLinkButton(link: link).disabled(true)
            }
        } else if model.showCode, let image = qrImage(link) {
            VStack(spacing: 8) {
                Image(nsImage: image)
                    .interpolation(.none)
                    .resizable()
                    .frame(width: 220, height: 220)
                    .padding(12)
                    .background(Color.white)
                    .accessibilityLabel("QR code with the link for your phone")
                HStack {
                    Button("Hide Code") { model.showCode = false }
                    CopyLinkButton(link: link)
                }
            }
        } else {
            VStack(spacing: 8) {
                placeholder("The code works like a password. Show it only where no one else can see your screen.")
                HStack {
                    Button("Show Code") { model.showCode = true }
                        .keyboardShortcut(.defaultAction)
                    CopyLinkButton(link: link)
                }
            }
        }
    }

    private func placeholder(_ text: String) -> some View {
        Text(text)
            .font(.callout)
            .foregroundStyle(.secondary)
            .multilineTextAlignment(.center)
            .frame(width: 244, height: 120)
            .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.1)))
    }

    private func noToken(address: String) -> some View {
        Group {
            Label("The relay has no token", systemImage: "exclamationmark.triangle.fill")
                .font(.headline).foregroundStyle(.orange)
            Text("Anyone who finds \(address) can control your agents. Run the installer again, and answer y to the token question.")
            command("cd herdr-remote/relay && ./install-service.sh")
            guideButton
        }
    }

    private var tempTunnel: some View {
        Group {
            Text("Herdi found no address for your temp tunnel, so the tunnel is not running or has not connected yet. Wait a minute, then open this window again.")
                .fixedSize(horizontal: false, vertical: true)
            changeSetup(to: .ownDomain)
        }
    }

    private var noTunnel: some View {
        Group {
            Text("Your phone reaches this Mac through a Cloudflare tunnel. Pick one to set up:")
            Picker("Setup", selection: $model.setupChoice) {
                ForEach(SetupChoice.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            setupSteps(model.setupChoice)
        }
    }

    @ViewBuilder
    private func setupSteps(_ choice: SetupChoice) -> some View {
        switch choice {
        case .free:
            Text("No account and no domain. The address changes when the Mac restarts, and you scan a new code then.")
                .font(.callout).fixedSize(horizontal: false, vertical: true)
            steps(freeSetupSteps)
        case .ownDomain:
            Text("A fixed address with a login page in front of it. You need a domain on Cloudflare and a free Cloudflare account. It takes about 15 minutes.")
                .font(.callout).fixedSize(horizontal: false, vertical: true)
            steps(ownDomainSetupSteps)
            guideButton
        }
    }

    private func steps(_ lines: [String]) -> some View {
        steps(lines.map { ($0, nil) })
    }

    /// A step with a command shows it in its own box under the text, so it never wraps mid-word.
    private func steps(_ lines: [Step]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(lines.enumerated()), id: \.offset) { index, line in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    if let number = stepNumber(index, of: lines.count) {
                        Text(number).monospacedDigit()
                    }
                    VStack(alignment: .leading, spacing: 4) {
                        Text(line.text).fixedSize(horizontal: false, vertical: true)
                        if let text = line.command { command(text) }
                    }
                }
            }
        }
    }

    private func command(_ text: String) -> some View {
        CommandBox(text: text)
    }

    private var guideButton: some View {
        Button("Open the Setup Guide") { NSWorkspace.shared.open(remoteAccessGuide) }
    }
}

typealias Step = (text: String, command: String?)

/// What to do on the phone after the scan. A temp tunnel has no Cloudflare Access login, and its
/// home-screen icon breaks when the address changes, so only the first step applies to it.
func phoneSteps(temporary: Bool) -> [String] {
    let open = "Open the link in Chrome (Android) or Safari (iPhone)."
    if temporary { return [open] }
    return [
        open,
        "If Cloudflare asks, enter your email and the PIN it sends you.",
        "Add the page to the home screen, and open Herdi from that icon.",
    ]
}

/// The installer asks "Login to Cloudflare now?" before the tunnel question. A temp tunnel needs
/// no login, so the free setup answers N there.
let freeSetupSteps: [Step] = [
    ("Install cloudflared:", "brew install cloudflared"),
    ("In the herdr-remote folder, run the installer:", "cd relay && ./install-service.sh"),
    ("Answer Y to the token question and N to the Cloudflare login. At the tunnel question, choose 2 (temp).", nil),
    ("Open this window again.", nil),
]

let ownDomainSetupSteps: [Step] = [
    ("Install cloudflared:", "brew install cloudflared"),
    ("In the herdr-remote folder, run the installer:", "cd relay && ./install-service.sh"),
    ("Answer Y to the token question and to the Cloudflare login. At the tunnel question, choose 1 (named).", nil),
    ("Give your relay address, for example relay.example.com.", nil),
    ("Add the login page with Cloudflare Access. The guide shows how.", nil),
    ("Open this window again.", nil),
]

/// "1.", "2.", and so on. A single step is a plain sentence with no number.
func stepNumber(_ index: Int, of count: Int) -> String? {
    count > 1 ? "\(index + 1)." : nil
}

/// Copies text and shows a green check for a moment, for the command boxes and Copy Link.
@MainActor
final class CopyFeedback: ObservableObject {
    @Published private(set) var copied = false

    func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        copied = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in self?.copied = false }
    }
}

/// Copies the full link, token included. The label never shows the link, so the token stays
/// off the screen.
struct CopyLinkButton: View {
    let link: String
    @StateObject private var feedback = CopyFeedback()

    var body: some View {
        Button { feedback.copy(link) } label: {
            Label(feedback.copied ? "Copied" : "Copy Link", systemImage: feedback.copied ? "checkmark" : "doc.on.doc")
                .foregroundStyle(feedback.copied ? Color.green : Color.primary)
        }
        .help("Copy the link for your phone. It holds your token, so paste it only where you trust.")
    }
}

/// A disclosure header that a click anywhere on the row opens. `DisclosureGroup` on macOS takes
/// clicks and draws its focus ring only on the chevron and the start of the label.
struct DisclosureRow: View {
    let title: String
    @Binding var isExpanded: Bool
    @FocusState private var focused: Bool

    var body: some View {
        Button { isExpanded.toggle() } label: {
            DisclosureRowLabel(title: title, isExpanded: isExpanded, focused: focused)
        }
        .buttonStyle(.plain)
        .focusEffectDisabled()
        .focused($focused)
        .accessibilityLabel(title)
        .accessibilityValue(isExpanded ? "Expanded" : "Collapsed")
        .accessibilityAddTraits(.isHeader)
    }
}

/// The row's look, apart from its focus state, so a test can draw the focused row.
struct DisclosureRowLabel: View {
    static let minHeight: CGFloat = 28

    let title: String
    let isExpanded: Bool
    let focused: Bool

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "chevron.right")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.secondary)
                .rotationEffect(.degrees(isExpanded ? 90 : 0))
                .frame(width: 12)
            Text(title)
        }
        .padding(.horizontal, 4)
        .frame(maxWidth: .infinity, minHeight: Self.minHeight, alignment: .leading)
        .contentShape(Rectangle())
        .overlay {
            if focused {
                RoundedRectangle(cornerRadius: 5).stroke(Color.accentColor.opacity(0.6), lineWidth: 3)
            }
        }
    }
}

/// A command to paste into Terminal. A click anywhere on the box copies it.
private struct CommandBox: View {
    let text: String
    @StateObject private var feedback = CopyFeedback()
    private var copied: Bool { feedback.copied }

    var body: some View {
        Button { feedback.copy(text) } label: {
            HStack(alignment: .firstTextBaseline) {
                Text(text)
                    .font(.system(.callout, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                Label(copied ? "Copied" : "Copy", systemImage: copied ? "checkmark" : "doc.on.doc")
                    .labelStyle(.iconOnly)
                    .foregroundStyle(copied ? Color.green : Color.secondary)
            }
            .padding(8)
            .contentShape(Rectangle())
            .background(RoundedRectangle(cornerRadius: 6).fill(Color.secondary.opacity(0.1)))
        }
        .buttonStyle(.plain)
        .help("Copy the command")
        .accessibilityLabel(copied ? "Copied: \(text)" : "Copy the command \(text)")
    }
}

/// Core Image draws one pixel per module, so the image is scaled up here and drawn with
/// `.interpolation(.none)` to keep the edges sharp enough for a phone camera.
private func qrImage(_ text: String) -> NSImage? {
    let filter = CIFilter.qrCodeGenerator()
    filter.message = Data(text.utf8)
    filter.correctionLevel = "M"
    guard let output = filter.outputImage?.transformed(by: CGAffineTransform(scaleX: 8, y: 8)),
          let cgImage = CIContext().createCGImage(output, from: output.extent) else { return nil }
    return NSImage(cgImage: cgImage, size: NSSize(width: output.extent.width, height: output.extent.height))
}
