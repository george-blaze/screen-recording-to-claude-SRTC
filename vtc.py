#!/usr/bin/env python3
"""vtc — SRTC (Screen Recording To Claude). Toggle a screen+mic recording with one hotkey; when it stops,
the digest (timestamped sheets + on-device transcript) opens a new Claude Code session
with your spoken words as the request and the sheets attached.

  vtc.py toggle     start if idle, stop if recording   (bound to the hotkey)
  vtc.py start | stop | cancel | status
  vtc.py finish <video>   run digest + delivery on an existing recording

Config: ~/.config/vtc.json  (created with defaults on first run)
"""
import json, os, shutil, signal, subprocess, sys, time, urllib.parse
from pathlib import Path

# launchd gives the hotkey agent a bare PATH; make Homebrew tools (ffmpeg, ffprobe) visible
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "/usr/bin:/bin")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "screencast"))
STATE_DIR = Path.home() / "Library/Application Support/VideoToClaude"
STATE = STATE_DIR / "current.json"
CONFIG = Path.home() / ".config/vtc.json"
DEFAULTS = {
    "folder": str(Path.home()),               # project folder a deeplink-mode session opens in
    "recordings": str(Path.home() / "Movies" / "SRTC"),
    "screen": 0,
    "mic": "auto",                            # auto = headphones/headset if connected, else the system input; or a name, a priority list, or "none"
    "mic_mode": "voice",                      # voice = Apple voice-processing capture via miccap (beamforming, auto gain, noise suppression) | raw = plain mic
    "asr_engine": "auto",                     # auto = Deepgram nova-3 when deepgram_api_key / DEEPGRAM_API_KEY is set, else local Whisper | deepgram | whisper
    "deepgram_api_key": "",                   # https://console.deepgram.com → API keys (what VoiceOS uses under the hood)
    "cap_seconds": 600,                       # 10 min, same as Claude's native recorder
    "lead_seconds": 3,                        # the 3-2-1: the mic opens at once, the screen + chime follow after this (or once the mic delivers)
    "warn_seconds": 20,                       # sound + notification this long before the cap
    "fps": 5, "width": 1920,
    "grid": "2x2", "asr": "stream",                # stream (Whisper during recording) | auto (Apple, after) | mlx | none
    "chunk_seconds": 20,
    "language": "en",                         # Whisper language; "" = auto-detect per chunk (hallucinates foreign text on noise)
    "backend": "auto",                        # auto | ffmpeg | screencapture
    "deliver": "paste",                       # paste (into the chat in front of you) | deeplink (new session) | clipboard
    "auto_send": False,                       # paste mode: press Return after pasting
    "paste_max_images": 10,
    "prompt_style": "plain",                  # plain = just the words with [n] markers + a screenshot per pointer moment | full = digest-style prompt
    "max_prompt_chars": 1200,
    "hotkey": "ctrl+cmd+s",                   # read by vtc-hotkey at launch; restart the agent after changing
    "pointer_moments": 24,
    "warn_sound": "",                         # path to any audio file to use as the 20-s countdown; empty = sounds/countdown.aiff (the only place it plays)
    "start_sound": "",                        # played when the mic is live; empty = sounds/start.aiff
    "error_sound": "",                        # played on failures / attention notices; empty = sounds/error.aiff
    "keep_days": 7,                           # delete takes older than this after each delivery (0 = keep forever)
    "keep_last": 30,                          # always keep at least the newest N takes                    # max zoomed screenshots of what you pointed at / circled / clicked
}
SOUND_START = str(HERE / "sounds" / "start.aiff")       # rising three-note chime: the mic is live, talk
if not os.path.exists(SOUND_START): SOUND_START = "/System/Library/Sounds/Tink.aiff"
SOUND_ALERT = str(HERE / "sounds" / "error.aiff")       # low two-note buzz: something failed or needs attention
if not os.path.exists(SOUND_ALERT): SOUND_ALERT = "/System/Library/Sounds/Basso.aiff"
SOUND_WARN = str(HERE / "sounds" / "countdown.aiff")   # 20-s countdown: 1 beep/s, double-time last 5 s, long tone at 0
SOUND_DONE = "/System/Library/Sounds/Glass.aiff"


CFG_CACHE = {}


def cfg():
    global CFG_CACHE
    c = dict(DEFAULTS)
    if CONFIG.exists():
        try: c.update(json.loads(CONFIG.read_text()))
        except Exception as e: print(f"warning: bad {CONFIG}: {e}")
    else:
        CONFIG.parent.mkdir(parents=True, exist_ok=True); CONFIG.write_text(json.dumps(c, indent=2))
    CFG_CACHE = c
    return c


def notify(title, text="", sound=None):
    try:
        c = CFG_CACHE if CFG_CACHE else {}
        if sound == SOUND_START and c.get("start_sound"): sound = c["start_sound"]
        if sound == SOUND_ALERT and c.get("error_sound"): sound = c["error_sound"]
    except Exception: pass
    if sound and os.path.exists(sound):
        subprocess.Popen(["afplay", sound])
    subprocess.Popen(["osascript", "-e", f'display notification "{text}" with title "{title}"'])


def read_state():
    try: return json.loads(STATE.read_text())
    except Exception: return None


def alive(pid):
    if not pid: return False
    try: os.kill(pid, 0); return True
    except OSError: return False


# ------------------------------------------------------------------ record ---
MICCAP = HERE / "miccap"


def use_miccap(c):
    return c.get("mic_mode", "voice") == "voice" and MICCAP.exists()


def ffmpeg_cmd(c, out, piped_mic=False):
    """Screen (+ raw mic) recorder. With piped_mic the mic is captured separately (see audio_cmd) and muxed in afterwards:
    avfoundation and a pipe run on different clocks, and a muxer waiting for them to line up stalls for good."""
    from screencast import resolve_inputs
    spec, has_audio = resolve_inputs(c["screen"], c["mic"])
    vidx, aidx = spec.split(":")
    raw_mic = has_audio and not piped_mic
    # screen and mic as two separate inputs: sharing one avfoundation input drops ~75% of mic buffers
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-f", "avfoundation",
           "-capture_cursor", "1", "-capture_mouse_clicks", "1", "-framerate", "15", "-i", f"{vidx}:none"]
    if raw_mic:
        cmd += ["-f", "avfoundation", "-thread_queue_size", "4096", "-i", f":{aidx}", "-map", "0:v", "-map", "1:a",
                "-af", "aresample=async=1:first_pts=0", "-c:a", "aac", "-b:a", "96k"]
    cmd += ["-t", str(c["cap_seconds"]), "-vf", f"fps={c['fps']},scale={c['width']}:-2,format=yuv420p",
            "-c:v", "h264_videotoolbox", "-b:v", "4M", "-realtime", "1", "-movflags", "+faststart", str(out)]
    if raw_mic and c["asr"] == "stream":   # second output: 16 kHz mono wav chunks, transcribed while you talk
        chunks = out.with_suffix(".chunks"); chunks.mkdir(exist_ok=True)
        cmd += ["-map", "1:a", "-t", str(c["cap_seconds"]), "-af", CHUNK_FILTER, "-ac", "1", "-ar", "16000",
                "-f", "segment", "-segment_time", str(c["chunk_seconds"]), "-reset_timestamps", "1", str(chunks / "%04d.wav")]
    return cmd


