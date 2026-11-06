# SRTC: Screen Recorder to Claude

### Talk and point instead of typing prompts. One hotkey records your screen and voice and drops the result into your open Claude chat as the request.

Built by **George Blaze** from **[Phase56](https://phase56.com)**.

[![macOS](https://img.shields.io/badge/macOS-15%2B%20Apple%20silicon-black)](docs/installation.md#requirements) [![Claude Code skill](https://img.shields.io/badge/Claude%20Code-skill-d97757)](SKILL.md) [![Whisper](https://img.shields.io/badge/transcription-local%20Whisper-2ea44f)](docs/installation.md#step-5-whisper-model) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Press **⌃⌘S**, explain something while you show it, press **⌃⌘S** again. Three seconds later the Claude chat in
front of you contains your spoken words as the prompt, timestamped screenshots of what you showed, and zoomed shots of
whatever you circled, rested on or clicked. Press Enter.

> **IMPORTANT.** Everything runs on your Mac: ffmpeg for capture, a local Whisper model for transcription, two small
> Swift helpers for the hotkey and the paste. No audio or video leaves the machine; what you send to Claude is what you
> see in the composer before you press Enter.

## Quick start

```bash
git clone https://github.com/george-blaze/srtc.git ~/.claude/skills/srtc
~/.claude/skills/srtc/install.sh
```

Then grant the three permissions macOS asks for (Accessibility, Microphone, Screen Recording) to **vtc-hotkey** and
restart the agent once. The full walkthrough, including what each prompt looks like and how to verify the install, is
in **[docs/installation.md](docs/installation.md)**.

### Record a take

1. Open the Claude chat you want the request in (or a new one).
2. **⌃⌘S** → a tink and a "Recording" notification; the menu bar shows `● 0:12` counting up.
3. Talk, point, circle, click. Mouse and keyboard work normally; nothing is blocked.
4. **⌃⌘S** again (Esc discards). Claude comes to the front, the images and the prompt land in the composer.
5. Enter.

### Ask Claude

What lands in the composer looks like this; Claude Code treats the narration as the instruction (a rule the installer
adds to `~/.claude/CLAUDE.md`):

```text
Screen recording request (SRTC). My spoken words are the request:
"Take this card layout and make the export panel on the right match it, same spacing, same header."
Attached: DIGEST.md, 2 pointer sheets (what I pointed at, circled or clicked, red box = cursor) and 5 sheets of what
was on my screen while I spoke; every tile is stamped mm:ss and lines up with the transcript.
```

### Use the terminal

```bash
python3 ~/.claude/skills/srtc/vtc.py status            # idle / recording / processing
python3 ~/.claude/skills/srtc/vtc.py usage             # takes on disk
python3 ~/.claude/skills/srtc/screencast/screencast.py digest ~/Desktop/demo.mov   # any recording → sheets + transcript
```

Inside Claude Code the same things are one slash command away: `/srtc digest ~/Desktop/demo.mov`,
`/srtc clean`, `/srtc status`.

### Update

```bash
cd ~/.claude/skills/srtc && git pull && ./install.sh
```

The installer is idempotent: it rebuilds only what changed, re-signs the helpers so your permission grants survive,
and leaves your `~/.config/vtc.json` alone.

## How it works

```
 ⌃⌘S ──► vtc-hotkey (menu bar, Swift) ──► vtc.py runner
            │  logs mouse moves/clicks           │
            │                                    ├─ ffmpeg: screen (5 fps) + mic as two inputs → .mp4
            │                                    ├─ 20-s wav chunks → Whisper worker (mlx) → transcript, live
            │                                    └─ 9:40 countdown sound + red panel · 10:00 auto-stop
 ⌃⌘S ──► stop ──► digest ───────────────────────► 1 fps → changed frames only → 2×2 timestamped sheets
                                                  mouse log → circle / dwell / click → zoomed pointer sheets
                                                  DIGEST.md (transcript + index) · quiet-audio check
                  deliver ──────────────────────► paste (Swift): Claude to front, images then prompt into the composer
```

## What you get

| Piece | What it does | Where |
|---|---|---|
| Hotkey toggle | ⌃⌘S start/stop, Esc cancel, configurable combo, no key blocking | `vtc-hotkey.swift` |
| Menu bar | ○ idle · ● elapsed · ⚠ last 20 s · ⟳ processing · warns when a password field blocks hotkeys | `vtc-hotkey.swift` |
| Capture | screen + mic with separate ffmpeg inputs (shared input drops ~75 % of mic audio), hardware H.264, 10-min cap | `vtc.py` |
| Live transcription | Whisper large-v3-turbo on Apple silicon, 20-s chunks while you talk, silence gate, hallucination filter | `asr_worker.py`, `vtc.py` |
| Digest | 1 fps sampling, change detection that ignores the cursor, 2×2 sheets with burned-in mm:ss | `screencast/` |
| Pointer moments | circling, resting after moving, clicks → zoomed shots with a red cursor box | `vtc.py` |
| Delivery | paste into the open chat (default), or a new Code session via `claude://code/new`, or clipboard | `paste.swift`, `vtc.py` |
| Retention | prune takes older than 7 days, keep the newest 30, menu-bar and CLI cleanup | `vtc.py` |
| Claude Code skill | `/srtc …` for digests, status, cleanup and troubleshooting | `SKILL.md` |

## FAQ

<details><summary>Does it send my recording as context or as a prompt?</summary>
As a prompt. The pasted message says "my spoken words are the request" followed by your transcript, with the images
attached. Claude acts on it when you press Enter. Type a line like "just context for now" before Enter if you want it
treated as background only.</details>

<details><summary>Which chat does it go to?</summary>
Whatever Claude chat is in front when the take finishes. Open a new chat first if you want a new one. Set
<code>"deliver": "deeplink"</code> to always open a new Code session with the files attached instead.</details>

<details><summary>Why a toggle and not hold-to-talk?</summary>
Hold-to-talk recorders have to block the keyboard while held, so you can't scroll, click or type. A toggle needs no
blocking; your hands stay free for the demo.</details>

<details><summary>Where do recordings go and how big are they?</summary>
<code>~/Movies/SRTC</code> by default: about 14 MB per minute of video plus a few MB of digest. Takes older
than 7 days are deleted automatically (keeping the newest 30); the menu bar has "Delete recordings…" items and
<code>vtc.py clean --all</code> wipes everything.</details>

<details><summary>The hotkey suddenly does nothing.</summary>
Look at the menu bar. "hotkey blocked: password field" means an app holds macOS secure keyboard input (a focused
password field, often a browser), which disables every global hotkey on the system; click out of it. If the circle is
orange, the Accessibility grant was lost: see <a href="docs/troubleshooting.md">troubleshooting</a>.</details>

<details><summary>Can I use my AirPods?</summary>
Yes. Make <code>mic</code> a priority list, e.g. <code>["AirPods Pro", "ME6S", "MacBook Pro Microphone"]</code>; the
first device that is actually connected at record time wins.</details>

<details><summary>Does Claude have this natively?</summary>
The Claude desktop app contains a "Record your screen" mode (Cowork tab) that attaches a screen + voice recording to the
chat, capped at 10 minutes, no hotkey, gated server-side per account. This tool exists because that gate was off and
because the Code tab, a hotkey and pointer zooms were wanted. Findings in <code>screencast/teardown.md</code>.</details>

## Documentation

- [Installation guide](docs/installation.md): requirements, every step, the permission prompts, verification, update, uninstall.
- [Settings reference](docs/settings.md): every key in `~/.config/vtc.json`.
- [Troubleshooting](docs/troubleshooting.md): symptoms → causes → fixes, with the log files to look at.
- [Blueprint](screencast/BLUEPRINT.md) and [teardown](screencast/teardown.md): design decisions, measurements, and what
  was learned from Claude.app's deep links and native recorder and from VoiceOS.

## Disclaimer

This is a personal tool that drives the Claude desktop app through its public `claude://` links and the macOS
pasteboard. It copies nothing from Anthropic's or anyone else's software; the teardown notes document interfaces only.
Expect to re-check the delivery step after major Claude app updates.

## License

MIT. See [LICENSE](LICENSE). © 2026 George Blaze, Phase56.
