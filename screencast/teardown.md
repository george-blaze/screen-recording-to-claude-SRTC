# Teardown: VoiceOS hold-to-record + Claude desktop delivery (REA report, 2026-11-05)

Targets inspected (read-only, nothing copied into the build):
- `/Applications/VoiceOS.app` v0.1.40 (WakoAI, Electron; main process obfuscated)
- `/Applications/Claude.app` (Electron; main process `.vite/build/index.chunk-Du-Wqi0E.js`, minified, not obfuscated)

Tags: **Seen** = read in the shipped files or observed running. **Inferred** = educated guess from Seen facts. **Unknown** = not established.

## A. How VoiceOS does "hold two keys → record → text appears"

| # | Finding | Tag | Evidence |
|---|---|---|---|
| A1 | Key detection is a native Rust helper `Resources/binaries/keyboard-listener`, driven over stdin/stdout with JSON commands `register_hotkeys` (HotkeyCombo of keys), `get_modifier_state`, `set_recording_block_keys`, `fn_passthrough`, `shift_enter_passthrough`; it emits `keydown` / `keyup` events. Hold-vs-release is decided in the Electron process from those events. | Seen | strings in binary; `src/modifier_state.rs`; error enums `EventTapError`, `KeyHookError` (CGEventTap under the hood) |
| A2 | While recording, VoiceOS **blocks keys** (`set_recording_block_keys`), which is why you cannot scroll, click or type while holding. It is a design choice, not a macOS limit. | Seen (command exists) / Inferred (that it is the cause of your symptom) | same binary |
| A3 | Audio is captured by a separate native helper `microphone-capture` (3.6 MB, likely bundles a VAD/ASR front-end). Transcription itself happens in the Electron process or a cloud call; main.js is obfuscated so the path is not recovered. | Seen (helper) / Unknown (where ASR runs) | binary present; obfuscated main.js |
| A4 | Screen context: VoiceOS takes **screenshots** (log strings `[Screenshot] desktopCapturer warmed up`, `[ScreenCapture] Captured`), not video, via Electron's desktopCapturer; it also reads the focused element / window text via `focused-element-reader`, `window-text-reader`, `app-observer` (Accessibility API, `AXUIElement`). | Seen | strings in main.js and helpers |
| A5 | Text delivery is `text-injector`: writes the text to `NSPasteboard` as a **concealed, transient** item (so clipboard managers ignore it), simulates **⌘V** with CGEvents, then verifies the paste (`pasteConfirmed=`). | Seen | strings `org.nspasteboard.ConcealedType`, `TransientType`, `Failed to verify clipboard content after concealed write`, `main thread did not register paste in time` |
| A6 | Its "Claude Code" integration (`bundled-integrations/claude-code/server.ts`, a local MCP server run with bundled bun) does **not** put anything into the Claude desktop chat. It runs Claude Code headless: `claude -p --input-format stream-json --output-format stream-json --verbose`, with `--session-id`, `--resume`, `--fork-session`, `--permission-prompts none`, shows progress in its notch, and "open session" = Terminal running `claude --resume <id>` via osascript. | Seen | server.ts lines 1525, 3529, 4831-4844, 6627, 7091 |
| A7 | VoiceOS depends on a user-installed `claude` CLI for A6; none is on this Mac (`which claude` empty). | Seen | shell check |

## B. How to get a request into the Claude desktop app

| # | Finding | Tag | Evidence |
|---|---|---|---|
| B1 | Claude.app registers the `claude://` URL scheme at launch and handles it in an `open-url` handler (`claudeURLHandler`). | Seen | Info.plist `CFBundleURLSchemes`; main chunk |
| B2 | **`claude://code/new?q=<text>&folder=<abs path>&file=<abs path>`** opens a new Code session: `q` (alias `prompt`, length-capped) is the request, `folder` (repeatable) sets the project, `file` (repeatable) attaches files; navigates the renderer to `/new?q=…&folder=…&src=external`. | Seen | handler: `s=(a.searchParams.get("q")??a.searchParams.get("prompt"))?.slice(0,wN), c=getAll("folder"), l=getAll("file")` |
| B3 | `claude://code/continue?session=last` or `session=local_<id>` reopens a session; **no prompt parameter** on this route. `claude://code/needs-input`, `claude://code/search` also exist. | Seen | handler; `pTr=/^local_[A-Za-z0-9-]{1,64}$/` |
| B4 | The app's own menu uses these links ("New Claude Code Session", "Continue Last Claude Code Session"), so they are a maintained interface, not a leftover. | Seen | desktop_action URLs in the same chunk |
| B5 | Live test: `open "claude://code/new?folder=…/Video to Claude&q=…test…"` ran at 22:28:42; no new session was created in the session list afterwards. | Seen | list_sessions |
| B6 | Therefore `q` most likely **prefills the composer** of a new-session screen and waits for you to press Enter (sessions are created on send). Needs one look at the screen to confirm. | Inferred → confirm with user | B5 |
| B7 | Claude.app runs Claude Code through `@anthropic-ai/claude-agent-sdk` inside a Linux VM (`claude-code-vm/2.1.317/claude` is an aarch64 ELF). There is no native CLI to call from a script; "append a turn to the open chat from outside" is not available. | Seen | `file` output; SDK string in main chunk |
| B8 | Fallback that needs no app internals: VoiceOS-style paste injection (B-A5) into the focused composer, or a Claude Code chat that watches the recordings folder. | Inferred (mechanism proven by A5) | — |

