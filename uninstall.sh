#!/bin/bash
launchctl bootout "gui/$(id -u)/com.videotoclaude.hotkey" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/com.videotoclaude.hotkey.plist"
[ -L "$HOME/.claude/skills/video-to-claude" ] && rm "$HOME/.claude/skills/video-to-claude"
echo "✔ agent and skill link removed. Delete this folder, ~/.config/vtc.json and the 'Screen recordings as requests'"
echo "  section in ~/.claude/CLAUDE.md to remove everything. Recordings live in ~/Movies/Video to Claude."
