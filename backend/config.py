from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("CLIPPER_DATA_DIR", ROOT / "data")).expanduser().resolve()
SOURCE_DIR = DATA_DIR / "sources"
WORK_DIR = DATA_DIR / "work"
EXPORT_DIR = DATA_DIR / "exports"
BRANDING_DIR = DATA_DIR / "branding"
DB_PATH = DATA_DIR / "clipper.sqlite3"
SETTINGS_PATH = DATA_DIR / "settings.json"
STATIC_DIR = Path(__file__).resolve().parent / "static"

FFMPEG = os.getenv("FFMPEG_PATH", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_PATH", "ffprobe")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "4096"))

# Environment variables are useful for headless/dev deployments. The desktop UI
# stores user-entered keys in the operating-system credential vault instead.
ENV_OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
ENV_GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
ENV_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

for directory in (DATA_DIR, SOURCE_DIR, WORK_DIR, EXPORT_DIR, BRANDING_DIR):
    directory.mkdir(parents=True, exist_ok=True)