## C. What this means for the build
- Hold-to-record is a bad fit for screen demos (A2). Use a **toggle** hotkey; you keep full use of mouse and keyboard while recording.
- Delivery = **deep link B2**: on stop, open `claude://code/new?folder=<project>&q=<short instruction>&file=<DIGEST.md>&file=<sheet_001.jpg>…`. The transcript lives in DIGEST.md, so the `q` length cap is irrelevant.
- Everything else (recording, digest, transcription) is already measured, see BLUEPRINT.md appendix.

## D. Unknowns that remain
- U1 whether `q` auto-sends or prefills (B6) — one visual check.
- U2 the `wN` prompt cap value — irrelevant if the prompt stays short.
- U3 whether `file` accepts images or only text — test at build time; fallback is listing the paths in `q`.
- U4 ffmpeg screen capture on macOS 27 — first run will show (Screen Recording permission prompt).
- U5 Apple on-device ASR accuracy on your real voice vs. the synthetic sample ("off-service" for "auth service" was seen).

## E. Legal flags
- Nothing from VoiceOS or Claude.app is copied; only interfaces were learned. The `claude://` scheme is Anthropic's app surface, used as the app's own menu uses it. If it changes in an update, the paste-injection fallback (B8) still works.

## F. The native recorder in Claude.app ("Share screen" / "Record a skill", codename CopperHeron) — added 22:45

| # | Finding | Tag | Evidence |
|---|---|---|---|
| F1 | The app has one built-in recorder with two purposes: `skill` ("Record a skill") and `context` ("Share screen"). Chooser text for context: screen, clicks, typing and voice are recorded; voice is transcribed as you speak; you review and trim in the composer before it is sent. | Seen | chunk BXe2V_x3, `WatchRecordPurpose={Skill,Context}` |
| F2 | What it captures: a frame every 250 ms (buffer 720 frames, then thinned and interval doubled), up to 30 click screenshots, key events, live narration transcript. Hard stop at **10 minutes** (`bt=6e5`, warning at 9 min, "max duration reached, auto-delivering"). | Seen | constants gt/_t/St/bt |
| F3 | Delivery: on Done you trim ranges (≤64) in the composer; the kept frames, a `watch-record-events` JSON block and the transcript are attached as content blocks to the **next message in the current session** (`trailingContentBlocks`, hash-verified). This is "directly in my chat window", natively. | Seen | `finalizeCopperHeron`, `LocalAgentModeSessions.sendMessage` |
| F4 | It is started only from the Code-tab composer UI (served from claude.ai) via IPC `startWatchRecording(sessionId, resumeRecord, purpose)`. No `claude://` route starts it. | Seen | all claude:// routes enumerated; IPC method list |
| F5 | Global shortcuts: the app registers QUICK_ENTRY (default Alt+Space, yours unset) and DICTATION (yours: Cmd+1, `quickEntryDictationShortcut`). A third slot HUSHED_HERON with preference `hushedHeronKey` exists in the schema but no handler registration was found; what it does is unknown. **No global hotkey starts Share screen.** | Seen / Unknown (HUSHED_HERON) | main chunk shortcut code; `claude_desktop_config.json` |
| F6 | Gated by feature flag `cowork_watch_record` / `watchRecordEnabled`; your app shows "Record a skill", so the flag is on. Whether "Share screen" is offered in your composer is unverified. | Inferred → one look | flag code; your report |
| F7 | Web UI entry points (cached claude.ai bundle): composer **+ → "Record your screen"** (purpose context) and **"Record a skill"** (purpose skill). Available only when `coworkWatchRecord.status==="supported"`, Computer Use opted in (`chicagoEnabled`, yours true), not restricted; targets are `cowork_session`, `cowork_new`, `hub_chat`, `hub_new` — Cowork and Chat, **not the Code tab**. Start statuses: needs-screen-recording, needs-accessibility, needs-cu-opt-in, remote-session-unsupported. | Seen | Cache_Data be2997d445a8e564_0: `addSkillLabel`, `menuLabel`, `startWatchRecording(v,n,t==="context"?Context:undefined)` |
| F8 | **Why "Record your screen" is missing for you:** context mode = `coworkCopperHeron` = `watchRecordEnabled && X("2661075028")`, a server-side rollout gate. Your log shows `watchRecordEnabled:true` (skill mode on) but the + menu offers only "Record a skill", so gate 2661075028 is off for your account. No local preference controls it; the dev toggle `isChicagoEnabled` forces only `watchRecordEnabled`. | Seen (code + your menu) | main chunk `dZr()`; main.log chicago_config |
