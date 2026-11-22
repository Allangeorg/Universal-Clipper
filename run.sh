#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f .env ] && [ -f .env.example ]; then cp .env.example .env; fi
if ! python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"' 2>/dev/null; then
  echo "Universal Clipper requires Python 3.11, 3.12, 3.13, or 3.14." >&2
  exit 1
fi
if [ ! -d .venv ]; then python3 -m venv .venv; fi
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  echo "FFmpeg/FFprobe are required. Install them and ensure both are on PATH." >&2
  exit 1
fi
exec uvicorn backend.main:app --host 127.0.0.1 --port 8765
