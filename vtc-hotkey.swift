// Video to Claude agent v3: menu-bar status + countdown + hotkey + Esc cancel + mouse log. One binary, one set of grants.
import Cocoa
import AVFoundation
import Carbon.HIToolbox

let vtc = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "vtc.py"
let python = "/opt/homebrew/bin/python3"
let home = FileManager.default.homeDirectoryForCurrentUser
let stateDir = home.appendingPathComponent("Library/Application Support/VideoToClaude")
let stateFile = stateDir.appendingPathComponent("current.json")
let eventsFile = stateDir.appendingPathComponent("events.jsonl")
let cfgFile = home.appendingPathComponent(".config/vtc.json")

func cfg() -> [String: Any] {
    if let d = try? Data(contentsOf: cfgFile), let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] { return j }
    return [:]
}
func loadHotkey() -> (UInt16, NSEvent.ModifierFlags, String) {
    let spec = (cfg()["hotkey"] as? String ?? "ctrl+cmd+s").lowercased()
    var mods: NSEvent.ModifierFlags = []; var key: UInt16 = 1
    let codes: [String: UInt16] = ["a":0,"s":1,"d":2,"f":3,"h":4,"g":5,"z":6,"x":7,"c":8,"v":9,"b":11,"q":12,"w":13,"e":14,"r":15,"y":16,"t":17,"1":18,"2":19,"3":20,"4":21,"6":22,"5":23,"9":25,"7":26,"8":28,"0":29,"o":31,"u":32,"i":34,"p":35,"l":37,"j":38,"k":40,"n":45,"m":46,"space":49,"f1":122,"f2":120,"f3":99,"f4":118,"f5":96,"f6":97,"f7":98,"f8":100,"f9":101,"f10":109,"f11":103,"f12":111]
    for part in spec.split(separator: "+").map({ String($0).trimmingCharacters(in: .whitespaces) }) {
        switch part {
        case "ctrl", "control", "⌃": mods.insert(.control)
        case "cmd", "command", "⌘": mods.insert(.command)
        case "alt", "opt", "option", "⌥": mods.insert(.option)
        case "shift", "⇧": mods.insert(.shift)
        default: if let c = codes[part] { key = c }
        }
    }
    return (key, mods, spec)
}
func runArgs(_ args: [String]) {
    let p = Process(); p.executableURL = URL(fileURLWithPath: python); p.arguments = [vtc] + args
    do { try p.run() } catch { NSLog("vtc: failed to run vtc.py \(args): \(error)") }
}
func run(_ cmd: String) { runArgs([cmd]) }

// ---------------- state ----------------
struct State { var phase: String; var started: Double; var cap: Double; var warn: Double }
func readState() -> State? {
    guard let d = try? Data(contentsOf: stateFile), let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return nil }
    let c = cfg()
    return State(phase: j["phase"] as? String ?? "recording", started: j["started"] as? Double ?? Date().timeIntervalSince1970,
                 cap: (c["cap_seconds"] as? Double) ?? 600, warn: (c["warn_seconds"] as? Double) ?? 20)
}

