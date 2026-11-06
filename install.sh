#!/bin/bash
# SRTC (Screen Recording To Claude) — one-shot installer. Run it yourself (it registers a login agent, a signing certificate, and edits ~/.claude/CLAUDE.md).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

say "1/7 Checking prerequisites"
for t in ffmpeg ffprobe python3 swiftc uv; do command -v "$t" >/dev/null || { echo "missing: $t  (see README → Prerequisites)"; exit 1; }; done
python3 -c "import numpy" 2>/dev/null || { echo "missing: numpy  →  python3 -m pip install numpy"; exit 1; }
echo "ok"

say "2/7 Building helpers"
[ -x "$DIR/vtc-hotkey" ] || swiftc -O "$DIR/vtc-hotkey.swift" -o "$DIR/vtc-hotkey"
[ -x "$DIR/paste" ] || swiftc -O "$DIR/paste.swift" -o "$DIR/paste"
[ -x "$DIR/screencast/sheet" ] || swiftc -O "$DIR/screencast/sheet.swift" -o "$DIR/screencast/sheet"
[ -x "$DIR/screencast/transcribe" ] || swiftc -O "$DIR/screencast/transcribe.swift" -o "$DIR/screencast/transcribe"
mkdir -p "$DIR/sounds"
[ -f "$DIR/sounds/countdown.aiff" ] || ffmpeg -y -v error -stream_loop -1 -i /System/Library/Sounds/Sosumi.aiff -t 20 -ac 1 -ar 44100 -c:a pcm_s16be "$DIR/sounds/countdown.aiff"
echo "ok"

say "3/7 Signing helpers (stable identity → permissions survive rebuilds)"
"$DIR/sign.sh" | grep -E "✔" || true

say "4/7 Config"
mkdir -p "$HOME/.config" "$HOME/Movies/SRTC"
if [ ! -f "$HOME/.config/vtc.json" ]; then
  python3 - "$DIR" <<'PY'
import json, sys, os
d = sys.argv[1]
cfg = {"folder": os.path.expanduser("~"), "recordings": os.path.expanduser("~/Movies/SRTC"),
       "screen": 0, "mic": "default", "hotkey": "ctrl+cmd+s", "cap_seconds": 600, "warn_seconds": 20,
       "asr": "stream", "deliver": "paste", "auto_send": False, "keep_days": 7, "keep_last": 30}
json.dump(cfg, open(os.path.expanduser("~/.config/vtc.json"), "w"), indent=2)
print("wrote ~/.config/vtc.json")
PY
else echo "~/.config/vtc.json already exists, left as is"; fi

say "5/7 Claude rule (narration = the request)"
mkdir -p "$HOME/.claude"
if ! grep -q "Screen recordings as requests" "$HOME/.claude/CLAUDE.md" 2>/dev/null; then
  { [ -f "$HOME/.claude/CLAUDE.md" ] && echo; cat "$DIR/claude-rule.md"; } >> "$HOME/.claude/CLAUDE.md"; echo "appended to ~/.claude/CLAUDE.md"
else echo "already present"; fi
if [ "$DIR" != "$HOME/.claude/skills/srtc" ]; then
  mkdir -p "$HOME/.claude/skills"; ln -sfn "$DIR" "$HOME/.claude/skills/srtc"; echo "skill linked: ~/.claude/skills/srtc → $DIR"
fi

say "6/7 Login agent (menu bar + hotkey)"
PLIST="$HOME/Library/LaunchAgents/com.srtc.hotkey.plist"
# migrate from the pre-rename agent label
launchctl bootout "gui/$(id -u)/com.videotoclaude.hotkey" 2>/dev/null || true; rm -f "$HOME/Library/LaunchAgents/com.videotoclaude.hotkey.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Application Support/VideoToClaude"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.srtc.hotkey</string>
  <key>ProgramArguments</key><array><string>$DIR/vtc-hotkey</string><string>$DIR/vtc.py</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Application Support/VideoToClaude/hotkey.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Application Support/VideoToClaude/hotkey.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/com.srtc.hotkey" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "running"

say "7/7 Whisper model (local, ~1.6 GB, one time)"
M="$DIR/models/whisper-large-v3-turbo"; mkdir -p "$M"
if [ ! -s "$M/weights.safetensors" ]; then
  echo "downloading in the background → $M (transcription uses Apple's on-device engine until it finishes)"
  (curl -sL -o "$M/config.json" "https://huggingface.co/mlx-community/whisper-large-v3-turbo/resolve/main/config.json";
   nohup curl -L -C - --retry 30 --retry-delay 3 --retry-all-errors -o "$M/weights.safetensors" \
     "https://huggingface.co/mlx-community/whisper-large-v3-turbo/resolve/main/weights.safetensors" > "$M/curl.log" 2>&1 &)
else echo "present"; fi

cat <<EOT

✔ Installed. Now grant the permissions macOS asks for (System Settings → Privacy & Security), all for "vtc-hotkey":
    Accessibility        – the agent asks right away (hotkey + mouse tracking)
    Microphone           – the agent asks right away
    Screen Recording     – the agent asks right away; then run:  launchctl kickstart -k gui/\$(id -u)/com.srtc.hotkey
  Press ⌃⌘S to start a take, talk and point, ⌃⌘S to stop. Your open Claude chat fills with the request. Press Enter.
EOT
