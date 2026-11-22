from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger(__name__)


def normalize_video_url(value: str) -> str:
    """Normalize a video URL without ever passing it through pathlib.Path.

    Older Universal Clipper builds stored URLs as Path objects. On Windows this
    changes URL separators (for example, it can turn the URL into a path), so this also
    repairs those already-persisted job records when a user retries them.
    """
    if not isinstance(value, str):
        value = str(value)
    raw = value.strip().strip('\"').strip("'").strip()
    if not raw:
        raise ValueError("Paste a video URL first.")

    # Repair Windows/POSIX path normalization from older builds. Backslashes
    # are never valid URL separators, but commonly appear after Path(url).
    raw = raw.replace("\\", "/")
    if re.match(r"^https?:/[^/]", raw, flags=re.IGNORECASE):
        raw = re.sub(r"^(https?):/", r"\1://", raw, count=1, flags=re.IGNORECASE)

    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError("That video URL is malformed. Copy the complete link from the browser address bar.") from exc

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ValueError("Enter a complete video URL beginning with https:// or http://.")
    if any(ch.isspace() for ch in raw):
        raise ValueError("The video URL contains spaces. Paste the complete link without extra text.")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing embedded usernames or passwords are not supported.")

    # Keep query strings and fragments untouched; they may carry video IDs or
    # platform parameters that yt-dlp needs.
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def _available_js_runtimes() -> dict[str, dict[str, str]]:
    """Enable a supported JavaScript runtime when one is available on PATH."""
    runtimes: dict[str, dict[str, str]] = {}
    # Node 22+ is supported by current yt-dlp-ejs; Deno is also auto-enabled by
    # yt-dlp when installed. Only explicitly enable Node if it is available.
    node = shutil.which("node")
    if node:
        runtimes["node"] = {"path": node}
    return runtimes


def download_video(url: str, output_dir: Path) -> tuple[Path, dict[str, Any]]:
    normalized_url = normalize_video_url(url)
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError(
            "YouTube/URL downloads need the current yt-dlp package. Run: "
            'python -m pip install --upgrade "yt-dlp[default]"'
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        "format": "bv*[height<=1080]+ba/b[height<=1080]/best",
        "outtmpl": str(output_dir / "source.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "ignoreerrors": False,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "continuedl": True,
    }
    js_runtimes = _available_js_runtimes()
    if js_runtimes:
        options["js_runtimes"] = js_runtimes

    log.info("Starting URL download with yt-dlp %s: %s", getattr(yt_dlp.version, "__version__", "unknown"), normalized_url)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(normalized_url, download=True)
            if not info:
                raise RuntimeError("yt-dlp did not return video information. The video may be private, unavailable, or blocked by the source platform.")
            path_text = ydl.prepare_filename(info)
            path = Path(path_text)
            if not path.exists():
                mp4_path = path.with_suffix(".mp4")
                if mp4_path.exists():
                    path = mp4_path
            if not path.exists():
                matches = sorted(output_dir.glob("source.*"), key=lambda item: item.stat().st_mtime, reverse=True)
                if not matches:
                    raise RuntimeError("The download completed but the media file could not be located.")
                path = matches[0]
            metadata = {
                "title": info.get("title"),
                "webpage_url": info.get("webpage_url") or normalized_url,
                "extractor": info.get("extractor"),
            }
            return path, metadata
    except Exception as exc:
        detail = str(exc)
        lowered = detail.lower()
        if "urlopen error no host given" in lowered or "no host given" in lowered:
            raise RuntimeError(
                "The downloader received a URL without a hostname. This usually happens when an older job "
                "stored the URL as a Windows file path. This build repairs that format automatically. "
                "Create a new job or retry this one after updating to v0.3.1."
            ) from exc
        if "sign in to confirm" in lowered or "cookies" in lowered:
            raise RuntimeError(
                "YouTube requires browser authentication for this video. Try a public video first. "
                "For content you are allowed to access, configure browser cookies in a future supported ingest setting; "
                "do not share your account password or cookies."
            ) from exc
        if "requested format is not available" in lowered or "no video formats found" in lowered:
            raise RuntimeError(
                "YouTube did not provide a downloadable format. Update yt-dlp and its JavaScript challenge support, "
                "then try again. Some videos require sign-in or a supported PO Token."
            ) from exc
        raise RuntimeError(f"Video download failed: {detail[-1800:]}") from exc
