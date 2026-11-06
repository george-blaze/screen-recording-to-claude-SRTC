// Paste content into the frontmost Claude window's composer (VoiceOS-style delivery).
// Usage: paste <textfile> [--send] [--files img1 img2 …]
//  1) activates the Claude desktop app  2) pastes each image as image data (the composer attaches pasted images)
//  3) pastes the text  4) optional Return
import Cocoa

var args = Array(CommandLine.arguments.dropFirst())
guard !args.isEmpty else { FileHandle.standardError.write("usage: paste <textfile> [--send] [--files …]\n".data(using: .utf8)!); exit(2) }
let textPath = args.removeFirst()
var send = false; var files: [String] = []
var i = 0
while i < args.count {
    if args[i] == "--send" { send = true } else if args[i] == "--files" { files = Array(args[(i + 1)...]); break }
    i += 1
}
let text = (try? String(contentsOfFile: textPath, encoding: .utf8)) ?? ""

func keystroke(_ key: CGKeyCode, _ flags: CGEventFlags) {
    let src = CGEventSource(stateID: .combinedSessionState)
    let down = CGEvent(keyboardEventSource: src, virtualKey: key, keyDown: true)!; down.flags = flags
    let up = CGEvent(keyboardEventSource: src, virtualKey: key, keyDown: false)!; up.flags = flags
    down.post(tap: .cghidEventTap); usleep(40_000); up.post(tap: .cghidEventTap)
}

guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: "com.anthropic.claudefordesktop").first else {
    FileHandle.standardError.write("Claude desktop app is not running\n".data(using: .utf8)!); exit(3)
}
app.activate(options: [.activateAllWindows])
usleep(700_000)

let pb = NSPasteboard.general
var pasted = 0
for f in files {
    guard let img = NSImage(contentsOfFile: f), let tiff = img.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff), let png = rep.representation(using: .png, properties: [:]) else { continue }
    pb.clearContents()
    pb.setData(png, forType: .png)
    pb.setData(tiff, forType: .tiff)
    keystroke(9 /* V */, .maskCommand)
    pasted += 1
    usleep(650_000)
}
pb.clearContents()
pb.setString(text, forType: .string)
keystroke(9, .maskCommand)
usleep(400_000)
if send { keystroke(36 /* Return */, []) }
print("pasted \(pasted) image(s) + \(text.count) chars" + (send ? ", sent" : ""))
