#!/usr/bin/env python3
"""screencast — record the screen straight to MP4, and digest any recording into a
handful of timestamped contact sheets + a transcript that Claude Code reads in one pass.

  record    ffmpeg → hardware H.264 → .mp4 (no export, no conversion). Ctrl+C stops.
  digest    video → 1 fps sample → drop unchanged frames → contact sheets → on-device ASR
  devices   list screens / microphones ffmpeg can see
  remux     .mov → .mp4 without re-encoding (sub-second)

Dependencies: ffmpeg/ffprobe (brew install ffmpeg), numpy, Xcode CLT (swiftc, one-time build).
"""
import argparse, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SMALL_W, SMALL_H = 96, 54          # thumbnail used for change detection


def run(cmd, **kw):
    return subprocess.run(cmd, text=True, capture_output=True, **kw)


def die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr); sys.exit(code)


def hms(sec):
    sec = int(round(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def tool(name):
    """Return path to a bundled Swift helper, compiling it on first use."""
    binary, src = HERE / name, HERE / f"{name}.swift"
    if not binary.exists() or binary.stat().st_mtime < src.stat().st_mtime:
        print(f"  building {name} (one time)…", flush=True)
        r = run(["swiftc", "-O", str(src), "-o", str(binary)])
        if r.returncode:
            die(f"could not build {name}: {r.stderr.strip()[:600]}")
    return str(binary)


# ----------------------------------------------------------------- devices ---
def list_devices():
    out = run(["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""]).stderr
    video, audio, section = {}, {}, None
    for line in out.splitlines():
        if "AVFoundation video devices" in line: section = video
        elif "AVFoundation audio devices" in line: section = audio
        elif section is not None:
            m = re.search(r"\[(\d+)\] (.+)$", line)
            if m: section[int(m.group(1))] = m.group(2).strip()
    return video, audio


def cmd_devices(_):
    video, audio = list_devices()
    print("Screens:")
    for i, n in video.items():
        if n.lower().startswith("capture screen"):
            print(f"  --screen {n.split()[-1]}    ({n})")
    print("Microphones:")
    for i, n in audio.items():
        print(f'  --mic "{n}"')


def default_input_name():
    """Name of the microphone macOS currently uses as its default input (System Settings → Sound → Input),
    e.g. the AirPods when they are connected. None if it cannot be determined."""
    try:
        d = json.loads(run(["system_profiler", "SPAudioDataType", "-json"]).stdout)
        for dev in d["SPAudioDataType"][0]["_items"]:
            if dev.get("coreaudio_default_audio_input_device") == "spaudio_yes": return dev.get("_name")
    except Exception: pass
    return None


def _norm(s):
    return s.lower().replace("\u2019", "'")   # curly vs straight apostrophe ("Dorde’s AirPods")


LAST_MIC_NAME = None
HEADPHONE_HINTS = ["airpods", "headphone", "headset", "earbud", "buds", "beats", "jabra", "bose", "sony wh", "sony wf", "pods", "plantronics", "poly ", "logitech zone", "steelseries", "hyperx"]


def resolve_inputs(screen, mic):
    video, audio = list_devices()
    vidx = next((i for i, n in video.items() if n == f"Capture screen {screen}"), None)
    if vidx is None: die(f"no 'Capture screen {screen}'; run: screencast.py devices")
    global LAST_MIC_NAME
    if mic in (None, "", "none"): LAST_MIC_NAME = None; return f"{vidx}:none", False
    if mic in ("auto", None) or mic == "auto":
        # headphones/headset first (wired or wireless) — the mic nearest the mouth — else the system's input
        hp = next((n for n in audio.values() if any(h in _norm(n) for h in HEADPHONE_HINTS)), None)
        mic = hp or default_input_name() or "MacBook Pro Microphone"
        print(f"  mic: auto → {mic}" + (" (headphones)" if hp else " (system input)"), flush=True)
    if isinstance(mic, (list, tuple)):
        # priority list: first device that is present right now wins (AirPods appear only while connected)
        chosen = None
        for want in mic:
            if any(_norm(want) == _norm(n) or _norm(want) in _norm(n) for n in audio.values()):
                chosen = want; break
        if chosen is None: die(f"none of the preferred microphones is connected: {mic}")
        print(f"  mic: priority list → {chosen}", flush=True)
        mic = chosen
    if mic in ("default", "system"):
        # follow the system default input so the mic you are actually wearing/using is the one recorded
        mic = default_input_name() or "MacBook Pro Microphone"
        print(f"  mic: system default → {mic}", flush=True)
    aidx = next((i for i, n in audio.items() if _norm(n) == _norm(mic)), None)
    if aidx is None: aidx = next((i for i, n in audio.items() if _norm(mic) in _norm(n)), None)
    if aidx is None: die(f"no microphone matching {mic!r}; run: screencast.py devices")
    LAST_MIC_NAME = audio[aidx]
    return f"{vidx}:{aidx}", True


# ------------------------------------------------------------------ record ---
def cmd_record(a):
    out = Path(a.out or f"screencast-{time.strftime('%Y%m%d-%H%M%S')}.mp4").resolve()
    spec, has_audio = resolve_inputs(a.screen, a.mic)
    vidx, aidx = spec.split(":")
    base = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-stats",
            "-f", "avfoundation", "-capture_cursor", "1", "-capture_mouse_clicks", "1",
            "-framerate", "15", "-i", f"{vidx}:none"]
    if has_audio:   # separate mic input: one shared input drops ~75% of mic buffers
        base += ["-f", "avfoundation", "-thread_queue_size", "4096", "-i", f":{aidx}", "-map", "0:v", "-map", "1:a",
                 "-af", "aresample=async=1:first_pts=0"]
    t0 = time.time()
    if a.live:
        # No video file at all: the digest's frame pass runs *during* the recording.
        workdir = out.with_suffix("")
        cmd = base + frame_pass_args(workdir, a) + (["-map", "1:a", "-ac", "1", "-ar", "16000", "-y", str(workdir / "audio.wav")] if has_audio else [])
        print(f"● LIVE capture → {workdir}   (Ctrl+C to stop)")
        subprocess.run(cmd)
        print(f"\n■ stopped after {hms(time.time() - t0)}")
        finish(workdir, a, source=f"live capture of screen {a.screen}")
        return
    cmd = base + ["-vf", f"fps={a.rec_fps},scale={a.width}:-2,format=yuv420p",
                  "-c:v", "h264_videotoolbox", "-b:v", a.bitrate, "-realtime", "1", "-movflags", "+faststart"]
    if has_audio: cmd += ["-c:a", "aac", "-b:a", "96k"]
    cmd += [str(out)]
    print(f"● Recording screen {a.screen} → {out}   (Ctrl+C to stop)")
    subprocess.run(cmd)   # ffmpeg catches Ctrl+C itself and finalizes the MP4
    print(f"\n■ stopped after {hms(time.time() - t0)}  →  {out}")
    if a.then_digest:
        digest(out, out.with_suffix(".digest"), a)


# ------------------------------------------------------------------- remux ---
def cmd_remux(a):
    src = Path(a.video); dst = Path(a.out or src.with_suffix(".mp4"))
    r = run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-c", "copy", "-movflags", "+faststart", str(dst)])
    if r.returncode: die(r.stderr.strip())
    print(dst)


# ------------------------------------------------------------------ digest ---
def probe(video):
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(video)])
    if r.returncode: die(r.stderr.strip())
    info = json.loads(r.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    return float(info["format"].get("duration") or 0), has_audio, ((int(v["width"]), int(v["height"])) if v else (0, 0))


def frame_pass_args(workdir, a):
    """One decode → full-size 1 fps JPEGs + tiny grayscale stream for change detection."""
    raw = workdir / "raw"
    if raw.exists(): shutil.rmtree(raw)
    raw.mkdir(parents=True)
    fc = (f"[0:v]fps={a.fps},split[a][b];[a]scale={a.tile_width}:-2[big];"
          f"[b]scale={SMALL_W}:{SMALL_H}:flags=area,format=gray[small]")
    return ["-filter_complex", fc, "-map", "[big]", "-q:v", str(a.jpeg_q), "-start_number", "0", str(raw / "%06d.jpg"),
            "-map", "[small]", "-f", "rawvideo", "-y", str(workdir / "small.raw")]


def start_asr(wav, engine, out_txt):
    """Transcribe in the background; returns Popen or None."""
    if engine == "none" or not Path(wav).exists(): return None
    if engine in ("auto", "apple"):
        try:
            return subprocess.Popen([tool("transcribe"), str(wav)], stdout=open(out_txt, "w"), stderr=subprocess.PIPE, text=True)
        except SystemExit:
            if engine == "apple": raise
    if engine in ("auto", "mlx") and shutil.which("uv"):
        code = ("import sys,mlx_whisper;r=mlx_whisper.transcribe(sys.argv[1],path_or_hf_repo='/Users/george.blaze/Video to Claude/models/whisper-large-v3-turbo');"
                "[print(f\"[{int(s['start'])//60:02d}:{int(s['start'])%60:02d}] {s['text'].strip()}\") for s in r['segments']]")
        return subprocess.Popen(["uv", "run", "-q", "--with", "mlx-whisper", "python", "-c", code, str(wav)],
                                stdout=open(out_txt, "w"), stderr=subprocess.PIPE, text=True)
    return None


def pick_frames(workdir, a):
    """Keep a frame when ≥ --change of thumbnail pixels moved vs. the last kept frame,
    or when --max-gap seconds passed. A moving cursor is ~0.1% of pixels: ignored."""
    import numpy as np
    buf = np.fromfile(workdir / "small.raw", dtype=np.uint8)
    n = len(buf) // (SMALL_W * SMALL_H)
    small = buf[: n * SMALL_W * SMALL_H].reshape(n, SMALL_H, SMALL_W).astype(np.int16)
    keep, last = [0], 0
    for i in range(1, n):
        moved = float((np.abs(small[i] - small[last]) > a.pixel_delta).mean())
        if moved >= a.change or (i - last) >= a.max_gap * a.fps:
            keep.append(i); last = i
    return keep, n


QUIET_PEAK_DB = -18.0   # speech into a mic you are actually using peaks well above this; below it the mic was probably wrong


LAST_MEAN_DB = None
LAST_AUDIO = {}
MIC_MODE = None         # "voice" (miccap, voice-processed) | "raw" — set by vtc.py; voice processing trims the top end even on clear speech
ASR_QUALITY = None      # {"words": n, "lp": mean log-prob} of the live transcript, set by vtc.py; a confident transcript overrides the spectral guess
QUIET_MEAN_DB = -40.0   # speech into a working mic averages about -25…-35 dB; below -40 it is mostly room noise
HOT_PEAK_DB = -1.0      # at or above this the input is clipping
MUFFLED_HF = 0.06       # share of speech energy above 1 kHz; clear speech into a mic is 0.10-0.25, a far/turned-away mic is 0.02-0.05
CLIP_FRACTION = 0.002   # share of speech samples sitting in flat full-scale runs


def analyze_audio(media, sr=16000):
    """Level and spectral balance of the first audio stream, decoded with ffmpeg: dict(peak, mean, hf, clip, voiced) or {}.
    hf is the share of speech energy above 1 kHz (consonant detail); clip is the share of speech samples in flat
    full-scale runs (true clipping, not a single loud sample); voiced is how many seconds carried speech-level sound."""
    try:
        import numpy as np
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"], capture_output=True)
        x = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        if x.size < sr: return {}
        peak = float(20 * np.log10(max(float(np.abs(x).max()), 1e-9)))
        mean = float(20 * np.log10(max(float(np.sqrt(np.mean(x * x))), 1e-9)))
        n = sr; win = np.hanning(n); fr = np.fft.rfftfreq(n, 1 / sr); hi = tot = 0.0; voiced = 0; clip = 0; vs = 0
        for i in range(0, x.size - n + 1, n):
            seg = x[i:i + n]
            if np.sqrt(np.mean(seg * seg)) < 0.005: continue
            f = np.abs(np.fft.rfft(seg * win)) ** 2
            hi += float(f[fr >= 1000].sum()); tot += float(f[fr >= 80].sum()); voiced += 1; vs += n
            a = (np.abs(seg) >= 0.985).astype(np.int8)
            d = np.diff(np.concatenate(([0], a, [0]))); runs = np.where(d == -1)[0] - np.where(d == 1)[0]
            clip += int(runs[runs >= 3].sum())
        return {"peak": peak, "mean": mean, "hf": (hi / tot) if tot else None, "clip": (clip / vs) if vs else 0.0, "voiced": voiced}
    except Exception as e:
        print(f"  audio analysis skipped: {e}", flush=True); return {}


def measure_peak_db(media):
    """Peak level (dBFS) of the first audio stream, or None. Also records the mean level in LAST_MEAN_DB and the
    full analysis in LAST_AUDIO."""
    global LAST_MEAN_DB, LAST_AUDIO
    LAST_AUDIO = analyze_audio(media)
    LAST_MEAN_DB = LAST_AUDIO.get("mean")
    return LAST_AUDIO.get("peak")


def quiet_audio_note(peak_db, mic=None):
    """Warning text when the recording is muffled, too quiet or clipping, else ''."""
    st = LAST_AUDIO; mean = LAST_MEAN_DB
    who = f"'{mic}'" if mic else "the microphone"
    hf = st.get("hf")
    bar = 0.035 if MIC_MODE == "voice" else MUFFLED_HF
    thin = hf is not None and st.get("voiced", 0) >= 5 and (hf < bar or (MIC_MODE != "voice" and hf < 2 * MUFFLED_HF and mean is not None and mean < -36))
    q = ASR_QUALITY or {}
    if thin and q.get("words", 0) >= 8 and (q.get("lp") or -9) > -0.6:
        thin = False                       # Whisper was confident across a real sentence: the words are fine, however thin the spectrum
    if thin:
        return (f"audio is muffled (only {hf * 100:.0f}% of the speech energy above 1 kHz on {who}: consonants did not reach the mic): "
                f"you were far from or turned away from that microphone, or it is covered. Sit closer to it, point it at your mouth, "
                f"or use AirPods/a headset (they win automatically when connected). Treat the words above as unreliable and rely on the frames.")
    if peak_db is not None and peak_db >= HOT_PEAK_DB and st.get("clip", 0) >= CLIP_FRACTION:
        return (f"audio is clipping ({st['clip'] * 100:.1f}% of speech samples flattened at full scale on {who}): lower that input's volume "
                f"in System Settings → Sound → Input until the meter stays around two-thirds while you talk. Loud syllables may be garbled.")
    quiet_peak = peak_db is not None and peak_db <= QUIET_PEAK_DB
    quiet_mean = mean is not None and mean <= QUIET_MEAN_DB
    if not (quiet_peak or quiet_mean): return ""
    lvl = f"peak {peak_db:.0f} dB" + (f", average {mean:.0f} dB" if mean is not None else "")
    return (f"audio was very quiet ({lvl} on {who}): that microphone was probably not the one being spoken into "
            f"(AirPods not connected to this Mac, or far from the mouth), so the transcript may be empty or garbled. "
            f"Check `mic` in ~/.config/vtc.json (\"default\" follows System Settings → Sound → Input). "
            f"Treat the words above as unreliable and rely on the frames and pointer sheets.")


def finish(workdir, a, source, duration=None, asr=None, audio_peak_db=None, mic=None):
    """raw frames + small.raw (+ audio.wav) → kept frames → sheets → DIGEST.md"""
    t_all = time.time(); timings = {}
    workdir = Path(workdir); raw, frames, sheets = workdir / "raw", workdir / "frames", workdir / "sheets"
    transcript_txt = workdir / "transcript.txt"
    if asr is None and a.asr != "none" and (workdir / "audio.wav").exists():
        asr = start_asr(workdir / "audio.wav", a.asr, transcript_txt)

    t = time.time()
    keep, n = pick_frames(workdir, a)
    timings["dedupe"] = time.time() - t
    if duration is None: duration = n / a.fps
    for d in (frames, sheets):
        if d.exists(): shutil.rmtree(d)
        d.mkdir()
    stamps = []
    for i in keep:
        src = raw / f"{i:06d}.jpg"
        if src.exists():
            sec = i / a.fps; stamps.append(sec)
            src.rename(frames / f"{hms(sec).replace(':', '-')}.jpg")
    if not a.keep_raw: shutil.rmtree(raw, ignore_errors=True)
    if not stamps: die("no frames extracted")

    t = time.time()
    cols, rows = (int(x) for x in a.grid.lower().split("x")); per = cols * rows
    listfile = workdir / "sheets.txt"
    listfile.write_text("".join(f"{frames / (hms(s).replace(':', '-') + '.jpg')}\t{hms(s)}\n" for s in stamps))
    r = run([tool("sheet"), str(listfile), str(cols), str(rows), str(sheets), "0.82"])
    if r.returncode: die(f"sheet composer failed: {r.stderr.strip()[:600]}")
    timings["sheets"] = time.time() - t
    sheet_files = sorted(sheets.glob("sheet_*.jpg"))

    transcript = ""
    if asr is not None:
        t = time.time(); _, err = asr.communicate(); timings["transcript wait"] = time.time() - t
        if asr.returncode: print(f"  transcription failed: {err.strip()[-400:]}")
    if transcript_txt.exists(): transcript = transcript_txt.read_text().strip()

    lines = ["# Screencast digest", "",
             f"- source: {source}",
             f"- duration: {hms(duration)}  |  kept {len(stamps)} of {n} sampled frames (changes only; unchanged seconds dropped)",
             f"- {len(sheet_files)} sheets, {cols}x{rows} tiles each, read left→right then top→bottom; every tile carries its mm:ss stamp top-left",
             "", "## Sheets", ""]
    for i, sf in enumerate(sheet_files):
        chunk = stamps[i * per:(i + 1) * per]
        lines.append(f"- `{sf}` — {hms(chunk[0])} → {hms(chunk[-1])}  (tiles: {', '.join(hms(s) for s in chunk)})")
    mic = mic or LAST_MIC_NAME
    note = quiet_audio_note(audio_peak_db, mic)
    if audio_peak_db is not None: lines.insert(4, f"- mic: {mic or 'unknown'}  |  audio peak: {audio_peak_db:.0f} dB" + (f" | clarity: {LAST_AUDIO['hf'] * 100:.0f}% above 1 kHz" if LAST_AUDIO.get('hf') is not None else "") + (f"  ⚠ {note}" if note else ""))
    lines += ["", "## Transcript", ""] + ([f"⚠ {note}", ""] if note else []) + [transcript or "_(no speech / transcription disabled)_", ""]
    (workdir / "DIGEST.md").write_text("\n".join(lines))
    if note: (workdir / "audio_warning.txt").write_text(note); print(f"  ⚠ {note}")
    print(f"✔ {len(stamps)}/{n} frames kept → {len(sheet_files)} sheets in {time.time() - t_all:.1f}s   "
          + "  ".join(f"{k} {v:.1f}s" for k, v in timings.items()))
    print(f"DIGEST: {workdir / 'DIGEST.md'}")


def digest(video, workdir, a):
    t0 = time.time(); workdir = Path(workdir); workdir.mkdir(parents=True, exist_ok=True)
    dur, has_audio, (w, h) = probe(video)
    print(f"▶ {Path(video).name}: {hms(dur)}, {w}x{h}, audio={'yes' if has_audio else 'no'}", flush=True)
    asr = None; audio_peak_db = measure_peak_db(video) if has_audio else None
    if has_audio and a.asr != "none":           # audio demux is instant; ASR overlaps the frame pass
        wav = workdir / "audio.wav"
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
             "-af", "aresample=async=1:first_pts=0,highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11", str(wav)])
        asr = start_asr(wav, a.asr, workdir / "transcript.txt")
    t = time.time()
    r = run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-threads", "0", "-i", str(video)] + frame_pass_args(workdir, a))
    if r.returncode: die(r.stderr.strip())
    print(f"  frame pass {time.time() - t:.1f}s", flush=True)
    finish(workdir, a, source=str(video), duration=dur, asr=asr, audio_peak_db=audio_peak_db)
    print(f"  total {time.time() - t0:.1f}s")


