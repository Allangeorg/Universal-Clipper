from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from backend.pipeline.downloader import download_video, normalize_video_url


def test_normalize_preserves_a_standard_youtube_url():
    url = "https://www.youtube.com/watch?v=AbCdEf12345&si=share-token"
    assert normalize_video_url(url) == url


def test_normalize_repairs_windows_path_corruption_from_old_jobs():
    corrupted = r"https:\www.youtube.com\watch?v=AbCdEf12345&si=share-token"
    assert normalize_video_url(corrupted) == "https://www.youtube.com/watch?v=AbCdEf12345&si=share-token"


def test_normalize_repairs_single_slash_posix_path_corruption():
    corrupted = "https:/youtu.be/AbCdEf12345"
    assert normalize_video_url(corrupted) == "https://youtu.be/AbCdEf12345"


@pytest.mark.parametrize("bad", ["", "youtube.com/watch?v=abc", "file:///C:/video.mp4", "https:///watch?v=x", "https://user:pass@example.com/video"])
def test_normalize_rejects_invalid_or_unsafe_url_shapes(bad):
    with pytest.raises(ValueError):
        normalize_video_url(bad)


def test_downloader_sends_normalized_url_to_ytdlp(monkeypatch, tmp_path):
    received = {}
    target = tmp_path / "source.mp4"

    class FakeYoutubeDL:
        def __init__(self, options):
            received["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            received["url"] = url
            target.write_bytes(b"fake mp4")
            return {"title": "Test video", "webpage_url": url, "extractor": "youtube"}

        def prepare_filename(self, info):
            return str(target)

    fake = types.SimpleNamespace(
        YoutubeDL=FakeYoutubeDL,
        version=types.SimpleNamespace(__version__="2026.10.26"),
    )
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    node = __import__("shutil").which("node")
    path, metadata = download_video(r"https:\www.youtube.com\watch?v=AbCdEf12345", tmp_path)
    assert received["url"] == "https://www.youtube.com/watch?v=AbCdEf12345"
    assert path == target
    assert metadata["extractor"] == "youtube"
    if node:
        assert received["options"]["js_runtimes"]["node"]["path"] == node
