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
    "mic": "default",                         # system default input (AirPods when connected); or a name from: python3 screencast/screencast.py devices
    "cap_seconds": 600,                       # 10 min, same as Claude's native recorder
    "warn_seconds": 20,                       # sound + notification this long before the cap
    "fps": 5, "width": 1920,
    "grid": "2x2", "asr": "stream",                # stream (Whisper during recording) | auto (Apple, after) | mlx | none
    "chunk_seconds": 20,
    "language": "en",                         # Whisper language; "" = auto-detect per chunk (hallucinates foreign text on noise)
    "backend": "auto",                        # auto | ffmpeg | screencapture
    "deliver": "paste",                       # paste (into the chat in front of you) | deeplink (new session) | clipboard
    "auto_send": False,                       # paste mode: press Return after pasting
    "paste_max_images": 10,
    "max_prompt_chars": 1200,
    "hotkey": "ctrl+cmd+s",                   # read by vtc-hotkey at launch; restart the agent after changing
    "pointer_moments": 24,
    "warn_sound": "",                         # path to any audio file to use as the countdown; empty = sounds/countdown.aiff
    "keep_days": 7,                           # delete takes older than this after each delivery (0 = keep forever)
    "keep_last": 30,                          # always keep at least the newest N takes                    # max zoomed screenshots of what you pointed at / circled / clicked
}
SOUND_START = "/System/Library/Sounds/Tink.aiff"
SOUND_ALERT = "/System/Library/Sounds/Sosumi.aiff"
SOUND_WARN = str(HERE / "sounds" / "countdown.aiff")   # 20-s countdown: 1 beep/s, double-time last 5 s, long tone at 0
SOUND_DONE = "/System/Library/Sounds/Glass.aiff"


def cfg():
    c = dict(DEFAULTS)
    if CONFIG.exists():
        try: c.update(json.loads(CONFIG.read_text()))
        except Exception as e: print(f"warning: bad {CONFIG}: {e}")
    else:
        CONFIG.parent.mkdir(parents=True, exist_ok=True); CONFIG.write_text(json.dumps(c, indent=2))
    return c


def notify(title, text="", sound=None):
    if sound and os.path.exists(sound):
        subprocess.Popen(["afplay", sound])
    subprocess.Popen(["osascript", "-e", f'display notification "{text}" with title "{title}"'])


def read_state():
    try: return json.loads(STATE.read_text())
    except Exception: return None


def alive(pid):
    try: os.kill(pid, 0); return True
    except OSError: return False


# ------------------------------------------------------------------ record ---
def ffmpeg_cmd(c, out):
    from screencast import resolve_inputs
    spec, has_audio = resolve_inputs(c["screen"], c["mic"])
    vidx, aidx = spec.split(":")
    # screen and mic as two separate inputs: sharing one avfoundation input drops ~75% of mic buffers
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-f", "avfoundation",
           "-capture_cursor", "1", "-capture_mouse_clicks", "1", "-framerate", "15", "-i", f"{vidx}:none"]
    if has_audio:
        cmd += ["-f", "avfoundation", "-thread_queue_size", "4096", "-i", f":{aidx}", "-map", "0:v", "-map", "1:a",
                "-af", "aresample=async=1:first_pts=0", "-c:a", "aac", "-b:a", "96k"]
    cmd += ["-t", str(c["cap_seconds"]), "-vf", f"fps={c['fps']},scale={c['width']}:-2,format=yuv420p",
            "-c:v", "h264_videotoolbox", "-b:v", "4M", "-realtime", "1", "-movflags", "+faststart", str(out)]
    if has_audio and c["asr"] == "stream":   # second output: 16 kHz mono wav chunks, transcribed while you talk
        chunks = out.with_suffix(".chunks"); chunks.mkdir(exist_ok=True)
        cmd += ["-map", "1:a", "-af", "aresample=async=1:first_pts=0,highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11", "-ac", "1", "-ar", "16000",
                "-f", "segment", "-segment_time", str(c["chunk_seconds"]), "-reset_timestamps", "1", str(chunks / "%04d.wav")]
    return cmd