CHUNK_FILTER = "aresample=async=1:first_pts=0,highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11"


def audio_cmd(c, out):
    """Mic-only ffmpeg fed by miccap on stdin: full-length wav (muxed into the mp4 at stop) + live 16 kHz chunks."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-f", "s16le", "-ar", "48000", "-ac", "1",
           "-thread_queue_size", "4096", "-i", "pipe:0",
           "-map", "0:a", "-t", str(c["cap_seconds"] + 15), "-c:a", "pcm_s16le", str(out.with_suffix(".wav"))]   # runner stops it 1 s after the screen
    if c["asr"] == "stream":
        chunks = out.with_suffix(".chunks"); chunks.mkdir(exist_ok=True)
        cmd += ["-map", "0:a", "-t", str(c["cap_seconds"] + 15), "-af", CHUNK_FILTER, "-ac", "1", "-ar", "16000",
                "-f", "segment", "-segment_time", str(c["chunk_seconds"]), "-reset_timestamps", "1", str(chunks / "%04d.wav")]
    return cmd


def media_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except ValueError: return None


def mux_audio(video, wav, tail=0.0):
    """Put the separately captured wav into the mp4 (video copied, audio → aac). The mic ran `tail` seconds past the end of
    the screen, so it started (video length - (audio length - tail)) seconds after the screen: the audio is shifted by that
    much so transcript times match the frame stamps. Returns the shift in seconds, or None on failure."""
    tmp = video.with_name(video.stem + ".mux.mp4")
    vd, ad = media_duration(video), media_duration(wav)
    delta = round(vd - (ad - tail), 3) if (vd and ad and -20 < vd - (ad - tail) < 8) else 0.0
    # delta > 0: mic started after the screen → delay the audio; delta < 0: mic was live before the first frame → trim its head
    ain = (["-itsoffset", str(delta)] if delta > 0 else ["-ss", str(-delta)] if delta < 0 else []) + ["-i", str(wav)]
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video)] + ain + [
                        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "96k",
                        "-af", "aresample=async=1:first_pts=0", "-movflags", "+faststart", str(tmp)], capture_output=True, text=True)
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1024:
        print(f"mux failed: {r.stderr.strip()[-200:]}", flush=True); tmp.unlink(missing_ok=True); return None
    tmp.replace(video); print(f"  mic audio muxed (mic started {delta:+.2f}s relative to the screen)", flush=True); return delta


def screencapture_cmd(c, out):
    # Apple's own recorder: always works where Screen Recording permission is granted; writes .mov
    cmd = ["screencapture", "-v", "-x", "-V", str(c["cap_seconds"])]
    if c["mic"] not in (None, "", "none"): cmd.append("-g")
    return cmd + [str(out.with_suffix(".mov"))]


def cmd_start(c):
    st = read_state()
    if st and (alive(st.get("recorder_pid", -1)) or (st.get("phase") == "starting" and alive(st.get("runner_pid")))):
        print("already recording"); return
    STATE_DIR.mkdir(parents=True, exist_ok=True); Path(c["recordings"]).mkdir(parents=True, exist_ok=True)
    out = Path(c["recordings"]) / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.mp4"
    # detached runner: survives the hotkey process, waits for the recorder, then digests + delivers
    runner = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_run", str(out)],
                              stdout=open(STATE_DIR / "runner.log", "a"), stderr=subprocess.STDOUT,
                              start_new_session=True)
    # provisional state at once, so a second hotkey press cannot start a second runner while this one opens the mic
    STATE.write_text(json.dumps({"runner_pid": runner.pid, "recorder_pid": None, "phase": "starting", "file": str(out), "started": time.time()}))
    print(f"● recording → {out}  (runner pid {runner.pid})")


def cmd_run(c, out):
    """The detached runner."""
    out = Path(out); backend = c["backend"]
    import screencast as _sc
    mic_p = aud_p = None
    def stop_mic():
        nonlocal mic_p, aud_p
        for q, sig in ((aud_p, signal.SIGINT), (mic_p, signal.SIGTERM)):      # ffmpeg finalizes the wav; miccap restores the default input
            if q is not None and q.poll() is None:
                try: q.send_signal(sig); q.wait(timeout=5)
                except Exception:
                    try: q.kill()
                    except Exception: pass
        mic_p = aud_p = None
    def launch(b, voice=True, lead=True):
        """Mic first, then the screen. Returns the screen recorder, or None when the voice-processed mic delivered nothing
        (the caller falls back to the plain mic). With `lead` the screen waits for the agent's 3-2-1 (lead_seconds)."""
        nonlocal mic_p, aud_p
        stop_mic()
        t_launch = time.time()
        piped = b == "ffmpeg" and voice and use_miccap(c) and c["mic"] not in (None, "", "none")
        cmd = ffmpeg_cmd(c, out, piped_mic=piped) if b == "ffmpeg" else screencapture_cmd(c, out)
        base = {"runner_pid": os.getpid(), "backend": b, "mic": _sc.LAST_MIC_NAME, "mic_mode": "voice" if piped else "raw",
                "file": str(out if b == "ffmpeg" else out.with_suffix(".mov")), "started": t_launch}
        STATE.write_text(json.dumps(dict(base, phase="starting", recorder_pid=None, mic_pid=None, audio_pid=None)))
        if piped:
            # mic at once, during the 3-2-1: AirPods switch to their hands-free profile when the mic opens and can take
            # several seconds to deliver; the screen and the chime wait until audio is flowing so the first words are kept.
            mic_p = subprocess.Popen([str(MICCAP), "--device", _sc.LAST_MIC_NAME], stdout=subprocess.PIPE, stderr=open(STATE_DIR / "miccap.log", "w"))
            aud_p = subprocess.Popen(audio_cmd(c, out), stdin=mic_p.stdout, stdout=subprocess.DEVNULL, stderr=open(STATE_DIR / "recorder-audio.log", "w"))
            mic_p.stdout.close()                 # the audio ffmpeg owns the read end; miccap sees EPIPE when it stops
            STATE.write_text(json.dumps(dict(base, phase="starting", recorder_pid=None, mic_pid=mic_p.pid, audio_pid=aud_p.pid)))
            if not (first_bytes(aud_p, out.with_suffix(".wav"), 8) and mic_p.poll() is None and aud_p.poll() is None): return None
        if lead: time.sleep(max(0.0, c.get("lead_seconds", 3) - (time.time() - t_launch)))
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=open(STATE_DIR / f"recorder-{b}.log", "w"))
        STATE.write_text(json.dumps(dict(base, phase="recording", recorder_pid=p.pid, mic_pid=mic_p.pid if mic_p else None,
                                         audio_pid=aud_p.pid if aud_p else None, started=time.time())))
        return p
    def kill_rec(p):
        try: p.send_signal(signal.SIGINT); p.wait(timeout=3)
        except Exception:
            try: p.kill(); p.wait(timeout=2)
            except Exception: pass
    def first_bytes(p, f, secs):
        for _ in range(int(secs * 2)):
            time.sleep(0.5)
            if p.poll() is not None: return False
            if f.exists() and f.stat().st_size >= 1024: return True
        return False
    p = launch("ffmpeg" if backend in ("auto", "ffmpeg") else "screencapture")
    if p is None:
        # the voice-processed mic delivered nothing in 8 s: record with the plain mic
        err = (STATE_DIR / "miccap.log").read_text().strip().splitlines() if (STATE_DIR / "miccap.log").exists() else []
        why = next((l for l in reversed(err) if "miccap: " not in l or "failed" in l or "missing" in l), "no audio arrived")
        out.with_suffix(".wav").unlink(missing_ok=True); shutil.rmtree(out.with_suffix(".chunks"), ignore_errors=True)
        print(f"miccap failed ({why}) → raw mic", flush=True)
        notify("SRTC", f"Voice-processed mic failed ({why[:60]}); recording with the plain mic.", SOUND_ALERT)
        p = launch("ffmpeg", voice=False, lead=False)
    t0 = time.time()
    notify("SRTC", "Recording. Press the hotkey again to stop.", SOUND_START)   # the chime: the mic is live, the screen is rolling
    if backend == "auto" and not first_bytes(p, out, 12):   # up to 12 s for the first bytes (USB mics can take a few seconds to open)
        err = (STATE_DIR / "recorder-ffmpeg.log").read_text().strip().splitlines()
        last = err[-1] if err else "no frames arrived (Screen Recording / Microphone permission for vtc-hotkey?)"
        kill_rec(p)
        print(f"ffmpeg produced no data in 12 s: {last} → falling back to screencapture", flush=True)
        notify("SRTC", f"ffmpeg failed: {last[:90]} — trying Apple recorder", SOUND_ALERT)
        p = launch("screencapture", lead=False); t0 = time.time()
    warned = False; warn_proc = None
    stream = StreamASR(out.with_suffix(".chunks"), c) if (c["asr"] == "stream" and (read_state() or {}).get("backend") == "ffmpeg") else None
    while p.poll() is None:
        el = time.time() - t0
        if not warned and el >= c["cap_seconds"] - c["warn_seconds"]:
            warned = True
            warn_proc = subprocess.Popen(["afplay", c.get("warn_sound") or SOUND_WARN])
            notify("SRTC", f"{c['warn_seconds']} seconds left")
        if stream:
            try: stream.poll()
            except Exception as e:
                print(f"live transcription stopped: {e}", flush=True); notify("SRTC", "Live transcription stopped; the take is still recording."); stream = None
        time.sleep(0.2)
    t_end = time.time()
    if warn_proc is not None and warn_proc.poll() is None: warn_proc.kill()   # stopped early: silence the countdown
    piped_wav = out.with_suffix(".wav") if aud_p is not None else None
    tail = 0.0
    if aud_p is not None and aud_p.poll() is None:
        time.sleep(1.0)                                   # people press stop while finishing the sentence
        tail = time.time() - t_end
    stop_mic()
    st = read_state() or {}
    st["phase"] = "processing"; STATE.write_text(json.dumps(st))
    video = Path(st.get("file", out))
    if not video.exists() or video.stat().st_size < 1024:
        notify("SRTC", "Nothing was recorded: grant Screen Recording + Microphone to vtc-hotkey (System Settings → Privacy & Security).", SOUND_ALERT)
        shutil.rmtree(out.with_suffix(".chunks"), ignore_errors=True)
        if piped_wav: piped_wav.unlink(missing_ok=True)
        STATE.unlink(missing_ok=True); return
    if piped_wav is not None:
        delta = mux_audio(video, piped_wav, tail) if piped_wav.exists() and piped_wav.stat().st_size > 48000 else None
        if delta is None: notify("SRTC", "No microphone audio was captured for this take.", SOUND_ALERT)
        else:
            piped_wav.unlink(missing_ok=True)
            if stream: stream.offset = delta          # chunk times are mic times; shift them onto the screen clock
    ev = STATE_DIR / "events.jsonl"
    if ev.exists():
        ev.replace(video.with_suffix(".events.jsonl"))
    vd = media_duration(video)
    if vd: st["video_start"] = t_end - vd; STATE.write_text(json.dumps(st))   # first frame's wall-clock time: pointer events line up with it
    # the agent's screenshots of other displays (taken while the cursor was there) + the first frame's wall-clock time,
    # in a sidecar of their own: the agent may still hold the events file open for a moment, so nothing is appended there
    shots = STATE_DIR / "shots"; sdir = video.with_name(video.stem + ".shots")
    if shots.exists(): shutil.rmtree(sdir, ignore_errors=True); shots.replace(sdir)
    video.with_name(video.stem + ".videos.json").write_text(json.dumps({"video_start": st.get("video_start"), "shots": str(sdir) if sdir.exists() else None}))
    notify("SRTC", "Processing…")
    try:
        try: transcript = stream.finish() if stream else None
        except Exception as e: print(f"transcript failed: {e}", flush=True); transcript = None
        finish(c, video, transcript=transcript, mic=st.get('mic'), mic_mode=st.get("mic_mode"), asr_quality=getattr(stream, "quality", None),
               segments=getattr(stream, "segments", None))
        prune(c)
    finally:
        STATE.unlink(missing_ok=True)


