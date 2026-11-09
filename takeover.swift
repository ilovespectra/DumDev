import AppKit
import CoreGraphics
import Darwin

if CommandLine.arguments.contains("--request-input-permission") {
    _ = CGRequestListenEventAccess()
    exit(0)
}
if CommandLine.arguments.contains("--request-screen-permission") {
    _ = CGRequestScreenCaptureAccess()
    exit(0)
}
if CommandLine.arguments.contains("--check-permissions") {
    let result: [String: Bool] = [
        "input": CGPreflightListenEventAccess(),
        "screen": CGPreflightScreenCaptureAccess()
    ]
    let data = try! JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
    exit(0)
}

struct MouseMotion {
    var x = 0.0
    var y = 0.0

    mutating func interrupts(dx: Double, dy: Double) -> Bool {
        x += dx
        y += dy
        return x * x + y * y >= 36
    }
}

if CommandLine.arguments.contains("--test-motion") {
    var motion = MouseMotion()
    for _ in 0..<100 {
        precondition(!motion.interrupts(dx: 0, dy: 0))
        precondition(!motion.interrupts(dx: 1, dy: -1))
        precondition(!motion.interrupts(dx: -1, dy: 1))
    }
    precondition(!motion.interrupts(dx: 3, dy: 0))
    precondition(motion.interrupts(dx: 3, dy: 0))
    motion = MouseMotion()
    precondition(motion.interrupts(dx: 0, dy: -6))
    print("Mouse motion checks passed")
    exit(0)
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

final class EdgeGlowView: NSView {
    override func draw(_ dirtyRect: NSRect) {
        let depth: CGFloat = 48
        let colors = [
            NSColor(calibratedRed: 0.48, green: 0.63, blue: 1, alpha: 0.16),
            NSColor(calibratedRed: 0.40, green: 0.54, blue: 1, alpha: 0.27),
            NSColor(calibratedRed: 0.56, green: 0.43, blue: 1, alpha: 0.08),
            NSColor.clear
        ]
        guard let gradient = NSGradient(colors: colors, atLocations: [0, 0.12, 0.45, 1],
                                        colorSpace: .deviceRGB) else { return }
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath(roundedRect: bounds, xRadius: 18, yRadius: 18).addClip()
        gradient.draw(in: NSRect(x: 0, y: 0, width: bounds.width, height: depth), angle: 90)
        gradient.draw(in: NSRect(x: 0, y: bounds.height - depth, width: bounds.width, height: depth), angle: 270)
        gradient.draw(in: NSRect(x: 0, y: 0, width: depth, height: bounds.height), angle: 0)
        gradient.draw(in: NSRect(x: bounds.width - depth, y: 0, width: depth, height: bounds.height), angle: 180)
        NSGraphicsContext.restoreGraphicsState()
    }
}

if CommandLine.arguments.contains("--test-glow") {
    let view = EdgeGlowView(frame: NSRect(x: 0, y: 0, width: 960, height: 600))
    guard let bitmap = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: 960, pixelsHigh: 600,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
        let context = NSGraphicsContext(bitmapImageRep: bitmap) else {
        fatalError("Cannot create glow render target")
    }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    NSColor.clear.setFill()
    view.bounds.fill(using: .copy)
    view.draw(view.bounds)
    NSGraphicsContext.restoreGraphicsState()
    let alpha = (0..<70).map { bitmap.colorAt(x: $0, y: 300)!.alphaComponent }
    precondition(alpha[0] > 0.05, "Glow must meet the screen edge without a gap")
    precondition(alpha[35] > 0, "Glow must have a soft, wide falloff")
    precondition(alpha[55] == 0, "Glow must leave the working area clear")
    precondition(zip(alpha, alpha.dropFirst()).allSatisfy { abs($0 - $1) < 0.04 },
                 "Glow must not have hard alpha transitions")
    precondition(bitmap.colorAt(x: 480, y: 300)!.alphaComponent == 0,
                 "Screen center must remain transparent")
    if let index = CommandLine.arguments.firstIndex(of: "--test-glow"),
       CommandLine.arguments.count > index + 1 {
        let path = CommandLine.arguments[index + 1]
        try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: path))
    }
    print("Glow checks passed: edge contact, smooth falloff, transparent center")
    exit(0)
}

var panels: [NSPanel] = []
var labels: [NSTextField] = []
var ending = false
var monitoring = false
var mouseMotion = MouseMotion()
let parent = pid_t(CommandLine.arguments[1])!
var armedAt = Date()

func showPanels(_ visible: Bool) {
    for panel in panels {
        if visible {
            panel.orderFrontRegardless()
        }
        NSAnimationContext.runAnimationGroup({ context in
            context.duration = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion ? 0 : 0.45
            panel.animator().alphaValue = visible ? 1 : 0
        }, completionHandler: {
            if !visible && !monitoring { panel.orderOut(nil) }
        })
    }
}

func emit(_ message: String) {
    print(message)
    fflush(stdout)
}

func finish(_ message: String, abort: Bool, reason: String = "stopped") {
    guard !ending else { return }
    ending = true
    monitoring = false
    for label in labels { label.stringValue = message }
    if abort { emit("ABORT: " + reason) }
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
        showPanels(false)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { app.terminate(nil) }
    }
}

