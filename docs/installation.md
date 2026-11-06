# SRTC installation guide

SRTC (Screen Recording To Claude) installs in about five minutes. The only parts you have to do by hand are three
macOS permission prompts; everything else is one script.

## Requirements

| | Needed | Check |
|---|---|---|
| Mac | Apple silicon, macOS 15 or newer (built and tested on macOS 27 / M3 Max) | ` ▸ About This Mac` |
| Claude | the Claude desktop app, signed in, with Claude Code (the "Code" tab) | open it once |
| Xcode command line tools | `swiftc`, used once to compile three small helpers | `xcode-select --install` |
| Homebrew packages | `ffmpeg` (capture + digest), `uv` (runs the Whisper worker in an isolated Python) | `brew install ffmpeg uv` |
| Python | Homebrew `python3` with `numpy` | `python3 -m pip install numpy` |
| Disk | ~2 GB (1.6 GB Whisper model + recordings) | |

Nothing else is installed system-wide. SRTC lives entirely in its own folder plus `~/.config/vtc.json`, one login
agent, and a short rule appended to `~/.claude/CLAUDE.md`.

## Step 1: clone into your Claude skills folder

```bash
git clone https://github.com/george-blaze/screen-recording-to-claude-SRTC.git ~/.claude/skills/srtc
```

Cloning into `~/.claude/skills/` makes the repo double as a Claude Code skill (`/srtc …`). You can clone anywhere
else; the installer then links the skill for you.

## Step 2: run the installer

```bash
~/.claude/skills/srtc/install.sh
```

What it does, in order (it prints each step):

1. **Checks prerequisites** and stops with the exact command to run if one is missing.
2. **Builds the helpers** with `swiftc`: `vtc-hotkey` (menu-bar agent), `paste` (delivers into the composer), `audio-input`
   (lists/sets the system input device), `miccap` (captures the mic through Apple's voice processing),
   `screencast/sheet` (contact sheets), `screencast/transcribe` (Apple on-device fallback). Also generates the
   20-second countdown sound from macOS's Sosumi alert.
3. **Signs the helpers** with a local certificate named "Video to Claude" that it creates in your login keychain.
   This gives the binaries a stable identity so macOS keeps the permission grants across rebuilds. If Keychain asks,
   click **Always Allow**.
4. **Writes `~/.config/vtc.json`** with defaults (never overwrites an existing one).
5. **Appends the rule** from `claude-rule.md` to `~/.claude/CLAUDE.md` so Claude treats the narration as the request,
   and links the skill if you cloned elsewhere.
6. **Registers the login agent** (`~/Library/LaunchAgents/com.srtc.hotkey.plist`) and starts it. From now on the
   menu-bar item is there whenever you're logged in.
7. **Starts the Whisper download** in the background (1.6 GB). Until it finishes, transcription uses Apple's on-device
   engine, which is fast but noticeably worse on accents.

## Step 3: grant the three permissions

The agent requests all three itself on first launch. They are all for **vtc-hotkey**, in System Settings → Privacy &
Security:

| Permission | Why | What you see |
|---|---|---|
| **Accessibility** | global hotkey and mouse tracking | a dialog "vtc-hotkey would like to control this computer" → Open System Settings → switch it on |
| **Microphone** | your voice | a plain Allow / Don't Allow prompt |
| **Screen & System Audio Recording** | the screen | a dialog that sends you to System Settings → switch it on |

Screen Recording is the one macOS applies only after the app restarts, so once it's on:

```bash
launchctl kickstart -k gui/$(id -u)/com.srtc.hotkey
```

If a prompt never appeared, open the matching list and add the binary with **+** (or drag it in from Finder):

```bash
open -R ~/.claude/skills/srtc/vtc-hotkey
```

## Step 4: verify

The menu-bar circle tells you the state: **orange "grant Accessibility"** until trusted, then **plain ○**. The log
says the same:

```bash
tail -3 ~/Library/Application\ Support/VideoToClaude/hotkey.log
```

Expect a line ending in `armed, hotkey = ctrl+cmd+s`. Now do a 10-second test: open any Claude chat, press ⌃⌘S, say
a sentence while moving the mouse over something, press ⌃⌘S. Within a few seconds Claude comes to the front and the
composer holds one or two images and a prompt starting with "Screen recording request". If the prompt says
"(no speech captured)" or shows an audio warning, read [troubleshooting](troubleshooting.md#transcript-empty-or-garbage).

## Step 5: Whisper model

The installer downloads `mlx-community/whisper-large-v3-turbo` into `models/whisper-large-v3-turbo/` in the
background. Check progress with:

```bash
ls -la ~/.claude/skills/srtc/models/whisper-large-v3-turbo/weights.safetensors    # complete at 1,613,977,612 bytes
```

If your connection to Hugging Face is slow or the download stalls, this resumes it:

```bash
cd ~/.claude/skills/srtc/models/whisper-large-v3-turbo && curl -L -C - -o weights.safetensors \
  https://huggingface.co/mlx-community/whisper-large-v3-turbo/resolve/main/weights.safetensors
```

Once the file is complete, every new take uses Whisper automatically (`"asr": "stream"` in the config).

## Step 6: make it yours

Open `~/.config/vtc.json` (menu bar → Settings) and set at least:

- `mic`: leave `"auto"` (connected headphones first, else the system input) or give a priority list of the mics you
  actually use, best first, e.g. `["AirPods Pro", "MacBook Pro Microphone"]`; the first one connected at record time wins.
  `python3 ~/.claude/skills/srtc/screencast/screencast.py devices` prints the exact names. Every take reports which mic
  it used and warns if it was muffled, clipping or too quiet.
- `mic_mode`: leave `"voice"` (default; mic through Apple's voice-processing DSP via `miccap`, falls back to raw if it
  can't start) unless you want the plain mic with `"raw"`.
- `hotkey` if ⌃⌘S collides with something (`ctrl+alt+r`, `cmd+shift+f6`, …).

All keys are described in the [settings reference](settings.md). Restart the agent after changing the hotkey:

```bash
launchctl kickstart -k gui/$(id -u)/com.srtc.hotkey
```

## Update

```bash
cd ~/.claude/skills/srtc && git pull && ./install.sh
```

The installer rebuilds only changed helpers and re-signs them, so no permission prompts come back. If you ever
rebuild a helper by hand, run `./sign.sh` afterwards for the same reason.

## Uninstall

```bash
~/.claude/skills/srtc/uninstall.sh
```

removes the login agent and the skill link. To remove everything else: delete the folder, `~/.config/vtc.json`, the
"Screen recordings as requests" section in `~/.claude/CLAUDE.md`, the recordings in `~/Movies/SRTC`, and the
"Video to Claude" certificate in Keychain Access. The permission entries for `vtc-hotkey` can be removed with **−** in
each Privacy & Security list.
