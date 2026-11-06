#!/usr/bin/env python3
"""Persistent Whisper worker (mlx-whisper, large-v3-turbo). Reads one wav path per stdin line,
prints one JSON line per file: {"path":..., "segments":[{"start":s,"end":s,"text":...}]}.
Run with: uv run -q --with mlx-whisper python asr_worker.py"""
import json, sys
import mlx_whisper
import os
MODEL = os.environ.get("VTC_WHISPER_MODEL", "/Users/george.blaze/Video to Claude/models/whisper-large-v3-turbo")   # local folder (config.json + weights.safetensors) or an HF repo id
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    path = line.strip()
    if not path: continue
    try:
        lang = os.environ.get("VTC_WHISPER_LANGUAGE") or None
        r = mlx_whisper.transcribe(path, path_or_hf_repo=MODEL, condition_on_previous_text=False, language=lang)
        segs = [{"start": s["start"], "end": s["end"], "text": s["text"].strip(), "nsp": s.get("no_speech_prob", 0), "lp": s.get("avg_logprob", 0), "cr": s.get("compression_ratio", 0)} for s in r["segments"] if s["text"].strip()]
        print(json.dumps({"path": path, "segments": segs}), flush=True)
    except Exception as e:
        print(json.dumps({"path": path, "error": str(e)}), flush=True)