for screen in NSScreen.screens {
    let panel = NSPanel(contentRect: screen.frame, styleMask: [.borderless, .nonactivatingPanel],
                        backing: .buffered, defer: false)
    panel.level = .screenSaver
    panel.isOpaque = false
    panel.backgroundColor = .clear
    panel.ignoresMouseEvents = true
    panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
    panel.hasShadow = false
    panel.alphaValue = 0
    let view = NSView(frame: NSRect(origin: .zero, size: screen.frame.size))
    view.wantsLayer = true
    let glow = EdgeGlowView(frame: view.bounds)
    glow.wantsLayer = true
    view.addSubview(glow)
    if !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
        let breath = CABasicAnimation(keyPath: "opacity")
        breath.fromValue = 0.65
        breath.toValue = 1
        breath.duration = 2.8
        breath.autoreverses = true
        breath.repeatCount = .infinity
        breath.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
        glow.layer?.add(breath, forKey: "breathing")
    }
    let width = min(CGFloat(560), screen.frame.width - 48)
    let capsule = NSVisualEffectView(frame: NSRect(
        x: (screen.frame.width - width) / 2, y: screen.frame.height - 70,
        width: width, height: 38))
    capsule.material = .hudWindow
    capsule.blendingMode = .behindWindow
    capsule.state = .active
    capsule.wantsLayer = true
    capsule.layer?.cornerRadius = 19
    capsule.layer?.masksToBounds = true
    let label = NSTextField(labelWithString: "Preparing Arc control · Escape to cancel")
    label.font = .systemFont(ofSize: 13, weight: .medium)
    label.textColor = NSColor.white.withAlphaComponent(0.92)
    label.alignment = .center
    label.frame = NSRect(x: 14, y: 10, width: width - 28, height: 18)
    capsule.addSubview(label)
    view.addSubview(capsule)
    panel.contentView = view
    panel.orderFrontRegardless()
    panels.append(panel)
    labels.append(label)
}
showPanels(true)

let mask: CGEventMask = [CGEventType.keyDown, .leftMouseDown, .rightMouseDown,
                         .otherMouseDown, .mouseMoved, .scrollWheel].reduce(0) { $0 | (1 << $1.rawValue) }
let callback: CGEventTapCallBack = { _, type, event, _ in
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        finish("TAKEOVER STOPPED — input monitor lost. You may resume.", abort: true, reason: "input monitor disabled")
    } else if event.getIntegerValueField(.eventSourceUserData) == Int64(parent)
              && event.getIntegerValueField(.eventSourceUnixProcessID) == Int64(parent) {
        // Only the controller's process-addressed text events are expected.
    } else if type == .keyDown && event.getIntegerValueField(.keyboardEventKeycode) == 53 {
        finish("TAKEOVER STOPPED — Escape pressed. You may resume.", abort: true, reason: "Escape pressed")
    } else if monitoring {
        let dx = event.getDoubleValueField(.mouseEventDeltaX)
        let dy = event.getDoubleValueField(.mouseEventDeltaY)
        if type != .mouseMoved || mouseMotion.interrupts(dx: dx, dy: dy) {
            let sourcePID = event.getIntegerValueField(.eventSourceUnixProcessID)
            finish("TAKEOVER PAUSED — input detected. Waiting for safe recovery.", abort: true,
                   reason: "input event type \(type.rawValue), source \(sourcePID), delta \(dx),\(dy)")
        }
    }
    return Unmanaged.passUnretained(event)
}
guard let tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap,
                                  options: .listenOnly, eventsOfInterest: mask,
                                  callback: callback, userInfo: nil) else {
    emit("ERROR: Input Monitoring permission is required for the takeover helper.")
    exit(1)
}
let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
CGEvent.tapEnable(tap: tap, enable: true)
emit("READY")

DispatchQueue.global().async {
    while let line = readLine() {
        DispatchQueue.main.async {
            if line == "ARM" && !ending {
                mouseMotion = MouseMotion()
                monitoring = true
                armedAt = Date()
                showPanels(true)
                for label in labels { label.stringValue = "Working in Arc · Escape or input pauses control" }
                emit("ARMED")
            } else if line == "IDLE" && !ending {
                monitoring = false
                mouseMotion = MouseMotion()
                showPanels(false)
            } else if line == "DONE" {
                finish("POST VERIFIED — takeover complete. You may resume.", abort: false)
            } else if line == "READ_DONE" {
                finish("READ COMPLETE — no post sent. You may resume.", abort: false)
            } else if line == "STOP" {
                finish("TAKEOVER STOPPED — outcome not verified. You may resume.", abort: true)
            } else if !ending {
                for label in labels { label.stringValue = line }
            }
        }
    }
    DispatchQueue.main.async { finish("TAKEOVER STOPPED — controller closed. You may resume.", abort: true) }
}
Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { _ in
    if kill(parent, 0) != 0 || (monitoring && Date().timeIntervalSince(armedAt) > 90) {
        finish("TAKEOVER STOPPED — timeout. You may resume.", abort: true)
    }
}
app.run()
