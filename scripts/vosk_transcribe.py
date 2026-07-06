#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from vosk import KaldiRecognizer, Model, SetLogLevel


DEFAULT_MODEL = "/root/.local/share/vosk-models/vosk-model-small-cn-0.22"


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def ffmpeg_to_wav(src: Path, dest: Path) -> None:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        str(dest),
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        fail(proc.stderr.strip() or "ffmpeg failed")


def transcribe_wav(model_path: Path, wav_path: Path) -> str:
    SetLogLevel(-1)
    model = Model(str(model_path))
    rec = KaldiRecognizer(model, 16000)
    parts: list[str] = []

    with wave.open(str(wav_path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getframerate() != 16000:
            fail("wav must be mono 16k")
        while True:
            data = wf.readframes(4000)
            if not data:
                break
            if rec.AcceptWaveform(data):
                obj = json.loads(rec.Result())
                text = (obj.get("text") or "").strip()
                if text:
                    parts.append(text)

    final = json.loads(rec.FinalResult())
    text = (final.get("text") or "").strip()
    if text:
        parts.append(text)

    return " ".join(parts).strip()


def main() -> int:
    if len(sys.argv) < 2:
        fail("usage: vosk_transcribe.py <audio_path> [mime]")

    audio_path = Path(sys.argv[1])
    if not audio_path.is_file():
        fail(f"audio not found: {audio_path}")

    model_path = Path(os.environ.get("VOSK_MODEL_PATH", DEFAULT_MODEL))
    if not model_path.is_dir():
        fail(f"vosk model not found: {model_path}")

    with tempfile.TemporaryDirectory(prefix="tidal-asr-") as tmp:
        wav_path = Path(tmp) / "input.wav"
        ffmpeg_to_wav(audio_path, wav_path)
        text = transcribe_wav(model_path, wav_path)

    if text:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
