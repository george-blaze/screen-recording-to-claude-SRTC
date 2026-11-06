---
name: video-to-claude
description: Hotkey screen+voice recorder whose takes land in the open Claude chat as the prompt (narration = request, screenshots + pointer zooms = context). Use when the user mentions Video to Claude, vtc, the ⌃⌘S recorder, wants an existing screen recording (.mov/.mp4) digested into sheets + transcript, wants to change the hotkey/mic/cap/retention, clean recordings, or when the hotkey or audio stops working.
user-invocable: true
argument-hint: "[status | digest <video> | clean | usage | install | help]"
allowed-tools: Bash, Read
---

# Video to Claude

`SKILL_DIR` is the directory containing this SKILL.md (normally `~/.claude/skills/video-to-claude`). Every command
below runs from there. The tool is `python3 "$SKILL_DIR/vtc.py"`; the digest engine is `python3 "$SKILL_DIR/screencast/screencast.py"`.

## What it does
- `⌃⌘S` toggles a screen + microphone recording (menu-bar agent `vtc-hotkey`). `Esc` cancels. 10-minute cap with a
  20-second countdown sound + red panel.
- On stop: 1 fps sampling → unchanged seconds dropped → 2×2 timestamped sheets; mouse log → zoomed "pointer sheets"
  for circled / rested-on / clicked spots; Whisper (local, streaming while recording) → timestamped transcript.
- Delivery: brings Claude to the front and pastes images then the prompt into the open chat (user presses Enter).
- A rule in `~/.claude/CLAUDE.md` (installed from `claude-rule.md`) makes the narration the request.

## Commands
| Ask | Run |
|---|---|
| status / is it recording | `python3 "$SKILL_DIR/vtc.py" status` |
| digest an existing recording (any .mov/.mp4) | `python3 "$SKILL_DIR/screencast/screencast.py" digest "<file>"` → then `Read` the printed `DIGEST.md`, then each sheet in `sheets/` and `pointer/` |
| storage usage | `python3 "$SKILL_DIR/vtc.py" usage` |
| clean old takes / clean everything | `python3 "$SKILL_DIR/vtc.py" clean` / `python3 "$SKILL_DIR/vtc.py" clean --all` |
| change hotkey / mic / cap / retention | edit `~/.config/vtc.json` (keys: `hotkey`, `mic`, `cap_seconds`, `warn_seconds`, `keep_days`, `deliver`, `auto_send`, `asr`), then `launchctl kickstart -k gui/$(id -u)/com.videotoclaude.hotkey` |
| install / reinstall | `"$SKILL_DIR/install.sh"` (the user must run it: it registers a login agent and a keychain certificate) |

When the user asks a question about a recording they just made, the digest folder is `recordings/<date>.digest/`
next to the video (default `~/Movies/Video to Claude/`, or `recordings` in `~/.config/vtc.json`): read `DIGEST.md`
first, then the sheets it lists.

## Troubleshooting (check in this order)
1. **Hotkey dead, menu bar says "hotkey blocked: password field"** → some app holds secure keyboard input (a focused
   password field, often a browser). Click out of it. `ioreg -l -w 0 | grep -o '"kCGSSessionSecureInputPID"=[0-9]*'` names the pid.
2. **Hotkey dead, log says "not trusted"** (`~/Library/Application Support/VideoToClaude/hotkey.log`) → Accessibility
   grant lost (happens after rebuilding without `sign.sh`). System Settings → Privacy & Security → Accessibility:
   remove `vtc-hotkey` (−), add it again (+ or drag the binary in). The agent arms itself within 2 s.
3. **"Nothing was recorded"** → Screen Recording or Microphone grant missing for `vtc-hotkey`; Screen Recording only
   applies after `launchctl kickstart -k gui/$(id -u)/com.videotoclaude.hotkey`.
4. **Transcript empty or garbage** → check the digest's "audio peak / average" line; below −40 dB average means the
   wrong mic. `mic` in `~/.config/vtc.json` is a priority list; `python3 "$SKILL_DIR/screencast/screencast.py" devices` lists devices.
5. **Transcript OK but late** → Whisper model missing (`models/whisper-large-v3-turbo/weights.safetensors`); see README.
Logs: `~/Library/Application Support/VideoToClaude/{hotkey,runner,asr,recorder-ffmpeg}.log`.

Never run `install.sh`, `sign.sh` or `launchctl bootstrap` yourself; show the command and let the user run it.
