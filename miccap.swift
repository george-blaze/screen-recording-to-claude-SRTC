// miccap — microphone capture through Apple's voice-processing DSP (beamforming, auto gain, noise
// suppression: what FaceTime/Slack use), written as s16le 48 kHz mono PCM to stdout for ffmpeg.
//   miccap [--device NAME] [--raw] [--seconds N]
// --device picks the mic by (sub)name: it becomes the system default input for the run and the
// previous default is restored on exit (voice processing can only run on the default input).
// --raw skips voice processing (plain mic, like ffmpeg's avfoundation input).
import AVFoundation
import CoreAudio
import Foundation

var devName: String? = nil; var raw = false; var seconds = 0.0
var args = Array(CommandLine.arguments.dropFirst())
while !args.isEmpty {
    let a = args.removeFirst()
    switch a {
    case "--device": if !args.isEmpty { devName = args.removeFirst() }
    case "--raw": raw = true
    case "--seconds": if !args.isEmpty { seconds = Double(args.removeFirst()) ?? 0 }
    default: break
    }
}
signal(SIGPIPE, SIG_IGN)
func log(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }
func norm(_ s: String) -> String { s.lowercased().replacingOccurrences(of: "\u{2019}", with: "'") }

// ---- CoreAudio helpers
func addr(_ sel: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: sel, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}
func deviceName(_ id: AudioDeviceID) -> String {
    var a = addr(kAudioObjectPropertyName); var v: Unmanaged<CFString>? = nil; var sz = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(id, &a, 0, nil, &sz, &v) == noErr, let s = v?.takeRetainedValue() else { return "?" }
    return s as String
}
func inputChannels(_ id: AudioDeviceID) -> Int {
    var a = addr(kAudioDevicePropertyStreamConfiguration, kAudioDevicePropertyScopeInput); var sz: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(id, &a, 0, nil, &sz) == noErr, sz > 0 else { return 0 }
    let buf = UnsafeMutablePointer<AudioBufferList>.allocate(capacity: Int(sz)); defer { buf.deallocate() }
    guard AudioObjectGetPropertyData(id, &a, 0, nil, &sz, buf) == noErr else { return 0 }
    return UnsafeMutableAudioBufferListPointer(buf).reduce(0) { $0 + Int($1.mNumberChannels) }
}
func inputDevices() -> [AudioDeviceID] {
    var a = addr(kAudioHardwarePropertyDevices); var sz: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &sz) == noErr else { return [] }
    var ids = [AudioDeviceID](repeating: 0, count: Int(sz) / MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &sz, &ids) == noErr else { return [] }
    return ids.filter { inputChannels($0) > 0 }
}
func defaultInput() -> AudioDeviceID {
    var a = addr(kAudioHardwarePropertyDefaultInputDevice); var id: AudioDeviceID = 0; var sz = UInt32(MemoryLayout<AudioDeviceID>.size)
    _ = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &sz, &id); return id
}
@discardableResult func setDefaultInput(_ id: AudioDeviceID) -> Bool {
    var a = addr(kAudioHardwarePropertyDefaultInputDevice); var v = id
    return AudioObjectSetPropertyData(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, UInt32(MemoryLayout<AudioDeviceID>.size), &v) == noErr
}

// ---- pick the mic
var restoreTo: AudioDeviceID? = nil
if let want = devName, !want.isEmpty {
    let devs = inputDevices()
    let hit = devs.first { norm(deviceName($0)) == norm(want) } ?? devs.first { norm(deviceName($0)).contains(norm(want)) }
    if let id = hit {
        let cur = defaultInput()
        if cur != id {
            if setDefaultInput(id) { restoreTo = cur; log("mic: default input → \(deviceName(id)) (was \(deviceName(cur)); restored on exit)") }
            else { log("mic: could not select \(deviceName(id)); using \(deviceName(cur))") }
        }
    } else { log("mic: no input named '\(want)'; using \(deviceName(defaultInput()))") }
}
func cleanup() { if let r = restoreTo { setDefaultInput(r); restoreTo = nil } }
atexit { cleanup() }
var running = true
for sig in [SIGINT, SIGTERM, SIGHUP] {
    signal(sig, SIG_IGN)
    let src = DispatchSource.makeSignalSource(signal: sig, queue: .main); src.setEventHandler { running = false }; src.resume()
    _ = Unmanaged.passRetained(src)
}