def screencapture_cmd(c, out):
    # Apple's own recorder: always works where Screen Recording permission is granted; writes .mov
    cmd = ["screencapture", "-v", "-x", "-V", str(c["cap_seconds"])]
    if c["mic"] not in (None, "", "none"): cmd.append("-g")
    return cmd + [str(out.with_suffix(".mov"))]


def cmd_start(c):
    st = read_state()
    if st and alive(st.get("recorder_pid", -1)):
        print("already recording"); return
    STATE_DIR.mkdir(parents=True, exist_ok=True); Path(c["recordings"]).mkdir(parents=True, exist_ok=True)
    out = Path(c["recordings"]) / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.mp4"
    # detached runner: survives the hotkey process, waits for the recorder, then digests + delivers
    runner = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_run", str(out)],
                              stdout=open(STATE_DIR / "runner.log", "a"), stderr=subprocess.STDOUT,
                              start_new_session=True)
    print(f"● recording → {out}  (runner pid {runner.pid})")


def cmd_run(c, out):
    """The detached runner."""
    out = Path(out); backend = c["backend"]
    def launch(b):
        cmd = ffmpeg_cmd(c, out) if b == "ffmpeg" else screencapture_cmd(c, out)
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=open(STATE_DIR / f"recorder-{b}.log", "w"))
        STATE.write_text(json.dumps({"runner_pid": os.getpid(), "recorder_pid": p.pid, "backend": b, "phase": "recording",
                                     "file": str(out if b == "ffmpeg" else out.with_suffix(".mov")), "started": time.time()}))
        return p
    t0 = time.time()
    p = launch("ffmpeg" if backend in ("auto", "ffmpeg") else "screencapture")
    notify("SRTC", "Recording. Press the hotkey again to stop.", SOUND_START)
    if backend == "auto":
        f = out; ok = False
        for _ in range(24):                       # up to 12 s for the first bytes (USB mics can take a few seconds to open)
            time.sleep(0.5)
            if p.poll() is not None: break
            if f.exists() and f.stat().st_size >= 1024: ok = True; break
        if not ok:
            err = (STATE_DIR / "recorder-ffmpeg.log").read_text().strip().splitlines()
            last = err[-1] if err else "no frames arrived (Screen Recording / Microphone permission for vtc-hotkey?)"
            try: p.send_signal(signal.SIGINT); p.wait(timeout=3)
            except Exception: p.kill()
            print(f"ffmpeg produced no data in 6 s: {last} → falling back to screencapture")
            notify("SRTC", f"ffmpeg failed: {last[:90]} — trying Apple recorder", "/System/Library/Sounds/Sosumi.aiff")
            p = launch("screencapture"); t0 = time.time()
    warned = False; warn_proc = None
    stream = StreamASR(out.with_suffix(".chunks"), c) if (c["asr"] == "stream" and (read_state() or {}).get("backend") == "ffmpeg") else None
    while p.poll() is None:
        el = time.time() - t0
        if not warned and el >= c["cap_seconds"] - c["warn_seconds"]:
            warned = True
            warn_proc = subprocess.Popen(["afplay", c.get("warn_sound") or SOUND_WARN])
            notify("SRTC", f"{c['warn_seconds']} seconds left")
        if stream: stream.poll()
        time.sleep(0.5)
    if warn_proc is not None and warn_proc.poll() is None: warn_proc.kill()   # stopped early: silence the countdown
    st = read_state() or {}
    st["phase"] = "processing"; STATE.write_text(json.dumps(st))
    video = Path(st.get("file", out))
    if not video.exists() or video.stat().st_size < 1024:
        notify("SRTC", "Nothing was recorded: grant Screen Recording + Microphone to vtc-hotkey (System Settings → Privacy & Security).", SOUND_ALERT)
        shutil.rmtree(out.with_suffix(".chunks"), ignore_errors=True)
        STATE.unlink(missing_ok=True); return
    ev = STATE_DIR / "events.jsonl"
    if ev.exists():
        ev.replace(video.with_suffix(".events.jsonl"))
    notify("SRTC", "Processing…")
    try:
        transcript = stream.finish() if stream else None
        finish(c, video, transcript=transcript)
        prune(c)
    finally:
        STATE.unlink(missing_ok=True)


