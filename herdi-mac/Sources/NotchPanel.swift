import AppKit
import SwiftUI

/// A non-activating floating panel that sits in the Mac's notch area.
/// On displays without a notch, it appears as a compact bar at top-center.
final class NotchPanel: NSPanel {
    private let hostingView: NSHostingView<AnyView>
    private var trackingArea: NSTrackingArea?
    private var expandedHeight: CGFloat = 420
    private var collapsedHeight: CGFloat = 32
    private var panelWidth: CGFloat = 280
    private var expandedWidth: CGFloat = 360

    /// Whether the panel is currently in expanded state
    var isExpanded = false {
        didSet { animateState() }
    }

    init<Content: View>(content: Content) {
        hostingView = NSHostingView(rootView: AnyView(content))

        let rect = NSRect(x: 0, y: 0, width: 280, height: 32)
        super.init(
            contentRect: rect,
            styleMask: [.nonactivatingPanel, .fullSizeContentView, .borderless],
            backing: .buffered,
            defer: false
        )

        // Panel behavior
        level = .statusBar + 1
        isFloatingPanel = true
        hidesOnDeactivate = false
        animationBehavior = .utilityWindow
        isMovable = false
        isMovableByWindowBackground = false
        backgroundColor = .clear
        isOpaque = false
        hasShadow = true
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        titleVisibility = .hidden
        titlebarAppearsTransparent = true

        // Content
        contentView = hostingView
        hostingView.frame = rect

        positionAtNotch()
    }

    override var canBecomeKey: Bool { isExpanded }
    override var canBecomeMain: Bool { false }

    // MARK: - Positioning

    func positionAtNotch() {
        guard let screen = NSScreen.main else { return }
        let frame = screen.frame
        let visibleFrame = screen.visibleFrame

        // Detect notch: on notch displays, the safe area inset at top is larger
        let hasNotch = (frame.height - visibleFrame.height - (visibleFrame.origin.y - frame.origin.y)) > 24

        let width = isExpanded ? expandedWidth : panelWidth
        let height = isExpanded ? expandedHeight : collapsedHeight

        let x = frame.midX - width / 2
        let y: CGFloat

        if hasNotch {
            // Sit right below the notch (menu bar area)
            y = frame.maxY - height - 8
        } else {
            // Top-center, just below menu bar
            y = visibleFrame.maxY - height - 4
        }

        setFrame(NSRect(x: x, y: y, width: width, height: height), display: true)
    }

    // MARK: - Expand / Collapse Animation

    private func animateState() {
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.35
            ctx.timingFunction = CAMediaTimingFunction(controlPoints: 0.2, 0.9, 0.3, 1.0) // spring-like
            ctx.allowsImplicitAnimation = true
            animator().setFrame(targetFrame(), display: true, animate: true)
        } completionHandler: { [weak self] in
            guard let self else { return }
            self.hostingView.frame = self.contentView?.bounds ?? .zero
        }
        // Also update shadow for depth effect
        self.animator().hasShadow = isExpanded
    }

    private func targetFrame() -> NSRect {
        guard let screen = NSScreen.main else { return frame }
        let screenFrame = screen.frame
        let visibleFrame = screen.visibleFrame
        let hasNotch = (screenFrame.height - visibleFrame.height - (visibleFrame.origin.y - screenFrame.origin.y)) > 24

        let width = isExpanded ? expandedWidth : panelWidth
        let height = isExpanded ? expandedHeight : collapsedHeight
        let x = screenFrame.midX - width / 2
        let y: CGFloat

        if hasNotch {
            y = screenFrame.maxY - height - 8
        } else {
            y = visibleFrame.maxY - height - 4
        }

        return NSRect(x: x, y: y, width: width, height: height)
    }

    func toggle() {
        isExpanded.toggle()
    }

    func expand() {
        guard !isExpanded else { return }
        isExpanded = true
    }

    func collapse() {
        guard isExpanded else { return }
        isExpanded = false
    }

    // MARK: - Mouse Tracking

    override func mouseEntered(with event: NSEvent) {
        super.mouseEntered(with: event)
        // Optional: expand on hover after a delay
    }

    override func mouseExited(with event: NSEvent) {
        super.mouseExited(with: event)
        // Collapse when mouse leaves (if not interacting)
        if isExpanded && !NSPointInRect(NSEvent.mouseLocation, frame) {
            // Small delay before collapse to prevent flickering
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
                guard let self else { return }
                if !NSPointInRect(NSEvent.mouseLocation, self.frame) {
                    self.collapse()
                }
            }
        }
    }

    override func resignKey() {
        super.resignKey()
        // Collapse when panel loses focus
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            guard let self else { return }
            if !self.isKeyWindow {
                self.collapse()
            }
        }
    }
}

// MARK: - Panel Controller

@Observable
final class NotchPanelController {
    var isExpanded = false
    private var panel: NotchPanel?

    func setup(with relay: RelayConnection) {
        let content = NotchContentView(relay: relay, controller: self)
        panel = NotchPanel(content: content)
        panel?.orderFrontRegardless()
        panel?.positionAtNotch()

        // Watch for screen changes (display connect/disconnect, resolution change)
        NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil, queue: .main
        ) { [weak self] _ in
            self?.panel?.positionAtNotch()
        }
    }

    func toggle() {
        isExpanded.toggle()
        panel?.isExpanded = isExpanded
    }

    func expand() {
        isExpanded = true
        panel?.expand()
    }

    func collapse() {
        isExpanded = false
        panel?.collapse()
    }

    func reposition() {
        panel?.positionAtNotch()
    }
}