// ---------------- UI ----------------
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
let menu = NSMenu()
let toggleItem = NSMenuItem(title: "Start recording", action: #selector(Ctl.toggle), keyEquivalent: "")
let cancelItem = NSMenuItem(title: "Cancel recording (Esc)", action: #selector(Ctl.cancel), keyEquivalent: "")
class Ctl: NSObject {
    @objc func toggle() { run("toggle") }
    @objc func cancel() { run("cancel") }
    @objc func openFolder() { NSWorkspace.shared.open(URL(fileURLWithPath: (cfg()["recordings"] as? String) ?? (home.appendingPathComponent("Video to Claude/recordings").path))) }
    @objc func openSettings() { NSWorkspace.shared.open(cfgFile) }
    @objc func cleanOld() { run("clean") }
    @objc func cleanAll() {
        let a = NSAlert(); a.messageText = "Delete all recordings?"; a.informativeText = "Every take in the recordings folder (videos, sheets, transcripts) will be removed."
        a.addButton(withTitle: "Delete all"); a.addButton(withTitle: "Cancel"); a.alertStyle = .warning
        NSApp.activate(ignoringOtherApps: true)
        if a.runModal() == .alertFirstButtonReturn { runArgs(["clean", "--all"]) }
    }
    @objc func quit() { app.terminate(nil) }
}
let ctl = Ctl()
for mi in [toggleItem, cancelItem] { mi.target = ctl; menu.addItem(mi) }
menu.addItem(.separator())
let f = NSMenuItem(title: "Open recordings folder", action: #selector(Ctl.openFolder), keyEquivalent: ""); f.target = ctl; menu.addItem(f)
let s = NSMenuItem(title: "Settings (vtc.json)", action: #selector(Ctl.openSettings), keyEquivalent: ""); s.target = ctl; menu.addItem(s)
menu.addItem(.separator())
let c1 = NSMenuItem(title: "Delete recordings older than the keep period", action: #selector(Ctl.cleanOld), keyEquivalent: ""); c1.target = ctl; menu.addItem(c1)
let c2 = NSMenuItem(title: "Delete all recordings…", action: #selector(Ctl.cleanAll), keyEquivalent: ""); c2.target = ctl; menu.addItem(c2)
menu.addItem(.separator())
let q = NSMenuItem(title: "Quit Video to Claude", action: #selector(Ctl.quit), keyEquivalent: ""); q.target = ctl; menu.addItem(q)
item.menu = menu

// countdown panel (last `warn` seconds), top-right, never takes focus
let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 260, height: 90), styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: true)
panel.level = .statusBar; panel.isOpaque = false; panel.backgroundColor = .clear; panel.hasShadow = true; panel.ignoresMouseEvents = true
let box = NSVisualEffectView(frame: panel.contentView!.bounds); box.material = .hudWindow; box.state = .active; box.wantsLayer = true; box.layer?.cornerRadius = 16
let label = NSTextField(labelWithString: ""); label.font = NSFont.monospacedDigitSystemFont(ofSize: 40, weight: .bold); label.alignment = .center
label.textColor = NSColor(calibratedRed: 1, green: 0.3, blue: 0.3, alpha: 1); label.frame = NSRect(x: 0, y: 28, width: 260, height: 50)
let sub = NSTextField(labelWithString: "seconds left · hotkey to stop"); sub.font = .systemFont(ofSize: 12); sub.alignment = .center; sub.textColor = .white; sub.frame = NSRect(x: 0, y: 8, width: 260, height: 18)
box.addSubview(label); box.addSubview(sub); panel.contentView = box
func placePanel() {
    if let scr = NSScreen.main { panel.setFrameOrigin(NSPoint(x: scr.visibleFrame.maxX - 280, y: scr.visibleFrame.maxY - 110)) }
}

func attributed(_ text: String, _ color: NSColor) -> NSAttributedString {
    NSAttributedString(string: text, attributes: [.foregroundColor: color, .font: NSFont.monospacedDigitSystemFont(ofSize: 13, weight: .semibold)])
}
var lastPhase = ""
func refresh() {
    let st = readState()
    let now = Date().timeIntervalSince1970
    if let st = st, st.phase == "recording" {
        let el = max(0, now - st.started); let left = st.cap - el
        let mm = Int(el) / 60, ss = Int(el) % 60
        if left <= st.warn {
            item.button?.attributedTitle = attributed(String(format: "⚠ %d s", Int(left.rounded())), .systemRed)
            label.stringValue = String(format: "%d", Int(left.rounded())); if !panel.isVisible { placePanel(); panel.orderFrontRegardless() }
        } else {
            item.button?.attributedTitle = attributed(String(format: "● %d:%02d", mm, ss), .systemRed); if panel.isVisible { panel.orderOut(nil) }
        }
        toggleItem.title = "Stop recording"; cancelItem.isHidden = false
    } else if let st = st, st.phase == "processing" {
        item.button?.attributedTitle = attributed("⟳ processing", .labelColor); if panel.isVisible { panel.orderOut(nil) }
        toggleItem.title = "Start recording"; cancelItem.isHidden = true
    } else {
        item.button?.attributedTitle = attributed("○", .labelColor); if panel.isVisible { panel.orderOut(nil) }
        toggleItem.title = "Start recording"; cancelItem.isHidden = true
    }
    // macOS blocks every global hotkey while an app holds secure keyboard input (a focused password field)
    if IsSecureEventInputEnabled() && (st == nil) {
        item.button?.attributedTitle = attributed("○ hotkey blocked: password field", .systemOrange)
    }
    let phase = st?.phase ?? "idle"
    if phase != lastPhase { lastPhase = phase; checkRecording(force: true) }
}
Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { _ in refresh() }

// ---------------- mouse event log (only while recording) ----------------
var recording = false
var lastMoveWrite = Date.distantPast
var handle: FileHandle? = nil
func checkRecording(force: Bool = false) {
    let isRec = readState()?.phase == "recording"
    if isRec && !recording {
        recording = true
        let scr = NSScreen.screens.first?.frame ?? .zero
        let scale = NSScreen.screens.first?.backingScaleFactor ?? 2
        let header = "{\"k\":\"screen\",\"w\":\(Int(scr.width)),\"h\":\(Int(scr.height)),\"scale\":\(scale),\"t\":\(Date().timeIntervalSince1970)}\n"
        try? header.write(to: eventsFile, atomically: true, encoding: .utf8)
        handle = try? FileHandle(forWritingTo: eventsFile); handle?.seekToEndOfFile()
    } else if !isRec && recording {
        recording = false; try? handle?.close(); handle = nil
    }
}
func logEvent(_ kind: String) {
    guard recording, let h = handle else { return }
    let now = Date()
    if kind == "move" { if now.timeIntervalSince(lastMoveWrite) < 0.033 { return }; lastMoveWrite = now }
    let p = NSEvent.mouseLocation
    h.write("{\"k\":\"\(kind)\",\"x\":\(Int(p.x)),\"y\":\(Int(p.y)),\"t\":\(now.timeIntervalSince1970)}\n".data(using: .utf8)!)
}

// ---------------- hotkey / Esc ----------------
let (keyCode, modifiers, spec) = loadHotkey()
var armed = false
var lastToggle = Date.distantPast
func requestMediaAccess() {
    // ask for the mic (and screen) for THIS identity; child ffmpeg processes inherit the grant
    if AVCaptureDevice.authorizationStatus(for: .audio) != .authorized {
        AVCaptureDevice.requestAccess(for: .audio) { ok in NSLog("vtc: microphone access \(ok ? "granted" : "denied")") }
    }
    if !CGPreflightScreenCaptureAccess() { CGRequestScreenCaptureAccess(); NSLog("vtc: requested Screen Recording access") }
}
func arm() {
    armed = true
    requestMediaAccess()
    NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { e in
        let mods = e.modifierFlags.intersection([.control, .command, .option, .shift])
        if e.keyCode == keyCode && mods == modifiers {
            if Date().timeIntervalSince(lastToggle) > 0.8 { lastToggle = Date(); run("toggle") }
        } else if e.keyCode == 53 /* Esc */ && mods.isEmpty && recording {
            run("cancel")
        }
    }
    NSEvent.addGlobalMonitorForEvents(matching: [.mouseMoved, .leftMouseDragged]) { _ in logEvent("move") }
    NSEvent.addGlobalMonitorForEvents(matching: .leftMouseDown) { _ in logEvent("down") }
    NSEvent.addGlobalMonitorForEvents(matching: .rightMouseDown) { _ in logEvent("rdown") }
    NSEvent.addGlobalMonitorForEvents(matching: .scrollWheel) { _ in logEvent("scroll") }
    NSLog("vtc: armed, hotkey = \(spec)")
}
let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
if AXIsProcessTrustedWithOptions(opts) { arm() } else {
    NSLog("vtc: not trusted yet — System Settings → Privacy & Security → Accessibility → vtc-hotkey (remove with −, add again with +)")
    item.button?.attributedTitle = attributed("○ grant Accessibility", .systemOrange)
    Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { t in if !armed && AXIsProcessTrusted() { arm(); t.invalidate() } }
}
refresh()
app.run()