def cmd_stop(c):
    st = read_state()
    if st and st.get("phase") == "starting" and alive(st.get("runner_pid")): print("still starting (mic warming up)"); return
    if not st or not alive(st.get("recorder_pid", -1)):
        print("not recording"); STATE.unlink(missing_ok=True); return
    os.kill(st["recorder_pid"], signal.SIGINT)   # ffmpeg / screencapture finalize the file on SIGINT; the runner stops the mic 1 s later
    print("■ stopping; the runner will digest and deliver")


def cmd_cancel(c):
    st = read_state()
    if not st: print("not recording"); return
    for k in ("recorder_pid", "audio_pid", "runner_pid"):
        if alive(st.get(k) or -1):
            try: os.kill(st[k], signal.SIGKILL)
            except OSError: pass
    if alive(st.get("mic_pid") or -1):
        try: os.kill(st["mic_pid"], signal.SIGTERM)          # lets miccap restore the previous default input
        except OSError: pass
    f = Path(st.get("file", "")); f.unlink(missing_ok=True); f.with_suffix(".wav").unlink(missing_ok=True)
    shutil.rmtree(STATE_DIR / "shots", ignore_errors=True)
    shutil.rmtree(f.with_suffix(".chunks"), ignore_errors=True); STATE.unlink(missing_ok=True)
    notify("SRTC", "Recording cancelled"); print("cancelled")