def cmd_stop(c):
    st = read_state()
    if not st or not alive(st.get("recorder_pid", -1)):
        print("not recording"); STATE.unlink(missing_ok=True); return
    os.kill(st["recorder_pid"], signal.SIGINT)   # ffmpeg / screencapture finalize the file on SIGINT
    print("■ stopping; the runner will digest and deliver")


def cmd_cancel(c):
    st = read_state()
    if not st: print("not recording"); return
    for k in ("recorder_pid", "runner_pid"):
        if alive(st.get(k, -1)):
            try: os.kill(st[k], signal.SIGKILL)
            except OSError: pass
    Path(st.get("file", "")).unlink(missing_ok=True); STATE.unlink(missing_ok=True)
    notify("SRTC", "Recording cancelled"); print("cancelled")


def cmd_toggle(c):
    st = read_state()
    if st and alive(st.get("recorder_pid", -1)): cmd_stop(c)
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
def finish(c, video, transcript=None):
    import argparse
    from screencast import digest
    asr = {"stream": "mlx", "auto": "auto", "mlx": "mlx", "apple": "apple", "none": "none"}[c["asr"]]
    if transcript is not None: asr = "none"
    a = argparse.Namespace(grid=c["grid"], tile_width=780, fps=1.0, max_gap=20, change=0.004, pixel_delta=24,
                           jpeg_q=3, asr=asr, keep_raw=False)
    workdir = video.with_name(video.stem + ".digest"); workdir.mkdir(parents=True, exist_ok=True)
    if transcript is not None: (workdir / "transcript.txt").write_text(transcript)
    digest(video, workdir, a)
    transcript = (workdir / "transcript.txt").read_text().strip() if (workdir / "transcript.txt").exists() else ""
    sheets = sorted((workdir / "sheets").glob("sheet_*.jpg"))
    pointer_sheets, moments = pointer_moments(c, video, workdir)
    if moments:
        md = workdir / "DIGEST.md"; txt = md.read_text()
        lines = ["", "## Pointed at (zoomed, red box = cursor)", ""]
        for i, m in enumerate(moments):
            lines.append(f"- {hms(m['t'])} {m['kind']}  → pointer sheet {i // 4 + 1}, tile {i % 4 + 1}")
        lines += ["", "Pointer sheets: " + ", ".join(f"`{p}`" for p in pointer_sheets), ""]
        md.write_text(txt.replace("## Transcript", "\n".join(lines) + "\n## Transcript", 1))
    spoken = " ".join(l.split("] ", 1)[-1] for l in transcript.splitlines() if l.strip())
    short = spoken[: c["max_prompt_chars"]] + ("…" if len(spoken) > c["max_prompt_chars"] else "")
    warn = (workdir / "audio_warning.txt").read_text().strip() if (workdir / "audio_warning.txt").exists() else ""
    if warn:
        notify("SRTC", "Audio was very quiet — wrong microphone? Check mic in ~/.config/vtc.json", SOUND_WARN)
    warn_line = f"⚠ {warn}\n" if warn else ""
    prompt = (f"Screen recording request (SRTC). My spoken words are the request:\n\"{short or '(no speech captured)'}\"\n"
              f"{warn_line}"
              f"Attached: DIGEST.md (full timestamped transcript + sheet index), {len(pointer_sheets)} pointer sheets (zoomed views of what I pointed at, circled or clicked, red box = cursor) and {len(sheets)} sheets of what was on my screen "
              f"while I spoke; each tile is stamped mm:ss and lines up with the transcript. Do what I asked; use the frames to "
              f"resolve 'this', 'here', 'that'. If the request is unclear, ask one question.")
    subprocess.run(["pbcopy"], input=prompt, text=True)
    if c["deliver"] == "paste":
        from screencast import tool
        images = (pointer_sheets + sheets)[: c["paste_max_images"]]
        body = (prompt + f"\n\nFiles on disk if you need more: DIGEST.md at {workdir / 'DIGEST.md'} "
                f"(full transcript + index), all sheets in {workdir / 'sheets'} and {workdir / 'pointer'}.")
        tf = workdir / "prompt.txt"; tf.write_text(body)
        cmd = [str(HERE / "paste"), str(tf)] + (["--send"] if c["auto_send"] else []) + ["--files"] + [str(i) for i in images]
        r = subprocess.run(cmd, capture_output=True, text=True)
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
        self.worker = subprocess.Popen(["uv", "run", "-q", "--with", "mlx-whisper", "python", str(HERE / "asr_worker.py")],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(STATE_DIR / "asr.log", "a"), text=True, env=env)
        self.pending = 0
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
                self.worker.stdin.write(str(f) + "\n"); self.worker.stdin.flush(); self.pending += 1
        self._drain(block=False)
    def _drain(self, block):
        import select
        while self.pending:
            if not block and not select.select([self.worker.stdout], [], [], 0)[0]: return
            line = self.worker.stdout.readline()
            if not line: return
            try: j = json.loads(line)
            except Exception: continue
            if "ready" in j: continue
            self.results[Path(j["path"])] = j.get("segments", []); self.pending -= 1
    def finish(self):
        time.sleep(0.3); self.poll(final=True); self._drain(block=True)
        try: self.worker.stdin.close(); self.worker.wait(timeout=5)
        except Exception: self.worker.kill()
        lines = []
        for i, f in enumerate(sorted(self.chunks.glob("*.wav"))):
            base = i * self.c["chunk_seconds"]
            segs = [sg for sg in self.results.get(f, []) if self.plausible(sg)]
            if len(segs) == 1 and segs[0]["text"].strip().lower() in self.HALLUCINATIONS: segs = []
            texts = [sg["text"].strip().lower() for sg in segs]
            if len(segs) >= 3 and len(set(texts)) <= max(1, len(texts) // 3): segs = []   # "I", "I", "I", … across a chunk
            segs = [sg for sg in segs if len(sg["text"].strip().strip(".,!? ")) > 2 or len(segs) == 1 and len(texts[0]) > 2]
            for sgm in segs:
                t = base + sgm["start"]; lines.append(f"[{int(t) // 60:02d}:{int(t) % 60:02d}] {sgm['text']}")
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
    """Zoomed, marked screenshots for each pointer moment → pointer sheets. Returns (sheet paths, moments)."""
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
    start_epoch = st.get("started") or screen["t"]
    probe_r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                              "-of", "csv=p=0", str(video)], capture_output=True, text=True)
    try: vw, vh = (int(x) for x in probe_r.stdout.strip().split(","))
    except Exception: return [], []
    moments = detect_moments(events, start_epoch, c["pointer_moments"])
    if not moments: return [], []
    pdir = workdir / "pointer"; pdir.mkdir(exist_ok=True)
    crop_w, crop_h = min(960, vw), min(600, vh)
    rows = []
    for i, m in enumerate(moments):
        # screen points (origin bottom-left) → video pixels (origin top-left)
        px = m["x"] / screen["w"] * vw; py = (screen["h"] - m["y"]) / screen["h"] * vh
        x0 = int(min(max(px - crop_w / 2, 0), vw - crop_w)); y0 = int(min(max(py - crop_h / 2, 0), vh - crop_h))
        bx, by = int(px - x0), int(py - y0)
        out = pdir / f"p{i:02d}.jpg"
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(m['t'] - 0.1, 0):.2f}", "-i", str(video),
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
    cmd = sys.argv[1] if len(sys.argv) > 1 else "toggle"
    if cmd == "_run": cmd_run(c, sys.argv[2])
    elif cmd == "finish": finish(c, Path(sys.argv[2]).expanduser().resolve())
    else: {"toggle": cmd_toggle, "start": cmd_start, "stop": cmd_stop, "cancel": cmd_cancel, "status": cmd_status, "clean": cmd_clean, "usage": cmd_usage}[cmd](c)
