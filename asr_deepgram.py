#!/usr/bin/env python3
"""Cloud ASR worker: Deepgram nova-3 (pre-recorded REST), the alternative to asr_worker.py (local Whisper).
Same protocol: prints {"ready": true}, then reads one wav path per stdin line and prints one JSON line per file:
{"path":..., "segments":[{"start":s, "end":s, "text":..., "nsp":0.0, "lp":log(confidence), "cr":1.0}]}.
Enable with "asr_engine": "deepgram" in ~/.config/vtc.json. The key comes from env DEEPGRAM_API_KEY, else from
"deepgram_api_key" in ~/.config/vtc.json (the LaunchAgent does not inherit the shell's environment, so the
config-file fallback is what makes it work from the hotkey). Standard library only: uv run python asr_deepgram.py
One-shot test: python asr_deepgram.py file.wav  -> prints "[mm:ss] text" lines."""
import json, math, os, re, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path

URL = "https://api.deepgram.com/v1/listen"
TIMEOUT = 30


def api_key():
    k = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if k: return k
    try: return (json.loads((Path.home() / ".config" / "vtc.json").read_text()).get("deepgram_api_key") or "").strip()
    except Exception: return ""


def lp_of(conf):
    """Deepgram confidence -> Whisper-like avg_logprob, clamped to [-5, 0]; -0.5 when missing."""
    if conf is None: return -0.5
    try: return max(-5.0, min(0.0, math.log(max(float(conf), 1e-9))))
    except Exception: return -0.5


def segment(start, end, text, conf, words=None):
    ws = [{"w": (w.get("punctuated_word") or w.get("word") or "").strip(), "s": round(float(w.get("start", 0)), 3), "e": round(float(w.get("end", 0)), 3)}
          for w in (words or []) if (w.get("punctuated_word") or w.get("word") or "").strip()]
    return {"start": round(float(start), 3), "end": round(float(end), 3), "text": text.strip(), "nsp": 0.0, "lp": lp_of(conf), "cr": 1.0, "words": ws}


def words_to_segments(words, max_span=8.0):
    """Fallback when utterances are absent: group words into a segment per sentence or per ~8 s."""
    segs, cur = [], []
    for w in words:
        cur.append(w)
        text = w.get("punctuated_word") or w.get("word") or ""
        if re.search(r"[.!?]$", text) or cur[-1].get("end", 0) - cur[0].get("start", 0) >= max_span:
            segs.append(cur); cur = []
    if cur: segs.append(cur)
    out = []
    for g in segs:
        text = " ".join((w.get("punctuated_word") or w.get("word") or "") for w in g).strip()
        confs = [w["confidence"] for w in g if w.get("confidence") is not None]
        if text: out.append(segment(g[0].get("start", 0), g[-1].get("end", 0), text, sum(confs) / len(confs) if confs else None, g))
    return out


def transcribe(path, key):
    lang = os.environ.get("VTC_WHISPER_LANGUAGE", "en").strip()
    q = {"model": "nova-3", "smart_format": "true", "punctuate": "true", "utterances": "true"}
    q.update({"language": lang} if lang else {"detect_language": "true"})
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(q), data=Path(path).read_bytes(), method="POST",
                                 headers={"Authorization": f"Token {key}", "Content-Type": "audio/wav"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        res = json.loads(r.read().decode("utf-8")).get("results", {})
    utts = res.get("utterances") or []
    if utts:
        return [segment(u.get("start", 0), u.get("end", 0), u.get("transcript", ""), u.get("confidence"), u.get("words")) for u in utts if u.get("transcript", "").strip()]
    alt = ((res.get("channels") or [{}])[0].get("alternatives") or [{}])[0]
    if not alt.get("transcript", "").strip(): return []
    if alt.get("words"): return words_to_segments(alt["words"])
    return [segment(0, 0, alt["transcript"], alt.get("confidence"))]


def short_error(e):
    if isinstance(e, urllib.error.HTTPError):
        try: body = e.read(512).decode("utf-8", "replace").strip()
        except Exception: body = ""
        return f"HTTP {e.code}: {body[:200] or e.reason}"
    return f"{type(e).__name__}: {str(e)[:200]}"


def main():
    key = api_key()
    if not key:
        print(json.dumps({"error": "DEEPGRAM_API_KEY not set"}), flush=True)
        print(json.dumps({"ready": False}), flush=True)
        sys.exit(2)
    if len(sys.argv) > 1:                                    # one-shot: python asr_deepgram.py file.wav
        try:
            segs = transcribe(sys.argv[1], key)
        except Exception as e:
            msg = short_error(e)
            if "401" in msg: msg += "  → the key is not accepted. Deepgram shows an ID (xxxxxxxx-xxxx-…) and a secret; store the secret, see docs/settings.md"
            print(f"deepgram: {msg}"); sys.exit(1)
        if not segs: print("(no speech recognized)")
        for sg in segs:
            t = int(sg["start"]); print(f"[{t // 60:02d}:{t % 60:02d}] {sg['text']}")
        print(f"deepgram OK: nova-3, {sum(len(sg['text'].split()) for sg in segs)} words")
        return
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        path = line.strip()
        if not path: continue
        try:
            print(json.dumps({"path": path, "segments": transcribe(path, key)}), flush=True)
        except Exception as e:
            print(json.dumps({"path": path, "segments": [], "error": short_error(e)}), flush=True)


if __name__ == "__main__":
    main()