def cmd_mictest(c):
    """Record 5 s through the configured mic (voice-processed when mic_mode=voice), analyse it, notify the verdict."""
    import screencast as _sc
    if read_state() and alive((read_state() or {}).get("recorder_pid", -1)): print("recording in progress"); return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _sc.resolve_inputs(c["screen"], c["mic"]); mic = _sc.LAST_MIC_NAME
    if not mic: print("mic is 'none'"); return
    wav = STATE_DIR / "mictest.wav"; wav.unlink(missing_ok=True)
    notify("SRTC", f"Mic test: talk normally for 5 seconds ({mic})", SOUND_START)
    if use_miccap(c):
        mp = subprocess.Popen([str(MICCAP), "--device", mic, "--seconds", "5"], stdout=subprocess.PIPE, stderr=open(STATE_DIR / "miccap.log", "w"))
        ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "s16le", "-ar", "48000", "-ac", "1", "-i", "pipe:0", "-ac", "1", "-ar", "16000", str(wav)],
                              stdin=mp.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        mp.stdout.close(); mode = "voice"
        try: ff.wait(timeout=20); mp.wait(timeout=5)
        except subprocess.TimeoutExpired:
            for q in (ff, mp):
                try: q.kill(); q.wait(timeout=2)
                except Exception: pass
    else:
        spec, _ = _sc.resolve_inputs(c["screen"], c["mic"]); aidx = spec.split(":")[1]
        try: subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "avfoundation", "-i", f":{aidx}", "-t", "5", "-ac", "1", "-ar", "16000", str(wav)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=20)
        except subprocess.TimeoutExpired: pass
        mode = "raw"
    if not wav.exists() or wav.stat().st_size < 32000:
        err = (STATE_DIR / "miccap.log").read_text().strip().splitlines() if (STATE_DIR / "miccap.log").exists() else []
        msg = f"Mic test failed: nothing captured from {mic}" + (f" ({err[-1][:70]})" if err else "")
        notify("SRTC", msg, SOUND_ALERT); print(msg); sys.exit(1)
    pk = _sc.measure_peak_db(wav); st = _sc.LAST_AUDIO; note = _sc.quiet_audio_note(pk, mic)
    clarity = f"{st['hf'] * 100:.0f}%" if st.get("hf") is not None else "n/a"
    if st.get("voiced", 0) == 0: verdict = f"Mic test: heard nothing on {mic} (talk during the 5 s, or pick another mic)"
    elif note: verdict = f"Mic test: {note.split(':')[0]} on {mic} — clarity {clarity}, level {st['mean']:.0f} dB"
    else: verdict = f"Mic test OK on {mic}: clarity {clarity}, level {st['mean']:.0f} dB ({mode})"
    print(verdict); notify("SRTC", verdict, SOUND_DONE if not note and st.get("voiced") else SOUND_ALERT)


def cmd_toggle(c):
    st = read_state()
    if st and (alive(st.get("recorder_pid", -1)) or (st.get("phase") == "starting" and alive(st.get("runner_pid")))): cmd_stop(c)
    else: cmd_start(c)


def cmd_status(c):
    st = read_state()
    if st and alive(st.get("recorder_pid", -1)):
        print(f"recording {st['file']} for {int(time.time() - st['started'])}s ({st['backend']})")
    else: print("idle")


# ------------------------------------------------------------- housekeeping ---
def takes(c):
    """All takes in the recordings folder, newest first: (stem, [paths])."""
    root = Path(c["recordings"]); groups = {}
    for p in root.iterdir():
        stem = p.name.split(".")[0]
        if stem[:4].isdigit(): groups.setdefault(stem, []).append(p)
    return sorted(groups.items(), key=lambda kv: kv[0], reverse=True)


def prune(c, days=None, keep_last=None, everything=False):
    days = c["keep_days"] if days is None else days
    keep_last = c["keep_last"] if keep_last is None else keep_last
    if not everything and days <= 0: return 0
    cutoff = time.time() - days * 86400; freed = 0; n = 0
    for i, (stem, paths) in enumerate(takes(c)):
        newest = max(p.stat().st_mtime for p in paths)
        if everything or (i >= keep_last and newest < cutoff):
            for p in paths:
                freed += sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
            n += 1
    if n: print(f"removed {n} take(s), freed {freed / 1e6:.0f} MB")
    return n


def cmd_clean(c):
    import argparse
    ap2 = argparse.ArgumentParser(prog="vtc.py clean")
    ap2.add_argument("--all", action="store_true", help="delete every recording")
    ap2.add_argument("--days", type=int, help="delete takes older than N days (default: keep_days from config)")
    a = ap2.parse_args(sys.argv[2:])
    n = prune(c, days=a.days, keep_last=0 if (a.all or a.days is not None) else None, everything=a.all)
    total = sum(f.stat().st_size for f in Path(c["recordings"]).rglob("*") if f.is_file())
    print(f"{'nothing to remove; ' if not n else ''}recordings folder now {total / 1e6:.0f} MB, {len(takes(c))} take(s)")


def cmd_usage(c):
    total = sum(f.stat().st_size for f in Path(c["recordings"]).rglob("*") if f.is_file())
    print(f"{len(takes(c))} take(s), {total / 1e6:.0f} MB in {c['recordings']}")


# ----------------------------------------------------------------- deliver ---
def transcript_segments(transcript):
    """'[mm:ss] text' lines → segments without word timing (Apple/mlx-after-the-take modes, or an old transcript.txt)."""
    import re
    segs = []
    for l in (transcript or "").splitlines():
        m = re.match(r"\[(\d+):(\d+)\]\s*(.*)", l.strip())
        if m: segs.append({"start": int(m[1]) * 60 + int(m[2]), "end": None, "text": m[3].strip(), "words": []})
    for a, b in zip(segs, segs[1:]): a["end"] = b["start"]
    if segs: segs[-1]["end"] = segs[-1]["start"] + 5
    return segs