def cmd_digest(a):
    video = Path(a.video).expanduser().resolve()
    if not video.exists(): die(f"{video} not found")
    digest(video, Path(a.out) if a.out else video.with_name(video.stem + ".digest"), a)


# -------------------------------------------------------------------- main ---
def add_digest_opts(p):
    p.add_argument("--grid", default="2x2", help="tiles per sheet: 2x2 (default), 1x1 for dense code, 3x3 for a fast skim")
    p.add_argument("--tile-width", type=int, default=780, help="px width of each tile (default 780 → 1572px-wide 2x2 sheet)")
    p.add_argument("--fps", type=float, default=1.0, help="sampling rate before change detection (default 1)")
    p.add_argument("--max-gap", type=int, default=20, help="always keep a frame at least every N seconds (default 20)")
    p.add_argument("--change", type=float, default=0.004, help="fraction of pixels that must move to keep a frame (default 0.004)")
    p.add_argument("--pixel-delta", type=int, default=24, help="gray-level delta counted as 'moved' (default 24)")
    p.add_argument("--jpeg-q", type=int, default=3, help="ffmpeg JPEG quality 2-31, lower is better (default 3)")
    p.add_argument("--asr", default="auto", choices=["auto", "apple", "mlx", "none"], help="speech-to-text: apple (on-device, default), mlx (whisper via uv), none")
    p.add_argument("--keep-raw", action="store_true", help="keep every sampled frame, not just the changed ones")


ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
sp = ap.add_subparsers(dest="cmd", required=True)
p = sp.add_parser("record", help="record the screen straight to MP4")
p.add_argument("--out"); p.add_argument("--screen", type=int, default=0)
p.add_argument("--mic", default="none", help='microphone name (see `devices`), "default" (the system input, e.g. AirPods when connected) or "none"')
p.add_argument("--rate", dest="rec_fps", type=int, default=5, help="recording frame rate (default 5: small file, fast digest)")
p.add_argument("--width", type=int, default=1920, help="downscale to this width (default 1920)")
p.add_argument("--bitrate", default="4M")
p.add_argument("--live", action="store_true", help="no video file: run the frame pass during the recording")
p.add_argument("--then-digest", action="store_true", help="digest automatically when the recording stops")
add_digest_opts(p); p.set_defaults(func=cmd_record)
p = sp.add_parser("digest", help="recording → sheets + transcript"); p.add_argument("video"); p.add_argument("--out")
add_digest_opts(p); p.set_defaults(func=cmd_digest)
p = sp.add_parser("devices", help="list screens and microphones"); p.set_defaults(func=cmd_devices)
p = sp.add_parser("remux", help=".mov → .mp4, no re-encode"); p.add_argument("video"); p.add_argument("--out"); p.set_defaults(func=cmd_remux)

if __name__ == "__main__":
    args = ap.parse_args()
    if args.cmd == "record" and not args.live:
        args.fps = args.fps  # digest sampling rate; recording rate is args.rec_fps
    args.func(args)
