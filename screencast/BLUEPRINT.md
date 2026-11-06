# Video to Claude — Blueprint v5 (built; see ../README.md)

**Status 2026-11-06 00:25:** own build complete and verified end to end: hotkey → screen+mic (100% audio) → digest with pointer moments → pasted into the open chat. Remaining: Whisper model download for streaming transcription; your judgement of transcript quality on a spoken take.

---

# (earlier) Blueprint v4

## Status 23:05 — native path blocked
"Record your screen" (context mode) is behind a server-side rollout gate that is off for your account (teardown F8). Only "Record a skill" is available, which produces a skill, not a request. Path N is unavailable until Anthropic flips it. Remaining options:

| Option | Gets you | Time |
|---|---|---|
| **B1. Own build, stage 1** | hotkey toggle (macOS Shortcut), 20-min cap, 20-s warning sound + notification, digest, opens a new Code session with your narration as the prompt and the sheets attached (`claude://code/new`) | ~45 min |
| **B2. Stage 2 polish** | menu-bar app: icon states, on-screen countdown, Esc cancel, settings | +1 h |
| W. Wait | check the + menu after each Claude update for "Record your screen" | 0 |

Recommendation: B1 now, B2 only if B1 proves itself in daily use.


## Difficulty, honestly (asked 22:45)

| Path | What it is | Time | Can it be done in 30 min? |
|---|---|---|---|
| **N. Native "Share screen"** | Claude.app already records screen + clicks + voice, transcribes live, lets you trim, and attaches it to your next message in the current chat (teardown F1–F3). We add only a standing prompt rule so the narration *is* the request. | ~20 min | **Yes**, if "Share screen" shows in your Code-tab composer |
| N + hotkey | Same, started by a global hotkey | not today | No: the app has no shortcut for it (F5). A click-automation hack is brittle. |
| **B. Own build** | ffmpeg recorder + digest + `claude://code/new` delivery + menu-bar hotkey (previous blueprint) | ~2 h | No |

Native limits to know: 10 minutes per recording, you press Done and trim before it sends, and it goes into the session that is open. Longer takes or hands-free capture are what path B is for.


## Requirements added 22:50
- **20-minute limit per take**, a visible countdown starting **20 s before** the limit, and a **sound** at that moment; at 0 the take stops and is delivered.
- Against the native recorder (teardown F2): its limit is 10 minutes, hard-coded in the app bundle (`bt=6e5`), with its own "Ns left" pill from 9:00 and no sound. Not changeable without modifying Claude.app, which is off the table.
- So: path N = 10 min max, native warning, no sound. Path B = 20 min, countdown overlay + `afplay` chime, both trivial in the menu-bar app (ffmpeg `-t 1200`, timer at 1180 s).

## Path N, step by step (the 30-minute version)
1. Open the **Cowork** tab (the recorder is wired to Cowork and Chat surfaces, not the Code tab; Seen in the web bundle: targets `cowork_session`, `cowork_new`, `hub_chat`). Composer **+** → **Record your screen** (purpose: context). "Record a skill" is the other entry point (purpose: skill). Needs Computer Use on in Settings (yours is on) and macOS Screen Recording + Accessibility. Talk and point; use mouse and keyboard normally (this recorder does not block input). Press **Done**, trim if needed.
2. Leave the text box empty (or one word) and send. The rule below makes Claude treat your narration as the prompt.
3. The rule, one paragraph in `~/.claude/CLAUDE.md` (applies to every session, no skill needed):
   > When a message carries a screen recording (frames, watch-record-events, transcript) with little or no typed text, the spoken narration is my request. Use the frames and click positions to resolve "this", "here" and "that". Carry it out; ask one question only if the request is genuinely ambiguous.
4. Test one 1-minute take end to end. Done.

Decision now: open a Code session and tell me whether **Share screen** is offered in the composer. If yes → path N today. If only "Record a skill" → it is a flag on Anthropic's side; path B is the way.

---

# Video to Claude — Blueprint (second stop: pick the delivery route, then "Approved")

## What you get
Press a hotkey. Talk and show things on your screen, using mouse and keyboard normally. Press the hotkey again.
About five seconds later a Claude Code session opens in the Claude app with your words as the request and
every distinct screen you showed attached, timestamped. You press Enter (or it sends itself, your choice). No typing prompts, no screenshots, no recording apps, no export.

## The job (REA sections 1–3)
1. **Cloning:** VoiceOS's hold-to-record + inject-text behaviour (teardown.md, section A).
2. **Want:** the same, but screen + voice, non-blocking, landing in the Claude desktop chat.
3. **Project:** `~/Video to Claude`. Existing: digest prototype (measured), two Swift helpers. Nothing else on the machine changes.

## Design (decided by the teardown)

```
 hotkey ⌥⌘R ─► menu-bar app "Video to Claude"        (Swift, ~250 lines, swiftc, no Xcode project)
     │   toggle: press = start, press again = stop, Esc = cancel. Keys are NOT blocked (unlike VoiceOS).
     ▼
 ffmpeg: screen 5 fps + mic → recordings/<timestamp>.mp4   (hardware H.264, closed the instant you stop)
     ▼
 digest (measured 4 s per 10 min): 1 fps → drop unchanged seconds → 2×2 timestamped sheets
         + Apple on-device speech-to-text → DIGEST.md  (runs in parallel; see appendix for numbers)
     ▼
 deliver: open  claude://code/new?folder=<project>&q=<instruction>&file=DIGEST.md&file=sheet_001.jpg…
          Claude app opens a new Code session with the request filled in and the files attached (teardown B2)
     ▼
 /screencast skill (Claude Code): "the transcript is the user's request; the sheets are what they were
          looking at; read DIGEST.md, then each sheet, then do what was asked."
```

