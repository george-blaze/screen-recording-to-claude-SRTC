// On-device speech-to-text using Apple's SpeechAnalyzer (macOS 26+).
// Usage: transcribe <audio file> [locale]   → prints "[mm:ss] text" lines.
import Foundation
import Speech
import AVFoundation

func fmt(_ t: CMTime) -> String {
    let s = max(0, Int(CMTimeGetSeconds(t).rounded()))
    return String(format: "%02d:%02d", s / 60, s % 60)
}

let args = CommandLine.arguments
guard args.count >= 2 else { FileHandle.standardError.write("usage: transcribe <audio> [locale]\n".data(using: .utf8)!); exit(2) }
let url = URL(fileURLWithPath: args[1])
let locale = Locale(identifier: args.count >= 3 ? args[2] : "en-US")

let sem = DispatchSemaphore(value: 0)
var exitCode: Int32 = 0
Task {
    do {
        let transcriber = SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [], attributeOptions: [])
        if let req = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            FileHandle.standardError.write("downloading speech model…\n".data(using: .utf8)!)
            try await req.downloadAndInstall()
        }
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let file = try AVAudioFile(forReading: url)
        let collector = Task {
            for try await result in transcriber.results where result.isFinal {
                let text = String(result.text.characters).trimmingCharacters(in: .whitespacesAndNewlines)
                if !text.isEmpty { print("[\(fmt(result.range.start))] \(text)") }
            }
        }
        if let last = try await analyzer.analyzeSequence(from: file) {
            try await analyzer.finalizeAndFinish(through: last)
        } else {
            await analyzer.cancelAndFinishNow()
        }
        try await collector.value
    } catch {
        FileHandle.standardError.write("transcribe error: \(error)\n".data(using: .utf8)!)
        exitCode = 1
    }
    sem.signal()
}
sem.wait()
exit(exitCode)
