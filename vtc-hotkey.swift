// SRTC (Screen Recording To Claude) agent v3: menu-bar status + countdown + hotkey + Esc cancel + mouse log. One binary, one set of grants.
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
func notifyUser(_ title: String, _ text: String) {
    let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
    p.arguments = ["-e", "display notification \"\(text)\" with title \"\(title)\""]
    do { try p.run() } catch { NSLog("vtc: failed to notify: \(error)") }
}

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
let micTestItem = NSMenuItem(title: "Test microphone (5 s)", action: #selector(Ctl.micTest), keyEquivalent: "")
var micTesting = false
class Ctl: NSObject {
    @objc func toggle() { toggleAction() }
    @objc func cancel() { if counting { cancelCountdown() } else { run("cancel") } }
    @objc func micTest() {
        guard !micTesting, readState()?.phase != "recording" else { return }
        micTesting = true; micTestItem.isEnabled = false
        item.button?.attributedTitle = attributed("◌ mic test", .labelColor)
        DispatchQueue.global(qos: .userInitiated).async {
            // runs `vtc.py mictest`: records 5 s, analyses, posts its own notification (~8 s total)
            let p = Process(); p.executableURL = URL(fileURLWithPath: python); p.arguments = [vtc, "mictest"]
            var failed = false
            do { try p.run(); p.waitUntilExit(); failed = p.terminationStatus != 0 }
            catch { NSLog("vtc: failed to run vtc.py mictest: \(error)"); failed = true }
            DispatchQueue.main.async {
                micTesting = false; micTestItem.isEnabled = true
                if failed { notifyUser("SRTC", "Mic test failed — see runner.log") }
                refresh()
            }
        }
    }
    @objc func openFolder() { NSWorkspace.shared.open(URL(fileURLWithPath: (cfg()["recordings"] as? String) ?? (home.appendingPathComponent("Movies/SRTC").path))) }
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
micTestItem.target = ctl; menu.addItem(micTestItem)
let s = NSMenuItem(title: "Settings (vtc.json)", action: #selector(Ctl.openSettings), keyEquivalent: ""); s.target = ctl; menu.addItem(s)
menu.addItem(.separator())
let c1 = NSMenuItem(title: "Delete recordings older than the keep period", action: #selector(Ctl.cleanOld), keyEquivalent: ""); c1.target = ctl; menu.addItem(c1)
let c2 = NSMenuItem(title: "Delete all recordings…", action: #selector(Ctl.cleanAll), keyEquivalent: ""); c2.target = ctl; menu.addItem(c2)
menu.addItem(.separator())
let q = NSMenuItem(title: "Quit SRTC", action: #selector(Ctl.quit), keyEquivalent: ""); q.target = ctl; menu.addItem(q)
menu.autoenablesItems = false  // we toggle micTestItem.isEnabled by hand
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

// ---------------- 3-2-1 before a take, "finished" after it ----------------
var counting = false
var countTimer: Timer? = nil
var toastUntil = Date.distantPast
let red = NSColor(calibratedRed: 1, green: 0.3, blue: 0.3, alpha: 1)
let green = NSColor(calibratedRed: 0.35, green: 0.9, blue: 0.5, alpha: 1)
func showPanel(_ big: String, _ small: String, _ color: NSColor, size: CGFloat = 40) {
    label.stringValue = big; label.textColor = color; label.font = NSFont.monospacedDigitSystemFont(ofSize: size, weight: .bold)
    sub.stringValue = small
    if !panel.isVisible { placePanel(); panel.orderFrontRegardless() }
}
func hidePanel() { if panel.isVisible { panel.orderOut(nil) } }
func startCountdown() {
    guard !counting else { return }
    counting = true; var n = 3
    showPanel("3", "recording starts · Esc to abort", red)
    item.button?.attributedTitle = attributed("● 3", .systemRed)
    countTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { t in
        n -= 1
        if n > 0 {
            showPanel("\(n)", "recording starts · Esc to abort", red); item.button?.attributedTitle = attributed("● \(n)", .systemRed)
        } else {
            t.invalidate(); countTimer = nil; counting = false
            showPanel("GO", "talk after the chime", red)      // runner plays the start chime once the mic is live
            toastUntil = Date().addingTimeInterval(1.5)
            run("toggle")
        }
    }
}
func cancelCountdown() {
    countTimer?.invalidate(); countTimer = nil; counting = false; hidePanel(); refresh()
}
func toggleAction() {
    if counting { cancelCountdown(); return }
    if readState()?.phase == "recording" { run("toggle") }          // stop
    else if readState() == nil { startCountdown() }                 // idle → 3-2-1 → start
}
func showFinished() {
    showPanel("Finished", "recording session delivered", green, size: 30)
    toastUntil = Date().addingTimeInterval(3.0)
}

func attributed(_ text: String, _ color: NSColor) -> NSAttributedString {
    NSAttributedString(string: text, attributes: [.foregroundColor: color, .font: NSFont.monospacedDigitSystemFont(ofSize: 13, weight: .semibold)])
}
// ---------------- live mic meter (menu bar, while recording) ----------------
// miccap rewrites level.txt ~10×/s; the last ~1 s scrolls through as bars so you can see the mic is hearing you.
let levelFile = stateDir.appendingPathComponent("level.txt")
var meter = [Double](repeating: 0, count: 10)
var meterTimer: Timer? = nil
var silentSince: Date? = nil
var recTitle = ""                                        // "● 0:12" / "⚠ 9 s", set by refresh(); the meter appends the bars
func readLevel() -> Double? {
    guard let a = try? FileManager.default.attributesOfItem(atPath: levelFile.path), let m = a[.modificationDate] as? Date,
          Date().timeIntervalSince(m) < 1.5,                       // stale file = the mic process is gone
          let s = try? String(contentsOf: levelFile, encoding: .utf8), let v = Double(s.trimmingCharacters(in: .whitespacesAndNewlines)) else { return nil }
    return v
}
func drawMeter() {
    let lv = readLevel()
    meter.removeFirst(); meter.append(lv ?? 0)
    if (lv ?? 0) > 0.06 { silentSince = nil } else if silentSince == nil { silentSince = Date() }
    let blocks = Array("▁▂▃▄▅▆▇█")
    let bars = String(meter.map { blocks[min(7, Int($0 * 7.99))] })
    let dead = lv == nil || (silentSince.map { Date().timeIntervalSince($0) > 4 } ?? false)
    let text = recTitle + " " + bars + (dead ? (lv == nil ? " no mic" : " silent") : "")
    item.button?.attributedTitle = attributed(text, dead ? .systemOrange : .systemRed)
}
func setMeter(on: Bool) {
    if on && meterTimer == nil {
        meter = [Double](repeating: 0, count: 10); silentSince = nil
        meterTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { _ in drawMeter() }
    } else if !on, let t = meterTimer { t.invalidate(); meterTimer = nil }
}

var lastPhase = ""
func refresh() {
    let st = readState()
    let now = Date().timeIntervalSince1970
    let toast = Date() < toastUntil                      // "GO" / "Finished" stays up briefly whatever the state says
    if counting { toggleItem.title = "Abort countdown"; cancelItem.isHidden = false; micTestItem.isEnabled = false; return }
    if let st = st, st.phase == "recording" {
        let el = max(0, now - st.started); let left = st.cap - el
        let mm = Int(el) / 60, ss = Int(el) % 60
        if left <= st.warn {
            recTitle = String(format: "⚠ %d s", Int(left.rounded()))
            showPanel(String(format: "%d", Int(left.rounded())), "seconds left · hotkey to stop", red)
        } else {
            recTitle = String(format: "● %d:%02d", mm, ss); if !toast { hidePanel() }
        }
        setMeter(on: true); drawMeter()
        toggleItem.title = "Stop recording"; cancelItem.isHidden = false; micTestItem.isEnabled = false
    } else if let st = st, st.phase == "processing" {
        setMeter(on: false)
        item.button?.attributedTitle = attributed("⟳ processing", .labelColor); if !toast { hidePanel() }
        toggleItem.title = "Start recording"; cancelItem.isHidden = true; micTestItem.isEnabled = !micTesting
    } else {
        setMeter(on: false)
        item.button?.attributedTitle = attributed(micTesting ? "◌ mic test" : "○", .labelColor); if !toast { hidePanel() }
        toggleItem.title = "Start recording"; cancelItem.isHidden = true; micTestItem.isEnabled = !micTesting
    }
    // macOS blocks every global hotkey while an app holds secure keyboard input (a focused password field)
    if IsSecureEventInputEnabled() && (st == nil) && !micTesting {
        item.button?.attributedTitle = attributed("○ hotkey blocked: password field", .systemOrange)
    }
    let phase = st?.phase ?? "idle"
    if phase != lastPhase {
        if lastPhase == "processing" && phase == "idle" { showFinished() }
        lastPhase = phase; checkRecording(force: true)
    }
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
        // every display's frame (global points, origin bottom-left), in the order avfoundation lists "Capture screen N"
        let screens = NSScreen.screens.map { s in
            "{\"x\":\(Int(s.frame.origin.x)),\"y\":\(Int(s.frame.origin.y)),\"w\":\(Int(s.frame.width)),\"h\":\(Int(s.frame.height)),\"scale\":\(s.backingScaleFactor)}"
        }.joined(separator: ",")
        let header = "{\"k\":\"screen\",\"w\":\(Int(scr.width)),\"h\":\(Int(scr.height)),\"scale\":\(scale),\"screens\":[\(screens)],\"t\":\(Date().timeIntervalSince1970)}\n"
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
            if Date().timeIntervalSince(lastToggle) > 0.8 { lastToggle = Date(); DispatchQueue.main.async { toggleAction() } }
        } else if e.keyCode == 53 /* Esc */ && mods.isEmpty && (recording || counting) {
            DispatchQueue.main.async { if counting { cancelCountdown() } else { run("cancel") } }
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
