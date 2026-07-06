#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


MODEL = os.environ.get("BAILIAN_ASR_MODEL", "fun-asr-flash-2026-06-15")
TIMEOUT = float(os.environ.get("BAILIAN_ASR_TIMEOUT", "45"))


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def ffmpeg_to_wav(src: Path, dest: Path) -> None:
    proc = subprocess.run(
        [
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
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        fail(proc.stderr.strip() or "ffmpeg failed")


def api_url() -> str:
    endpoint = os.environ.get("BAILIAN_ASR_ENDPOINT", "").strip()
    if endpoint:
        return endpoint
    workspace = os.environ.get("BAILIAN_WORKSPACE_ID", "").strip()
    if not workspace:
        fail("BAILIAN_WORKSPACE_ID is required")
    return f"https://{workspace}.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"


def parse_text(data: dict) -> str:
    output = data.get("output") if isinstance(data, dict) else None
    if isinstance(output, dict):
        text = output.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        sentence = output.get("sentence")
        if isinstance(sentence, dict):
            text = sentence.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def transcribe(wav_path: Path) -> str:
    api_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("BAILIAN_API_KEY")
    if not api_key:
        fail("DASHSCOPE_API_KEY or BAILIAN_API_KEY is required")

    data_uri = "data:audio/wav;base64," + base64.b64encode(wav_path.read_bytes()).decode("ascii")
    payload = {
        "model": MODEL,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": data_uri},
                        }
                    ],
                }
            ]
        },
        "parameters": {
            "format": "wav",
            "sample_rate": "16000",
        },
    }
    req = urllib.request.Request(
        api_url(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-SSE": "disable",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        fail(f"bailian asr HTTP {exc.code}: {detail[:800]}")
    except Exception as exc:
        fail(f"bailian asr request failed: {exc}")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        fail(f"bailian asr returned non-json: {body[:800]}")

    text = parse_text(data)
    if not text:
        fail(f"bailian asr returned no text: {json.dumps(data, ensure_ascii=False)[:800]}")
    return text


def main() -> int:
    if len(sys.argv) < 2:
        fail("usage: bailian_asr_transcribe.py <audio_path> [mime]")
    src = Path(sys.argv[1])
    if not src.is_file():
        fail(f"audio not found: {src}")
    with tempfile.TemporaryDirectory(prefix="tidal-bailian-asr-") as tmp:
        wav = Path(tmp) / "input.wav"
        ffmpeg_to_wav(src, wav)
        print(transcribe(wav))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
