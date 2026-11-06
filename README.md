# Video to Claude

Talk and point instead of typing prompts. Press **⌃⌘S**, show something on your screen while you explain it, press
**⌃⌘S** again, and the Claude chat in front of you fills with your words as the request plus timestamped screenshots
of what you showed and zoomed shots of what you pointed at, circled or clicked. Press Enter. That's the whole workflow.

- **Hotkey toggle, not hold** — mouse and keyboard keep working while you record.
- **Live transcription** — Whisper large-v3-turbo runs locally and transcribes 20-second chunks while you talk; the
  transcript is ready a couple of seconds after you stop. Nothing leaves your Mac.
- **Pointer moments** — circling, resting the cursor on something after moving there, and clicks become zoomed
  screenshots with a red box on the cursor, stamped mm:ss, delivered first.
- **Into the open chat** — delivery pastes into whatever Claude chat is in front of you (new session only if you open one).
- **10-minute cap** with a red countdown panel and a 20-second countdown sound; Esc cancels a take.
- **Self-cleaning** — takes older than 7 days are pruned automatically.
- Also a **Claude Code skill**: `/video-to-claude digest <file>` turns any existing .mov/.mp4 into sheets + transcript.

## Requirements
macOS 15 or newer on Apple silicon (tested on macOS 27, M3 Max), the Claude desktop app, and:
```bash
xcode-select --install                 # swiftc (compiles three small helpers)
brew install ffmpeg uv                 # recorder/digest engine, Python runner for Whisper
python3 -m pip install numpy           # change detection (Homebrew python3)
```

## Install
```bash
git clone https://github.com/george-blaze/video-to-claude.git ~/.claude/skills/video-to-claude
~/.claude/skills/video-to-claude/install.sh
```
The installer builds the helpers, signs them with a local certificate (so macOS keeps the permissions across rebuilds),
writes `~/.config/vtc.json`, appends the "narration is the request" rule to `~/.claude/CLAUDE.md`, registers the
menu-bar agent to run at login, and starts the Whisper model download (1.6 GB, one time; on-device Apple transcription
is used until it lands).

Then grant three permissions to **vtc-hotkey** in System Settings → Privacy & Security, all requested by the agent on
first launch: **Accessibility** (hotkey + mouse tracking), **Microphone**, **Screen & System Audio Recording**.
Screen Recording takes effect after a restart of the agent:
```bash
launchctl kickstart -k gui/$(id -u)/com.videotoclaude.hotkey
```
The menu-bar circle turns from orange to plain ○ when it's armed. Uninstall: `uninstall.sh` (removes the agent and
the skill link; delete the folder and `~/.config/vtc.json` to remove everything).

## Every time
| You | It |
|---|---|
| ⌃⌘S | tink · "Recording" notification · menu bar `● 0:42` counting up |
| talk, point, circle, click; use mouse and keyboard normally | mouse logged; Whisper transcribes in the background |
| ⌃⌘S again (or Esc to discard) | ~3 s later Claude comes to the front, images then the prompt land in the composer |
| Enter | Claude acts on what you said, resolving "this/here" from the frames |

At 9:40 a red countdown appears top-right with a 20-second alert loop; at 10:00 the take stops and delivers by itself.
Menu bar: ○ idle · ● recording · ⚠ last 20 s · ⟳ processing · "hotkey blocked: password field" when a focused password
field somewhere (macOS secure input) is swallowing all global hotkeys — click out of it.

## Settings — `~/.config/vtc.json`
| Key | Default | Meaning |
|---|---|---|
| `hotkey` | `ctrl+cmd+s` | modifiers + key; restart the agent after changing |
| `mic` | `default` | system input; or a device name; or a priority list `["AirPods Pro", "ME6S", "MacBook Pro Microphone"]` → first one connected wins; `none` |
| `asr` | `stream` | Whisper while recording · `mlx` after · `auto` Apple on-device · `none` |
| `deliver` | `paste` | into the open chat · `deeplink` new Code session with files attached · `clipboard` |
| `auto_send` | `false` | press Return after pasting |
| `cap_seconds` / `warn_seconds` | 600 / 20 | take limit and countdown length |
| `warn_sound` | `""` | any audio file for the countdown (default `sounds/countdown.aiff`) |
| `keep_days` / `keep_last` | 7 / 30 | retention (0 days = keep forever) |
| `grid` | `2x2` | tiles per sheet; `1x1` for dense code |
| `recordings` | `~/Movies/Video to Claude` | where takes and digests live |
`python3 screencast/screencast.py devices` lists screens and microphones.

## What Claude receives
1. **Pointer sheets** — zoomed views of each moment you circled, rested on, or clicked (red box = cursor), stamped mm:ss.
2. **Overview sheets** — every second the screen changed, 2×2 tiles, stamped mm:ss.
3. **The prompt** — your transcript as the request, plus the path of `DIGEST.md` (full transcript + sheet index).
Claude Code reads the rule in `~/.claude/CLAUDE.md` and treats the narration as the instruction.

## Command line
```bash
python3 vtc.py status | start | stop | cancel | usage | clean [--days N | --all]
python3 screencast/screencast.py digest <video.mov|mp4>     # digest any recording: sheets + transcript
python3 screencast/screencast.py record --mic default        # record without the hotkey
```

## How it works
`vtc-hotkey` (Swift, menu bar) listens for the hotkey and logs the mouse while recording. `vtc.py` runs ffmpeg with the
screen and the mic as two separate inputs (one shared input silently drops ~75% of mic buffers), writes 20-second wav
chunks that a persistent Whisper worker (`asr_worker.py`, mlx-whisper) transcribes as they close, then `screencast/`
samples the video at 1 fps, keeps only frames where ≥0.4% of pixels changed (a moving cursor doesn't count), tiles
them into sheets with burned-in timestamps, detects pointer moments from the mouse log, and `paste` (Swift) brings Claude
forward and pastes images then text into the composer. Design notes and the reverse-engineering findings behind the
choices (Claude.app's deep links and native recorder, VoiceOS's mechanics) are in `screencast/BLUEPRINT.md` and
`screencast/teardown.md`.

## Troubleshooting
- **Hotkey dead** → menu bar says "password field"? click out of it. Log says "not trusted"? remove and re-add
  `vtc-hotkey` in Accessibility. Logs: `~/Library/Application Support/VideoToClaude/`.
- **"Nothing was recorded"** → Screen Recording or Microphone not granted to `vtc-hotkey`; Screen Recording needs the
  agent restart above.
- **Empty or garbled transcript** → the digest reports the audio level; an average below −40 dB means the wrong mic was
  live. Check `mic`, and the input level meter in System Settings → Sound.
- **Rebuilt a helper and permissions vanished** → run `sign.sh` after every rebuild; it keeps the identity stable.
