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

The prompt and `DIGEST.md` carry an audio line measured on the decoded take: **peak**, **average level**, **clarity**
(the share of speech energy above 1 kHz) and true clipping (flat full-scale runs). The digest's `mic:` line reads e.g.
`- mic: MacBook Pro Microphone | audio peak: -3 dB | clarity: 18% above 1 kHz`. Healthy speech into a mic is 10–25 %
clarity. Three warnings, in priority order:

- **Muffled**: clarity below 6 % (or below 12 % with an average under −36 dB). The speaker was far from or turned away
  from the mic, or it is covered; consonants are missing. Sit closer, face the mic, or use AirPods/a headset (they win
  automatically when connected).
- **Clipping**: at least 0.2 % of speech samples flattened at full scale. Lower the input volume in System Settings →
  Sound → Input.
- **Very quiet**: peak at −18 dB or below, or average at −40 dB or below. Probably the wrong mic (AirPods on the desk,
  USB mic muted); fix `mic` in the config (see settings) and watch the input level meter while you talk.

While recording, the first 20-second chunk that contains speech is also checked: if it sounds muffled you get a macOS
notification ("Mic sounds muffled…", Sosumi) so you can fix it before losing a long take.

The digest names the device used. `./audio-input list` shows the system's inputs with the current default marked,
`./audio-input set "MacBook Pro Microphone"` switches it; apps with voice processing (Slack, Zoom, FaceTime) can change
the hardware gain of the input during a call, so check the level meter afterwards.

### Transcript is garbage / only a few words, but the level warning says nothing or says clipping

That was muffled audio: the level looked fine but there was almost no energy above 1 kHz (typical when sitting at an
external monitor with the MacBook off to the side), and Whisper cannot transcribe speech without its consonants. Check
the **clarity** number in `DIGEST.md`; below about 6 % is the cause. Fixes: sit closer, face the mic, use AirPods/a
headset, and keep `"mic_mode": "voice"` (the default; the `miccap` voice-processing path restores the highs on the
MacBook mic array). Also make sure macOS Voice Control is off: when on, it takes over the microphone.

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

## Check the mic before a long take

Menu bar ○ → **Test microphone (5 s)** (or `python3 vtc.py mictest`). Talk normally for five seconds; the notification
reports the mic used, its clarity (share of speech energy above 1 kHz, healthy is 10–25 %) and level, or the same
muffled / clipping / quiet warning a take would carry. Do this whenever you change seats, headphones or monitors.

## Whisper still gets nothing out of a take

Switch the recognizer to the one VoiceOS uses: create a Deepgram API key (free credit at deepgram.com), then in
`~/.config/vtc.json` set `"asr_engine": "deepgram"` and `"deepgram_api_key": "<key>"`. Chunks go to nova-3 over HTTPS
while you record; nothing else changes.

