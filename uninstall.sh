#!/bin/bash
for L in com.srtc.hotkey com.videotoclaude.hotkey; do launchctl bootout "gui/$(id -u)/$L" 2>/dev/null || true; rm -f "$HOME/Library/LaunchAgents/$L.plist"; done
[ -L "$HOME/.claude/skills/srtc" ] && rm "$HOME/.claude/skills/srtc"
echo "✔ agent and skill link removed. Delete this folder, ~/.config/vtc.json and the 'Screen recordings as requests'"
echo "  section in ~/.claude/CLAUDE.md to remove everything. Recordings live in ~/Movies/SRTC."