def words_with_markers(segments, moments):
    """The spoken text in order, with [n] right after the word being said when pointer moment n happened."""
    import bisect
    toks = []                                   # [text, start, end]; word ends are clamped (an engine can stretch one word over a silence)
    for sg in segments:
        ws = sg.get("words") or []
        if ws:
            for w in ws: toks.append([w["w"], w["s"], min(w["e"], w["s"] + 2.0)])
        elif sg["text"]: toks.append([sg["text"], sg["start"], sg["end"] if sg["end"] is not None else sg["start"]])   # no word timing: a marker lands after the sentence in progress
    toks.sort(key=lambda t: t[1])
    starts = [t[1] for t in toks]
    marks = {}                                  # token index (after which) → [n…]; -1 = before everything
    for n, m in enumerate(moments, 1):
        idx = bisect.bisect_right(starts, m["t"]) - 1        # the word being said = the last one that had started
        marks.setdefault(idx, []).append(n)
    out = []; prev_end = None
    if -1 in marks: out.append(" ".join(f"[{n}]" for n in marks[-1]))
    for i, (txt, st, en) in enumerate(toks):
        if out and prev_end is not None and st - prev_end >= 3.0: out[-1] += "\n"   # a pause of 3 s = a new paragraph
        out.append(txt)
        if i in marks: out.append(" ".join(f"[{n}]" for n in marks[i]))
        prev_end = en if prev_end is None else max(prev_end, en)
    text = ""
    for piece in out:
        if not text or text.endswith("\n"): text += piece
        else: text += " " + piece
    return text.strip()


def finish(c, video, transcript=None, mic=None, mic_mode=None, asr_quality=None, segments=None):
    import argparse
    from screencast import digest
    asr = {"stream": "mlx", "auto": "auto", "mlx": "mlx", "apple": "apple", "none": "none"}[c["asr"]]
    if transcript is not None: asr = "none"
    a = argparse.Namespace(grid=c["grid"], tile_width=780, fps=1.0, max_gap=20, change=0.004, pixel_delta=24,
                           jpeg_q=3, asr=asr, keep_raw=False)
    workdir = video.with_name(video.stem + ".digest"); workdir.mkdir(parents=True, exist_ok=True)
    if transcript is not None: (workdir / "transcript.txt").write_text(transcript)
    if segments is not None: (workdir / "transcript.json").write_text(json.dumps(segments))
    elif (workdir / "transcript.json").exists():
        try: segments = json.loads((workdir / "transcript.json").read_text())
        except Exception: segments = None
    import screencast as _sc
    _sc.LAST_MIC_NAME = mic or _sc.LAST_MIC_NAME
    _sc.MIC_MODE = mic_mode or c.get("mic_mode", "voice"); _sc.ASR_QUALITY = asr_quality
    digest(video, workdir, a)
    transcript = (workdir / "transcript.txt").read_text().strip() if (workdir / "transcript.txt").exists() else ""
    sheets = sorted((workdir / "sheets").glob("sheet_*.jpg"))
    pointer_sheets, moments = pointer_moments(c, video, workdir)
    if moments:
        md = workdir / "DIGEST.md"; txt = md.read_text()
        lines = ["", "## Pointed at (zoomed, red box = cursor)", ""]
        for i, m in enumerate(moments):
            lines.append(f"- {hms(m['t'])} {m['kind']}  → " + (f"[{i + 1}] `{m['shot']}`" if "shot" in m else f"pointer sheet {i // 4 + 1}, tile {i % 4 + 1}"))
        if not all("shot" in m for m in moments): lines += ["", "Pointer sheets: " + ", ".join(f"`{p}`" for p in pointer_sheets), ""]
        md.write_text(txt.replace("## Transcript", "\n".join(lines) + "\n## Transcript", 1))
    spoken = " ".join(l.split("] ", 1)[-1] for l in transcript.splitlines() if l.strip())
    short = spoken[: c["max_prompt_chars"]] + ("…" if len(spoken) > c["max_prompt_chars"] else "")
    warn = (workdir / "audio_warning.txt").read_text().strip() if (workdir / "audio_warning.txt").exists() else ""
    if warn and not os.environ.get("VTC_DELIVER"):
        notify("SRTC", warn.split(":")[0].capitalize() + " — see DIGEST.md", SOUND_ALERT)   # one Sosumi, never the countdown
    plain = c.get("prompt_style", "plain") == "plain"
    if plain:
        segs = segments if segments else transcript_segments(transcript)
        prompt = words_with_markers(segs, moments) if segs else "(no speech captured)"
        if moments and not prompt.strip("[]0123456789 \n"): prompt = "(no speech captured) " + prompt
    warn_line = f"⚠ {warn}\n" if warn else ""
    if not plain: prompt = (f"Screen recording request (SRTC). My spoken words are the request:\n\"{short or '(no speech captured)'}\"\n"
              f"{warn_line}"
              f"Attached: DIGEST.md (full timestamped transcript + sheet index), {len(pointer_sheets)} pointer sheets (zoomed views of what I pointed at, circled or clicked, red box = cursor) and {len(sheets)} sheets of what was on my screen "
              f"while I spoke; each tile is stamped mm:ss and lines up with the transcript. Do what I asked; use the frames to "
              f"resolve 'this', 'here', 'that'. If the request is unclear, ask one question."
              + (f" (mic: {mic})" if mic else ""))
    subprocess.run(["pbcopy"], input=prompt, text=True)
    (workdir / "prompt.txt").write_text(prompt)
    if c["deliver"] == "paste":
        from screencast import tool
        images = pointer_sheets if plain else (pointer_sheets + sheets)[: c["paste_max_images"]]
        body = prompt if plain else (prompt + f"\n\nFiles on disk if you need more: DIGEST.md at {workdir / 'DIGEST.md'} "
                f"(full transcript + index), all sheets in {workdir / 'sheets'} and {workdir / 'pointer'}.")
        tf = workdir / "prompt.txt"; tf.write_text(body)
        cmd = [str(HERE / "paste"), str(tf)] + (["--send"] if c["auto_send"] else []) + ["--files"] + [str(i) for i in images]
        r = subprocess.run(cmd, capture_output=True, text=True)
        print(f"paste: rc={r.returncode} {r.stdout.strip()} {r.stderr.strip()}")
        if r.returncode:
            notify("SRTC", "Claude isn't in front; prompt is on the clipboard — paste it.", SOUND_DONE)
        else:
            notify("SRTC", f"Dropped into your open chat: {len(images)} images, {len(spoken.split())} words." + ("" if c["auto_send"] else " Press Enter."), SOUND_DONE)
    elif c["deliver"] == "deeplink":
        q = urllib.parse.urlencode({"folder": c["folder"], "q": prompt, "source": "video_to_claude"})
        files = [workdir / "DIGEST.md"] + pointer_sheets[:12] + sheets[:40]
        q += "".join("&file=" + urllib.parse.quote(str(f), safe="") for f in files)
        subprocess.run(["open", f"claude://code/new?{q}"])
        notify("SRTC", f"Sent to Claude: {len(sheets)} sheets, {len(spoken.split())} words. Prompt also on clipboard.", SOUND_DONE)
    else:
        notify("SRTC", "Prompt copied. Paste it into Claude and attach the digest folder.", SOUND_DONE)
    print(f"delivered: {workdir}")


