# Settings reference: `~/.config/vtc.json`

Created by the installer; edit with any text editor (menu bar → "Settings (vtc.json)"). Values not present fall back
to the defaults below. Changes to `hotkey` need an agent restart (`launchctl kickstart -k gui/$(id -u)/com.srtc.hotkey`);
everything else is read at the start of each take.

| Key | Default | What it does |
|---|---|---|
| `hotkey` | `"ctrl+cmd+s"` | Toggle combination. Modifiers `ctrl`, `cmd`, `alt`, `shift` plus one key (letters, digits, `space`, `f1`–`f12`). |
| `mic` | `"auto"` | `"auto"`: a connected headphones/headset mic if there is one (names containing AirPods, Headset, Buds, Beats, Jabra, Bose, Sony WH/WF, …), otherwise the system input (System Settings → Sound → Input). `"default"`: always the system input. A device name uses that device. A list such as `["AirPods Pro", "MacBook Pro Microphone"]` uses the first one connected when the take starts. `"none"` records video only. The digest names the mic used and warns when the audio is muffled, clipping or very quiet (see [troubleshooting](troubleshooting.md#transcript-empty-or-garbage)). |
| `mic_mode` | `"voice"` | New. `"voice"`: the mic is captured by the `miccap` helper through Apple's voice-processing DSP (beamforming on the MacBook mic array, automatic gain, noise suppression; the path FaceTime, Slack and dictation apps use) and fed to ffmpeg as 48 kHz mono PCM. Voice processing only works on the system default input, so if the mic chosen by `mic` is not the default, `miccap` switches the default to it for the take and restores the previous one afterwards. It also engages the output device (echo-cancellation reference); other audio is not ducked. If `miccap` fails to start, the recorder falls back to raw and notifies. `"raw"`: the previous behaviour, the plain mic straight through ffmpeg (avfoundation). |
| `screen` | `0` | Which display to capture (`screencast.py devices` lists them). |
| `cap_seconds` | `600` | Hard stop; the take is delivered automatically when reached. |
| `warn_seconds` | `20` | Countdown length before the cap: sound, notification, red panel. |
| `warn_sound` | `""` | Path to any audio file for the countdown. Empty uses `sounds/countdown.aiff` (Sosumi looped for 20 s). |
| `fps` | `5` | Recording frame rate. 5 keeps files small; the digest samples at 1 fps anyway. |
| `width` | `1920` | Recording width in pixels (height follows the display's aspect ratio). |
| `asr_engine` | `"whisper"` | New. `"whisper"`: local mlx-whisper (asr_worker.py), no network. `"deepgram"`: Deepgram nova-3 cloud (what VoiceOS dictation uses; far more forgiving of distant or muffled audio). Needs `"deepgram_api_key": "..."` in this file (or `DEEPGRAM_API_KEY` in the agent's environment). Same 20-s chunk protocol, see `asr_deepgram.py`. |
| `asr` | `"stream"` | `stream`: Whisper transcribes 20-s chunks while you record. `mlx`: Whisper after the take. `auto`: Apple on-device engine (fast, weaker on accents). `none`. |
| `chunk_seconds` | `20` | Chunk length for streaming transcription. |
| `deliver` | `"paste"` | `paste`: into the Claude chat in front of you. `deeplink`: a new Code session with the files attached (`claude://code/new`). `clipboard`: prompt on the clipboard only. |
| `auto_send` | `false` | In paste mode, press Return after pasting. |
| `paste_max_images` | `10` | How many sheets to paste (pointer sheets first). The rest stay on disk and are listed in the prompt. |
| `folder` | your home | Project folder a deeplink-mode session opens in. |
| `grid` | `"2x2"` | Tiles per overview sheet. `1x1` for dense code, `3x3` for a fast skim. |
| `tile_width` | `780` | Pixel width of each tile. |
| `pointer_moments` | `24` | Maximum zoomed pointer shots per take. |
| `recordings` | `~/Movies/SRTC` | Where takes (`<date>.mp4`), digests (`<date>.digest/`) and mouse logs live. |
| `keep_days` | `7` | After each delivery, delete takes older than this (0 = keep forever). |
| `keep_last` | `30` | Never delete the newest N takes. |
| `max_prompt_chars` | `1200` | Transcript characters put in the prompt itself; the full transcript is always in `DIGEST.md`. |

## Pointer detection thresholds

In `vtc.py`, `detect_moments`: a **circle** is ≥300° of turning within 1.2 s inside a 30–500 px box; **pointed at**
is the cursor resting within 25 px for ≥0.75 s right after moving ≥120 px; every **click** counts. Moments closer than
1.5 s are merged (circle beats click beats dwell).

## Change detection

In `screencast/screencast.py`: the video is sampled at 1 fps and a frame is kept when ≥0.4 % of the pixels of a
96×54 thumbnail changed by more than 24 gray levels since the last kept frame, or every 20 s regardless. A moving
cursor is ~0.1 % and is ignored; a scroll, a tab switch or a new window always counts.
