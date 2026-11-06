// audio-input: list / get / set the macOS default input device (CoreAudio). No dependencies.
//   audio-input list          → one device per line, "* " marks the current default input
//   audio-input get           → name of the current default input
//   audio-input set <name>    → make that device (exact or substring match) the default input
import Foundation
import CoreAudio

func devices() -> [(AudioDeviceID, String)] {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size)
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids)
    return ids.compactMap { id in
        var inAddr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreamConfiguration, mScope: kAudioDevicePropertyScopeInput, mElement: kAudioObjectPropertyElementMain)
        var sz: UInt32 = 0
        AudioObjectGetPropertyDataSize(id, &inAddr, 0, nil, &sz)
        let buf = UnsafeMutablePointer<AudioBufferList>.allocate(capacity: Int(sz)); defer { buf.deallocate() }
        AudioObjectGetPropertyData(id, &inAddr, 0, nil, &sz, buf)
        let channels = UnsafeMutableAudioBufferListPointer(buf).reduce(0) { $0 + Int($1.mNumberChannels) }
        guard channels > 0 else { return nil }
        var nameAddr = AudioObjectPropertyAddress(mSelector: kAudioObjectPropertyName, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var name: CFString = "" as CFString
        var nsz = UInt32(MemoryLayout<CFString>.size)
        AudioObjectGetPropertyData(id, &nameAddr, 0, nil, &nsz, &name)
        return (id, name as String)
    }
}
func defaultInput() -> AudioDeviceID {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultInputDevice, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var id: AudioDeviceID = 0; var size = UInt32(MemoryLayout<AudioDeviceID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &id)
    return id
}
let args = CommandLine.arguments.dropFirst()
let cmd = args.first ?? "list"
let devs = devices(); let cur = defaultInput()
switch cmd {
case "list": for (id, n) in devs { print((id == cur ? "* " : "  ") + n) }
case "get": print(devs.first { $0.0 == cur }?.1 ?? "")
case "set":
    let want = args.dropFirst().joined(separator: " ").lowercased()
    guard !want.isEmpty, let dev = devs.first(where: { $0.1.lowercased() == want }) ?? devs.first(where: { $0.1.lowercased().contains(want) }) else {
        FileHandle.standardError.write("no input device matching \"\(want)\"\n".data(using: .utf8)!); exit(1)
    }
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultInputDevice, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var id = dev.0
    let st = AudioObjectSetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, UInt32(MemoryLayout<AudioDeviceID>.size), &id)
    if st != noErr { FileHandle.standardError.write("failed (\(st))\n".data(using: .utf8)!); exit(2) }
    print("default input → \(dev.1)")
default: FileHandle.standardError.write("usage: audio-input list|get|set <name>\n".data(using: .utf8)!); exit(2)
}
