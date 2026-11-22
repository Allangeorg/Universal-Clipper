from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.pipeline.editor import _ai_reframe_graph, render_edited_clip
from backend.pipeline.media import probe


def _plan(layout: str = "single_subject"):
    frames = []
    for index, t in enumerate((0.0, 1.0, 2.0, 3.0, 4.0)):
        subjects = [{"id": "host", "bbox": [420 + index * 8, 150, 620 + index * 8, 760]}]
        if layout == "two_panel":
            subjects.append({"id": "guest", "bbox": [90 + index * 5, 180, 280 + index * 5, 760]})
        frames.append({"t": t, "shot_cut": index == 3, "subjects": subjects})
    return {"layout": layout, "primary_subject_id": "host",
            "secondary_subject_id": "guest" if layout == "two_panel" else None,
            "keyframes": frames}


def _make_source(path: Path):
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=5",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=5",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ], check=True)


def test_ai_single_subject_plan_compiles_to_dynamic_portrait_crop():
    plan = _plan("single_subject")
    graph, simple_filter, report = _ai_reframe_graph({"width": 1280, "height": 720}, (1080, 1920), 5, plan)
    assert graph is None
    assert simple_filter is not None
    assert simple_filter.startswith("crop=")
    assert "if(lt(t," in simple_filter
    assert report["method"] == "openrouter-vision-subject-path"
    assert report["keyframes"] == 5


def test_ai_two_speaker_plan_compiles_to_two_panel_layout():
    graph, simple_filter, report = _ai_reframe_graph({"width": 1280, "height": 720}, (1080, 1920), 5, _plan("two_panel"))
    assert graph is not None
    assert "vstack=inputs=2" in graph
    assert "[top][bottom]" in graph
    assert simple_filter is None
    assert report["layout"] == "two_panel"


def test_ai_three_speaker_plan_compiles_to_three_panel_layout():
    plan = {
        "layout": "three_panel",
        "primary_subject_id": "host",
        "secondary_subject_id": "guest1",
        "tertiary_subject_id": "guest2",
        "keyframes": [
            {
                "t": float(i),
                "shot_cut": False,
                "subjects": [
                    {"id": "host", "bbox": [400, 100, 600, 700]},
                    {"id": "guest1", "bbox": [100, 150, 300, 750]},
                    {"id": "guest2", "bbox": [700, 150, 900, 750]},
                ],
            }
            for i in range(5)
        ],
    }
    graph, simple_filter, report = _ai_reframe_graph({"width": 1280, "height": 720}, (1080, 1920), 5, plan)
    assert graph is not None
    assert "split=3" in graph
    assert "hstack=inputs=2" in graph
    assert "vstack=inputs=2" in graph
    assert simple_filter is None
    assert report["layout"] == "three_panel"


def test_invalid_or_sparse_plan_falls_back_to_full_frame():
    graph, simple_filter, report = _ai_reframe_graph({"width": 1280, "height": 720}, (1080, 1920), 5,
        {"layout": "single_subject", "primary_subject_id": "host", "keyframes": []})
    assert graph is None and simple_filter is None
    assert report["layout"] == "full_frame"
    assert "Fewer than three" in report["reason"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required for renderer integration test")
def test_editor_renders_ai_subject_tracking_to_9x16(tmp_path: Path):
    source = tmp_path / "source.mp4"
    target = tmp_path / "adaptive.mp4"
    _make_source(source)
    result = render_edited_clip(source, target, 0, 5, {
        "aspect_ratio": "9:16", "framing_mode": "adaptive", "framing_plan": _plan("single_subject"),
        "preserve_audio": True, "normalize_audio": True, "crf": 18, "preset": "ultrafast",
        "fade_in": 0, "fade_out": 0,
    })
    meta = probe(target)
    assert (meta["width"], meta["height"]) == (1080, 1920)
    assert meta["duration"] == pytest.approx(5.0, abs=0.15)
    assert result["reframe"]["method"] == "openrouter-vision-subject-path"


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required for renderer integration test")
def test_editor_renders_ai_two_panel_composition_to_9x16(tmp_path: Path):
    source = tmp_path / "source.mp4"
    target = tmp_path / "two_panel.mp4"
    _make_source(source)
    result = render_edited_clip(source, target, 0, 5, {
        "aspect_ratio": "9:16", "framing_mode": "adaptive", "framing_plan": _plan("two_panel"),
        "preserve_audio": True, "normalize_audio": True, "crf": 18, "preset": "ultrafast",
        "fade_in": 0, "fade_out": 0,
    })
    meta = probe(target)
    assert (meta["width"], meta["height"]) == (1080, 1920)
    assert meta["duration"] == pytest.approx(5.0, abs=0.15)
    assert result["reframe"]["layout"] == "two_panel"


def test_crop_compiler_keeps_the_entire_refined_path_not_only_first_ai_anchors():
    frames = [
        {"t": index * 0.5, "shot_cut": index == 30,
         "subjects": [{"id": "host", "bbox": [300 + index, 180, 520 + index, 760]}]}
        for index in range(40)
    ]
    graph, crop, report = _ai_reframe_graph(
        {"width": 1920, "height": 1080}, (1080, 1920), 20,
        {"layout": "single_subject", "primary_subject_id": "host", "confidence": 0.95, "keyframes": frames},
    )
    assert graph is None and crop is not None
    assert report["keyframes"] == 40
    assert "if(lt(t,19.500)" in crop


def test_portrait_native_source_is_not_cropped_a_second_time():
    plan = _plan("single_subject")
    graph, crop, report = _ai_reframe_graph({"width": 1080, "height": 1920}, (1080, 1920), 5, plan)
    assert graph is None and crop is None
    assert report["method"] == "source-portrait-preserved"
    assert report["layout"] == "full_frame"
