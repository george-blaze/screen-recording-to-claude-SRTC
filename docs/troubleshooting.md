# Troubleshooting

Logs live in `~/Library/Application Support/VideoToClaude/`: `hotkey.log` (agent), `runner.log` (each take),
`recorder-ffmpeg.log` (capture), `asr.log` (Whisper worker). The menu-bar item is the quickest signal.

## Hotkey does nothing

**Menu bar says "hotkey blocked: password field".** An app holds macOS secure keyboard input, which disables every
global hotkey on the system while a password field is focused (browsers do this, Terminal with "Secure Keyboard
Entry" too). Click out of the field or close the dialog. To see who holds it:
```bash
ioreg -l -w 0 | grep -o '"kCGSSessionSecureInputPID"=[0-9]*' | head -1 | grep -o '[0-9]*$' | xargs -I{} ps -p {} -o comm=
```

**Menu bar is orange / log says "not trusted".** The Accessibility grant is missing or belongs to an older build.
System Settings → Privacy & Security → Accessibility: select `vtc-hotkey`, **−**, then **+** and add
`~/.claude/skills/srtc/vtc-hotkey` (or drag it from Finder: `open -R ~/.claude/skills/srtc/vtc-hotkey`). The agent arms
itself within two seconds; no restart needed. This happens after rebuilding a helper without running `sign.sh`.

**The combination opens something else.** ⌃⌘S is free in most apps, but the Claude app grabs ⌥⌘R, Chrome grabs
many ⌘ combos. Change `hotkey` in `~/.config/vtc.json` and restart the agent.

**Agent not running at all.** `launchctl print gui/$(id -u)/com.srtc.hotkey | grep state` should say `running`.
If not: `./install.sh` again.

## "Nothing was recorded"

Screen Recording or Microphone is not granted to `vtc-hotkey`, or Screen Recording was granted but the agent has not
been restarted since (macOS applies that one only on relaunch):
```bash
launchctl kickstart -k gui/$(id -u)/com.srtc.hotkey
```
`recorder-ffmpeg.log` shows ffmpeg's last line; "no frames arrived" means the screen grant, "Cannot use <device>"
means the microphone grant.

## Transcript empty or garbage

The prompt and `DIGEST.md` carry an **audio peak / average** line. Speech into a working mic averages about −25 to
−35 dB. Below −40 dB the live microphone was not the one you spoke into (AirPods on the desk, USB mic muted, wrong
device selected); the prompt then tells Claude to rely on the frames. Fix `mic` in the config (see settings) and look
at the input level meter in System Settings → Sound → Input while you talk.

Peak at 0 dB means clipping: lower the input volume of that device in the same pane.

If levels are fine but the transcript is still poor, you are probably on the Apple fallback engine: check that
`models/whisper-large-v3-turbo/weights.safetensors` is complete (1,613,977,612 bytes) and `asr` is `"stream"`.

## It delivered into the wrong place

Delivery pastes into whatever app is frontmost when the take finishes. Keep Claude in front, or switch to it within
the ~3 s processing window. If Claude wasn't running, the prompt is on the clipboard: paste it anywhere.
Text arrived but no images: the composer only accepts image data, which `paste` sends; if it still fails after a
Claude update, the sheet paths in the prompt let Claude read them from disk.

## Nothing happens at the 10-minute mark

The countdown and stop are driven by the runner, which needs the recorder to be ffmpeg (default). With
`"backend": "screencapture"` the cap still applies but there is no countdown sound.

## Permissions vanished after I rebuilt a helper

Always `./sign.sh` after a manual `swiftc`. Unsigned builds get a new identity on every compile and macOS drops the
grants silently. The installer signs automatically.

## Clean slate

```bash
./uninstall.sh
rm -rf ~/.config/vtc.json "~/Library/Application Support/VideoToClaude"
```
then `./install.sh` and the permission prompts again.