// ---- permission (no-op when the responsible process already holds the Microphone grant)
if ProcessInfo.processInfo.environment["MICCAP_SKIP_PERM"] == nil {
    let sem = DispatchSemaphore(value: 0); var granted = false
    AVCaptureDevice.requestAccess(for: .audio) { ok in granted = ok; sem.signal() }
    if sem.wait(timeout: .now() + 8) == .timedOut || !granted { log("microphone permission missing"); cleanup(); exit(2) }
}

// ---- engine
let engine = AVAudioEngine()
let input = engine.inputNode
if !raw {
    do { try input.setVoiceProcessingEnabled(true) } catch { log("voice processing unavailable (\(error)); raw mic"); raw = true }
}
if !raw, #available(macOS 14.0, *) {
    input.voiceProcessingOtherAudioDuckingConfiguration = AVAudioVoiceProcessingOtherAudioDuckingConfiguration(enableAdvancedDucking: false, duckingLevel: .min)
}
let inFmt = input.outputFormat(forBus: 0)
guard inFmt.sampleRate > 0, inFmt.channelCount > 0 else { log("no input format (is a microphone connected?)"); cleanup(); exit(2) }
let monoFmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: inFmt.sampleRate, channels: 1, interleaved: false)!
let outFmt = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 48000, channels: 1, interleaved: true)!
guard let conv = AVAudioConverter(from: monoFmt, to: outFmt) else { log("no converter"); cleanup(); exit(2) }
let out = FileHandle.standardOutput
let writeQ = DispatchQueue(label: "miccap.write")     // never block CoreAudio's render thread on the pipe
var written = 0
input.installTap(onBus: 0, bufferSize: 4800, format: inFmt) { buf, _ in
    guard let src = buf.floatChannelData, buf.frameLength > 0,
          let mono = AVAudioPCMBuffer(pcmFormat: monoFmt, frameCapacity: buf.frameLength) else { return }
    mono.frameLength = buf.frameLength
    mono.floatChannelData![0].update(from: src[0], count: Int(buf.frameLength))   // processed voice is channel 0
    let cap = AVAudioFrameCount(Double(buf.frameLength) * 48000.0 / inFmt.sampleRate) + 32
    guard let ob = AVAudioPCMBuffer(pcmFormat: outFmt, frameCapacity: cap) else { return }
    var fed = false; var err: NSError? = nil
    let st = conv.convert(to: ob, error: &err) { _, status in
        if fed { status.pointee = .noDataNow; return nil }
        fed = true; status.pointee = .haveData; return mono
    }
    if st == .error || ob.frameLength == 0 { return }
    let data = Data(bytes: ob.int16ChannelData![0], count: Int(ob.frameLength) * 2)
    writeQ.async {
        guard running else { return }
        do { try out.write(contentsOf: data); written += data.count } catch { running = false }   // ffmpeg went away
    }
}
// device went away / default input changed mid-take: try to restart the engine, else end the stream (ffmpeg sees EOF)
NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main) { _ in
    log("miccap: audio configuration changed; restarting engine")
    engine.prepare()
    do { try engine.start() } catch { log("miccap: restart failed: \(error)"); running = false }
}
engine.prepare()
do { try engine.start() } catch { log("engine start failed: \(error)"); cleanup(); exit(2) }
log("miccap: \(raw ? "raw" : "voice-processed") \(deviceName(defaultInput())) \(Int(inFmt.sampleRate)) Hz ×\(inFmt.channelCount) → s16le 48000 mono")

let t0 = Date()
while running {
    RunLoop.main.run(until: Date().addingTimeInterval(0.2))
    if seconds > 0 && Date().timeIntervalSince(t0) >= seconds { running = false }
}
engine.stop(); input.removeTap(onBus: 0)
writeQ.sync {}
try? out.close()
cleanup()
log("miccap: wrote \(written / 96000) s")
exit(0)