# ------------------------------------------------------- streaming Whisper ---
class StreamASR:
    """Feeds finished wav chunks to a persistent Whisper worker while recording; returns the full transcript at stop."""
    def __init__(self, chunks, c):
        self.chunks, self.c, self.done, self.results = Path(chunks), c, set(), {}
        env = dict(os.environ, VTC_WHISPER_LANGUAGE=c.get("language") or "")
        key = (c.get("deepgram_api_key") or os.environ.get("DEEPGRAM_API_KEY") or "").strip()
        eng = c.get("asr_engine", "auto")
        self.engine = "deepgram" if (eng == "deepgram" or (eng == "auto" and key)) and (HERE / "asr_deepgram.py").exists() else "whisper"
        if key: env["DEEPGRAM_API_KEY"] = key
        self.env = env
        self.worker = self._spawn(self.engine)
        self.backup = None                      # local Whisper, started only if Deepgram fails on a chunk (no network, bad key…)
        self.pending = 0
        print(f"  asr: {self.engine}", flush=True)
        self.voiced_chunks = 0; self.thin_run = 0; self.muffled_warned = False
    WORKERS = {"deepgram": ["uv", "run", "-q", "python", "asr_deepgram.py"],
               "whisper": ["uv", "run", "-q", "--with", "mlx-whisper", "python", "asr_worker.py"]}

    def _spawn(self, engine):
        cmd = list(self.WORKERS[engine]); cmd[-1] = str(HERE / cmd[-1])
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(STATE_DIR / "asr.log", "a"), text=True, env=self.env)

    def _send(self, worker, path):
        worker.stdin.write(str(path) + "\n"); worker.stdin.flush()

    def _finished_chunks(self, final=False):
        files = sorted(self.chunks.glob("*.wav"))
        return files if final else files[:-1]            # the newest chunk is still being written
    SILENCE_RMS_DB = -50.0          # chunks quieter than this hold no speech: skip (Whisper hallucinates "Thank you." on silence)
    HALLUCINATIONS = {"thank you.", "thank you", "thanks for watching.", "thanks for watching", "you", "bye.", "."}

    @staticmethod
    def plausible(sg):
        """Drop what Whisper produces on noise: low confidence, 'no speech', or a looping word."""
        import re
        t = sg.get("text", "").strip()
        if not t: return False
        if sg.get("nsp", 0) > 0.6 or sg.get("lp", 0) < -1.0 or sg.get("cr", 0) > 2.4: return False
        if re.search(r"\b(\w+)(?:\W+\1\b){3,}", t, re.I): return False       # "can can can can"
        return True

    # normalized chunks: clear speech 0.12-0.25, a far/turned-away mic 0.02-0.05. Voice processing (noise suppression)
    # trims the top end even on clear speech (built-in mic: ~0.06 with a perfect transcript), so its bar is lower.
    MUFFLED_HF = {"raw": (0.05, 0.07), "voice": (0.03, 0.045)}   # (first speech chunk, two in a row)

    @staticmethod
    def chunk_hf(path):
        """Share of speech energy above 1 kHz in a chunk, or None (no speech)."""
        import numpy as np
        try:
            x = np.fromfile(path, dtype=np.int16, offset=44).astype(np.float32) / 32768.0
            n = 16000; win = np.hanning(n); fr = np.fft.rfftfreq(n, 1 / n); hi = tot = 0.0
            for i in range(0, x.size - n + 1, n):
                seg = x[i:i + n]
                if np.sqrt(np.mean(seg * seg)) < 0.005: continue
                f = np.abs(np.fft.rfft(seg * win)) ** 2
                hi += float(f[fr >= 1000].sum()); tot += float(f[fr >= 80].sum())
            return (hi / tot) if tot else None
        except Exception:
            return None

    def live_check(self, f):
        """While recording: warn once, early, when the mic sounds muffled (far away / turned away / covered)."""
        if self.muffled_warned: return
        hf = self.chunk_hf(f)
        if hf is None: return
        self.voiced_chunks += 1
        st = read_state() or {}
        first, run = self.MUFFLED_HF.get(st.get("mic_mode") or "voice", self.MUFFLED_HF["voice"])
        self.thin_run = self.thin_run + 1 if hf < run else 0
        if (self.voiced_chunks == 1 and hf < first) or self.thin_run >= 2:
            self.muffled_warned = True
            mic = st.get("mic") or "the mic"
            print(f"  ⚠ live: muffled audio (clarity {hf * 100:.0f}%) on {mic}", flush=True)
            notify("SRTC", f"Mic sounds muffled on {mic}: sit closer / face it, or put AirPods in. Transcript will suffer.", SOUND_ALERT)

    @staticmethod
    def chunk_rms_db(path):
        import numpy as np
        try:
            raw = np.fromfile(path, dtype=np.int16, offset=44)   # 16-bit mono PCM wav from ffmpeg
            if raw.size < 1600: return -120.0
            x = raw.astype(np.float32) / 32768.0
            return float(20 * np.log10(max(np.sqrt(np.mean(x * x)), 1e-9)))
        except Exception:
            return 0.0                                            # unknown → transcribe anyway

    def poll(self, final=False):
        for f in self._finished_chunks(final):
            if f not in self.done:
                self.done.add(f)
                if self.chunk_rms_db(f) < self.SILENCE_RMS_DB:
                    self.results[f] = []; continue
                self.live_check(f)
                self._send(self.worker, f); self.pending += 1
        self._drain(block=False)
    def _drain(self, block):
        import select
        while self.pending:
            outs = [w.stdout for w in (self.worker, self.backup) if w is not None and w.poll() is None]
            if not outs: return
            ready, _, _ = select.select(outs, [], [], None if block else 0)
            if not ready: return
            line = ready[0].readline()
            if not line:
                if self.engine == "deepgram" and self.worker.poll() is not None and self.backup is None:
                    print("  asr: deepgram worker died → local Whisper", flush=True); self.backup = self._spawn("whisper")
                    for f in self.done:
                        if f not in self.results: self._send(self.backup, f)
                    continue
                return
            try: j = json.loads(line)
            except Exception: continue
            if "ready" in j:
                if j["ready"] is False and self.engine == "deepgram" and self.backup is None:
                    print("  asr: deepgram unavailable → local Whisper", flush=True); self.backup = self._spawn("whisper")
                    for f in self.done:
                        if f not in self.results: self._send(self.backup, f)
                continue
            if j.get("error") and "path" in j and self.engine == "deepgram" and ready[0] is self.worker.stdout:
                print(f"  asr: deepgram failed on {Path(j['path']).name}: {j['error'][:80]} → local Whisper", flush=True)
                if self.backup is None: self.backup = self._spawn("whisper")
                self._send(self.backup, j["path"]); continue
            self.results[Path(j["path"])] = j.get("segments", []); self.pending -= 1
    def finish(self):
        time.sleep(0.3); self.poll(final=True); self._drain(block=True)
        for w in (self.worker, self.backup):
            if w is None: continue
            try: w.stdin.close(); w.wait(timeout=5)
            except Exception: w.kill()
        lines = []; words = 0; lps = []; self.segments = []
        for i, f in enumerate(sorted(self.chunks.glob("*.wav"))):
            base = i * self.c["chunk_seconds"] + getattr(self, "offset", 0.0)
            segs = [sg for sg in self.results.get(f, []) if self.plausible(sg)]
            if len(segs) == 1 and segs[0]["text"].strip().lower() in self.HALLUCINATIONS: segs = []
            texts = [sg["text"].strip().lower() for sg in segs]
            if len(segs) >= 3 and len(set(texts)) <= max(1, len(texts) // 3): segs = []   # "I", "I", "I", … across a chunk
            segs = [sg for sg in segs if len(sg["text"].strip().strip(".,!? ")) > 2 or len(segs) == 1 and len(texts[0]) > 2]
            for sgm in segs:
                t = max(0.0, base + sgm["start"]); lines.append(f"[{int(t) // 60:02d}:{int(t) % 60:02d}] {sgm['text']}")
                words += len(sgm["text"].split()); lps.append(sgm.get("lp", -1.0))
                self.segments.append({"start": t, "end": max(0.0, base + sgm["end"]), "text": sgm["text"].strip(),
                                      "words": [{"w": w["w"], "s": max(0.0, base + w["s"]), "e": max(0.0, base + w["e"])} for w in sgm.get("words", [])]})
        self.quality = {"words": words, "lp": (sum(lps) / len(lps)) if lps else None}
        shutil.rmtree(self.chunks, ignore_errors=True)
        return "\n".join(lines)


# ---------------------------------------------------------- pointer moments ---
def hms(sec):
    sec = int(round(sec)); return f"{sec // 60:02d}:{sec % 60:02d}"


def detect_moments(events, start_epoch, max_n):
    """events: dicts with k,x,y,t (epoch, screen points, origin bottom-left). Returns moments [{t, x, y, kind}] (t relative s)."""
    import math
    moves = [e for e in events if e["k"] == "move"]
    moments = []
    for e in events:
        if e["k"] == "down": moments.append({"t": e["t"] - start_epoch, "x": e["x"], "y": e["y"], "kind": "clicked"})
    # circling: in a 1.2 s window the heading turns ≥ 300° within a 30–500 px box
    # dwell: ≥ 0.8 s inside a 25 px radius right after ≥ 120 px of movement
    i = 0; last_mark = -10
    for j in range(len(moves)):
        while moves[j]["t"] - moves[i]["t"] > 1.2: i += 1
        win = moves[i:j + 1]
        if len(win) < 8 or moves[j]["t"] - start_epoch - last_mark < 2.0: continue
        turn = 0.0; prev = None
        for a, b in zip(win, win[1:]):
            dx, dy = b["x"] - a["x"], b["y"] - a["y"]
            if dx * dx + dy * dy < 9: continue
            h = math.atan2(dy, dx)
            if prev is not None:
                d = h - prev
                while d > math.pi: d -= 2 * math.pi
                while d < -math.pi: d += 2 * math.pi
                turn += d
            prev = h
        xs = [p["x"] for p in win]; ys = [p["y"] for p in win]
        diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        if abs(turn) >= math.radians(300) and 30 <= diag <= 500:
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            moments.append({"t": moves[j]["t"] - start_epoch, "x": cx, "y": cy, "kind": "circled"}); last_mark = moves[j]["t"] - start_epoch
            continue
        d_i = 0
        while d_i < j and moves[j]["t"] - moves[d_i]["t"] > 0.8: d_i += 1
        dw = moves[d_i:j + 1]
        if len(dw) >= 3 and moves[j]["t"] - dw[0]["t"] >= 0.75:
            cx, cy = sum(p["x"] for p in dw) / len(dw), sum(p["y"] for p in dw) / len(dw)
            if all(math.hypot(p["x"] - cx, p["y"] - cy) <= 25 for p in dw):
                before = [p for p in moves[max(0, d_i - 60):d_i] if dw[0]["t"] - p["t"] <= 2.0]
                if before and max(math.hypot(p["x"] - cx, p["y"] - cy) for p in before) >= 120:
                    moments.append({"t": moves[j]["t"] - start_epoch, "x": cx, "y": cy, "kind": "pointed at"}); last_mark = moves[j]["t"] - start_epoch
    moments = [m for m in moments if m["t"] >= 0]          # the mouse log can start before the first frame (3-2-1)
    moments.sort(key=lambda m: m["t"])
    out = []
    for m in moments:                      # merge moments closer than 1.5 s, prefer circled > clicked > pointed
        if out and m["t"] - out[-1]["t"] < 1.5:
            rank = {"circled": 2, "clicked": 1, "pointed at": 0}
            if rank[m["kind"]] > rank[out[-1]["kind"]]: out[-1] = m
        else: out.append(m)
    if len(out) > max_n:                   # keep an even spread
        step = len(out) / max_n; out = [out[int(k * step)] for k in range(max_n)]
    return out


def pointer_moments(c, video, workdir):
    """Screenshots for each pointer moment (full frame, cursor boxed red, one per [n]) → paths. Returns (paths, moments).
    A moment on a display other than the recorded one comes from that display's low-rate video (<take>.dN.mp4)."""
    ev = Path(video).with_suffix(".events.jsonl")
    if not ev.exists(): return [], []
    events, screen = [], None
    for line in ev.read_text().splitlines():
        try: e = json.loads(line)
        except Exception: continue
        if e.get("k") == "screen": screen = e
        else: events.append(e)
    if not screen or not events: return [], []
    st = read_state() or {}
    vj = Path(video).with_name(Path(video).stem + ".videos.json")
    try: videos = json.loads(vj.read_text()) if vj.exists() else {}
    except Exception: videos = {}
    start_epoch = videos.get("video_start") or st.get("video_start") or screen["t"]
    def probe(path):
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True)
        try: return tuple(int(x) for x in r.stdout.strip().split(","))
        except Exception: return None
    dims = probe(video)
    if not dims: return [], []
    screens = screen.get("screens") or [{"x": 0, "y": 0, "w": screen["w"], "h": screen["h"]}]
    main = int(c.get("screen", 0)) if int(c.get("screen", 0)) < len(screens) else 0
    sources = {main: {"file": str(video), "start": start_epoch, "dims": dims, "scr": screens[main]}}
    shots = {}                                  # display index → sorted [(epoch, path)] of the agent's screenshots of it
    sdir = Path(videos["shots"]) if videos.get("shots") else None
    for f in (sorted(sdir.glob("d*_*.jpg")) if sdir and sdir.exists() else []):
        try: k, ep = f.stem[1:].split("_"); shots.setdefault(int(k), []).append((float(ep), f))
        except ValueError: continue
    for k in list(shots):
        if k < len(screens) and k != main: sources[k] = {"shots": sorted(shots[k]), "scr": screens[k]}
        else: del shots[k]
    def display_of(m):
        for k, sc in enumerate(screens):
            if sc["x"] <= m["x"] < sc["x"] + sc["w"] and sc["y"] <= m["y"] < sc["y"] + sc["h"]: return k
        return main
    moments = detect_moments(events, start_epoch, c["pointer_moments"])
    for m in moments: m["display"] = display_of(m)
    missing = sorted({m["display"] for m in moments if m["display"] not in sources})
    if missing: print(f"  {sum(m['display'] in missing for m in moments)} pointer moment(s) on display {missing} skipped: no video of it", flush=True)
    moments = [m for m in moments if m["display"] in sources]
    if not moments: return [], []
    def frame(m):
        """(source, seek args, cursor x, cursor y, width, height) for a moment, or None: the recorded display's video at that
        time, else the agent's screenshot of the other display nearest to it (within 1.5 s). Screen points (origin
        bottom-left) → image pixels."""
        import bisect
        src = sources[m["display"]]; sc = src["scr"]
        if "shots" in src:
            eps = [e for e, _ in src["shots"]]; ep = m["t"] + start_epoch
            i = bisect.bisect_left(eps, ep); cands = [j for j in (i - 1, i) if 0 <= j < len(eps)]
            if not cands: return None
            j = min(cands, key=lambda j: abs(eps[j] - ep))
            if abs(eps[j] - ep) > 1.5: return None
            path = src["shots"][j][1]; d = probe(path)
            if not d: return None
            vw, vh = d; seek = []
        else:
            vw, vh = src["dims"]; seek = ["-ss", f"{max(m['t'] + start_epoch - src['start'] - 0.1, 0.0):.2f}"]; path = src["file"]
        px = (m["x"] - sc["x"]) / sc["w"] * vw; py = (sc["h"] - (m["y"] - sc["y"])) / sc["h"] * vh
        return str(path), seek, px, py, vw, vh
    pdir = workdir / "pointer"; pdir.mkdir(exist_ok=True)
    if c.get("prompt_style", "plain") == "plain":
        # one full-frame screenshot per moment (cursor boxed in red), numbered in spoken order: these are the [n] in the prompt
        keep = moments
        if len(keep) > c["paste_max_images"]:
            step = len(keep) / c["paste_max_images"]; keep = [keep[int(k * step)] for k in range(c["paste_max_images"])]
        shots = []
        for i, m in enumerate(keep):
            fr = frame(m)
            if fr is None: continue
            src, seek, px, py, vw, vh = fr
            out = pdir / f"shot_{len(shots) + 1:02d}.jpg"
            r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + seek + ["-i", src,
                                "-frames:v", "1", "-vf", f"drawbox=x={int(px) - 30}:y={int(py) - 30}:w=60:h=60:color=red@0.9:t=5,scale=1600:-2",
                                "-q:v", "3", str(out)], capture_output=True, text=True)
            if r.returncode == 0 and out.exists(): shots.append(out); m["shot"] = str(out)
        return shots, [m for m in keep if "shot" in m]
    rows = []
    for i, m in enumerate(moments):
        fr = frame(m)
        if fr is None: continue
        src, seek, px, py, vw, vh = fr
        crop_w, crop_h = min(960, vw), min(600, vh)
        x0 = int(min(max(px - crop_w / 2, 0), vw - crop_w)); y0 = int(min(max(py - crop_h / 2, 0), vh - crop_h))
        bx, by = int(px - x0), int(py - y0)
        out = pdir / f"p{i:02d}.jpg"
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + seek + ["-i", src,
                            "-frames:v", "1", "-vf", f"crop={crop_w}:{crop_h}:{x0}:{y0},drawbox=x={bx - 28}:y={by - 28}:w=56:h=56:color=red@0.9:t=4,scale=780:-2",
                            "-q:v", "3", str(out)], capture_output=True, text=True)
        if r.returncode == 0 and out.exists():
            rows.append((out, f"{hms(m['t'])} {m['kind']}"))
    if not rows: return [], []
    listfile = pdir / "list.txt"; listfile.write_text("".join(f"{p}\t{l}\n" for p, l in rows))
    from screencast import tool
    subprocess.run([tool("sheet"), str(listfile), "2", "2", str(pdir), "0.85"], capture_output=True, text=True)
    return sorted(pdir.glob("sheet_*.jpg")), moments[:len(rows)]


# -------------------------------------------------------------------- main ---
if __name__ == "__main__":
    c = cfg()
    if os.environ.get("VTC_DELIVER"): c["deliver"] = os.environ["VTC_DELIVER"]
    cmd = sys.argv[1] if len(sys.argv) > 1 else "toggle"
    if cmd == "_run": cmd_run(c, sys.argv[2])
    elif cmd == "finish": finish(c, Path(sys.argv[2]).expanduser().resolve())
    else: {"toggle": cmd_toggle, "start": cmd_start, "stop": cmd_stop, "cancel": cmd_cancel, "status": cmd_status, "clean": cmd_clean, "usage": cmd_usage, "mictest": cmd_mictest}[cmd](c)