Why these choices:
- **Toggle, not hold.** VoiceOS blocks keys while held (`set_recording_block_keys`); that is what stops you scrolling. A toggle needs no key blocking.
- **Deep link, not paste.** `claude://code/new` with `q`, `folder` and `file` is the app's own, maintained interface (its menu uses it). Paste-injection (VoiceOS's way) stays as a fallback if an update changes the link.
- **Video, not screenshots.** VoiceOS captures screenshots; we record 5 fps video and let the digest pick the moments, so nothing you point at is missed.
- **Zero dependencies.** ffmpeg (installed), numpy (installed), two Swift helpers compiled once.

## Delivery route: your pick (second stop)

| Route | What you see | Needs | Tag |
|---|---|---|---|
| **A. Deep link, prefilled** (recommended) | New Code session opens, request + files in the box, you press Enter | nothing extra | Seen (link handled) / Inferred (prefill, not auto-send) |
| **A2. Deep link, auto-send** | same, but the app sends it for you | one simulated Return keystroke (Accessibility permission) | Inferred |
| **B. Into the session you're in** | the digest lands in a chat that is already open | that chat runs `/screencast watch` (Monitor) — it then answers each recording by itself | Seen (tool exists) |
| **C. Paste injection** | text pasted wherever your cursor is (VoiceOS style) | Accessibility permission | Inferred from VoiceOS A5 |

A and B can both be on: hotkey → deep link by default; a watching chat picks it up instead when one is running.

## Hotkey and recording defaults
- Hotkey **⌥⌘R** toggle (⌥⌃ is Claude's dictation). Esc while recording = cancel and discard.
- Menu-bar icon: ○ idle · ● recording (with elapsed time) · ⟳ processing. macOS also shows its own purple recording indicator.
- Screen 0 (main display), 5 fps, 1920 px wide, mic on. Change in the menu.
- Files: `~/Video to Claude/recordings/<date-time>.mp4` + `<date-time>.digest/` (sheets, DIGEST.md, transcript). Keep or auto-delete after N days (menu).

## What gets built (files) and what stays untouched
- `app/VideoToClaude.swift` → `Video to Claude.app` (menu bar, hotkey, ffmpeg control, deep link)
- `screencast/screencast.py` + `sheet.swift` + `transcribe.swift` (exist; calibrate on a real recording)
- `~/.claude/skills/screencast/SKILL.md` (`/screencast <digest>` and `/screencast watch`)
- `README.md` (install, permissions, uninstall = delete two folders)
- Untouched: VoiceOS, Screen Studio, your other skills, system settings. No backup needed; nothing existing is modified.

## Permissions macOS will ask for, once
Screen Recording, Microphone (ffmpeg via the app), Input Monitoring (global hotkey). Accessibility only for routes A2/C.

## Steps after approval (~2 h)
1. Confirm U1 (does the deep link prefill or send) with you looking at the screen — 1 minute.
2. Build the menu-bar app; real-screen recording test (turns U4 Seen). 
3. Build the skill; calibrate the change threshold on a real coding/browsing take; check Apple ASR on your voice (U5), switch to Whisper if poor.
4. End-to-end: hotkey → talk → hotkey → Claude answers. Report wall-clock for a 1-min and a 10-min take, with a screenshot of each state.
5. Hand-over: README written for someone who has never used a terminal.

## Decisions needed
1. Delivery route: **A** (recommended), A2, B, or C.
2. Hotkey: ⌥⌘R or another.
3. Then: "Approved" / "Change the plan: …" / "Cut the scope to …".

---

# Appendix: measurements from the feasibility spike (M3 Max, macOS 27.0.1, ffmpeg 9.0.2)

Synthetic 10-minute 1080p recording, new slide every 8 s, moving cursor, speech at the start.

| Step | Result | Tag |
|---|---|---|
| Decode + 1 fps sample + thumbnails | 3.0 s (software; hardware decode slower, 69 s) | Seen |
| Change detection (numpy, 96×54 thumbnails) | 0.2 s, kept 75 of 600 frames, cursor ignored | Seen |
| 19 contact sheets (2×2, 1572×894, stamped) | 0.7 s, 3.3 MB | Seen |
| Apple on-device speech-to-text | 17 s of speech → text in 0.96 s, no download | Seen |
| Whole digest | 4.1 s | Seen |
| Claude reading 19 sheets + transcript | est. 35–45 k tokens, 30–60 s | Inferred |

Gemini AI Studio's speed is 1 fps sampling with cheap frames, not a faster model. We sample at 1 fps and additionally drop every second where the screen did not change, so a 10-minute screen demo becomes ~20 images. What we lose: motion between samples (irrelevant for screen work).

Recording alternatives if ffmpeg screen capture fails on macOS 27: `screencapture -v` (writes .mov; digest reads it directly). Screen Studio / Loom / QuickTime are not needed; their recordings can still be dropped into `screencast digest`.
