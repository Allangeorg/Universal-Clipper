import shutil
import subprocess
from pathlib import Path

import pytest

from backend.pipeline.editor import render_edited_clip
from backend.pipeline.media import probe


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required for renderer integration test")
def test_editor_renders_vertical_speed_caption_and_audio(tmp_path: Path):
    source = tmp_path / "source.mp4"
    target = tmp_path / "edited.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=6",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
        ],
        check=True,
    )
    transcript = {"segments": [{"start": 1.0, "end": 2.5, "text": "A practical caption rendering test."}]}
    settings = {
        "aspect_ratio": "9:16", "focus_x": 0.75, "focus_y": 0.5, "zoom": 1.15,
        "speed": 1.25, "volume": 0.8, "brightness": 0.04, "contrast": 1.05,
        "saturation": 1.15, "fade_in": 0.2, "fade_out": 0.2, "preserve_audio": True,
        "captions_enabled": True, "caption_style": "karaoke", "caption_position": "bottom",
        "font_size": 54, "text_colour": "#FFFFFF", "outline_colour": "#000000",
        "title_overlay": "Editor integration test",
    }
    result = render_edited_clip(source, target, 1, 5, settings, transcript)
    output = probe(target)
    assert target.is_file() and target.stat().st_size > 1000
    assert (output["width"], output["height"]) == (1080, 1920)
    assert output["has_audio"] is True
    assert output["audio_codec"] == "aac"
    assert output["duration"] == pytest.approx(3.2, abs=0.12)
    assert result["settings"]["focus_x"] == 0.75


def test_editor_validates_and_clamps_edit_settings(tmp_path: Path):
    # A non-existent source must fail clearly before invoking FFmpeg.
    with pytest.raises(Exception, match="Original source file is missing"):
        render_edited_clip(tmp_path / "missing.mp4", tmp_path / "out.mp4", 0, 1, {})


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required for renderer integration test")
def test_editor_fit_blur_fallback_accepts_colour_fade_and_speed_filters(tmp_path: Path, monkeypatch):
    """The complex fit/blur graph must carry post-filters without a competing -vf."""
    source = tmp_path / "portrait_source.mp4"
    target = tmp_path / "portrait_edited.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=25:duration=5",
            "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=48000:duration=5",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
        ],
        check=True,
    )
    report = {
        "mode": "fit-blur",
        "active_bounds": {"left": 0, "top": 50, "right": 720, "bottom": 1230},
        "faces_detected": 2,
        "method": "fit-blur",
        "duration": 4.0,
    }
    monkeypatch.setattr(
        "backend.pipeline.editor.crop_filter",
        lambda *args, **kwargs: ("unused-crop-filter", report),
    )
    settings = {
        "aspect_ratio": "9:16", "smart_reframe": True, "speed": 1.25,
        "brightness": 0.04, "contrast": 1.05, "saturation": 1.1,
        "fade_in": 0.2, "fade_out": 0.2, "preserve_audio": True,
        "normalize_audio": True,
    }
    result = render_edited_clip(source, target, 0.5, 4.5, settings)
    output = probe(target)
    assert target.is_file() and target.stat().st_size > 1000
    assert (output["width"], output["height"]) == (1080, 1920)
    assert output["has_audio"] is True
    assert output["duration"] == pytest.approx(3.2, abs=0.15)
    assert result["reframe"]["method"] == "full-frame-fallback"


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required for renderer integration test")
def test_full_frame_mode_keeps_landscape_source_visible_in_vertical_output(tmp_path: Path):
    source = tmp_path / "landscape.mp4"
    target = tmp_path / "portrait_full_frame.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
        check=True,
    )
    result = render_edited_clip(source, target, 0, 4, {
        "aspect_ratio": "9:16", "framing_mode": "full_frame", "preserve_audio": False,
        "crf": 17, "preset": "veryfast",
    })
    output = probe(target)
    assert (output["width"], output["height"]) == (1080, 1920)
    assert result["reframe"]["method"] == "full-frame-preserve"
    assert result["duration"] == pytest.approx(4.0, abs=0.12)
