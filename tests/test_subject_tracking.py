from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.pipeline.editor import _ai_reframe_graph, _ffmpeg_piecewise
from backend.pipeline.subject_tracking import refine_subject_keyframes


def _make_moving_subject_video(path: Path) -> None:
    width, height, fps, seconds = 640, 360, 25, 5
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        pytest.skip("OpenCV MP4 writer unavailable")
    rng = np.random.default_rng(7)
    patch = rng.integers(0, 255, (130, 96, 3), dtype=np.uint8)
    # Add deterministic geometric detail so LK has points to follow.
    for y in range(0, 130, 12):
        cv2.line(patch, (0, y), (95, y), (255, 255, 255), 1)
    for x in range(0, 96, 12):
        cv2.line(patch, (x, 0), (x, 129), (0, 0, 0), 1)
    for index in range(fps * seconds):
        frame = np.full((height, width, 3), 18, dtype=np.uint8)
        x = 110 + index * 2
        frame[100:230, x:x + 96] = patch
        writer.write(frame)
    writer.release()


def _plan() -> dict:
    # Anchor boxes follow the moving subject at 0, 2 and 4 seconds.
    anchors = []
    for t, x in ((0.0, 110), (2.0, 210), (4.0, 310)):
        anchors.append({
            "t": t,
            "shot_cut": False,
            "subjects": [{
                "id": "host",
                "bbox": [x / 640 * 1000, 100 / 360 * 1000,
                         (x + 96) / 640 * 1000, 230 / 360 * 1000],
            }],
        })
    return {"layout": "single_subject", "primary_subject_id": "host", "keyframes": anchors}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg/OpenCV media stack required")
def test_cloud_anchors_are_refined_by_local_optical_flow(tmp_path: Path):
    source = tmp_path / "moving_subject.mp4"
    _make_moving_subject_video(source)
    refined, report = refine_subject_keyframes(source, 0, 5, _plan(), sample_fps=5, output_fps=2)
    assert report["tracking"] == "refined"
    assert report["frames_sampled"] >= 20
    assert report["crop_keyframes"] >= 8
    assert report["flow_updates"] > 0
    host_boxes = [frame["subjects"][0]["bbox"] for frame in refined["keyframes"] if frame["subjects"]]
    centers = [(box[0] + box[2]) / 2 for box in host_boxes]
    assert centers[-1] > centers[0] + 100


def test_tracker_keeps_cloud_plan_when_source_cannot_be_opened(tmp_path: Path):
    plan = _plan()
    refined, report = refine_subject_keyframes(tmp_path / "missing.mp4", 0, 5, plan)
    assert refined == plan
    assert report["tracking"] == "anchor-only"


def test_subject_id_churn_reassociates_without_name_error_or_hard_camera_reset():
    plan = {
        "layout": "single_subject", "primary_subject_id": "host",
        "keyframes": [
            {"t": 0, "subjects": [{"id": "host", "bbox": [400, 200, 600, 800]}]},
            {"t": 1, "subjects": [{"id": "renumbered_host", "bbox": [410, 200, 610, 800]}]},
            {"t": 2, "subjects": [{"id": "renumbered_host", "bbox": [420, 200, 620, 800]}]},
            {"t": 3, "subjects": [{"id": "renumbered_host", "bbox": [430, 200, 630, 800]}]},
        ],
    }
    graph, crop, report = _ai_reframe_graph({"width": 1280, "height": 720}, (1080, 1920), 4, plan)
    assert graph is None
    assert crop is not None
    assert report["method"] == "openrouter-vision-subject-path"
    assert report["subject_switches"] == 0


def test_piecewise_camera_path_holds_first_position_before_first_anchor():
    expression = _ffmpeg_piecewise([(1.0, 100.0), (2.0, 200.0)], 50.0)
    assert expression.startswith("if(lt(t,1.000),100.000,")


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is required for renderer integration test")
def test_optical_flow_refined_path_renders_as_valid_portrait_mp4(tmp_path: Path):
    from backend.pipeline.editor import render_edited_clip
    from backend.pipeline.media import probe

    source = tmp_path / "moving_subject.mp4"
    target = tmp_path / "moving_subject_short.mp4"
    _make_moving_subject_video(source)
    refined, tracking = refine_subject_keyframes(source, 0, 5, _plan(), sample_fps=5, output_fps=2)
    assert tracking["tracking"] == "refined"
    result = render_edited_clip(source, target, 0, 5, {
        "aspect_ratio": "9:16",
        "framing_mode": "adaptive",
        "framing_plan": refined,
        "preserve_audio": False,
        "crf": 20,
        "preset": "ultrafast",
        "fade_in": 0,
        "fade_out": 0,
    })
    metadata = probe(target)
    assert (metadata["width"], metadata["height"]) == (1080, 1920)
    assert metadata["duration"] == pytest.approx(5.0, abs=0.15)
    assert result["reframe"]["method"] == "openrouter-vision-subject-path"
    assert result["reframe"].get("tracking_report", {}).get("tracking") == "refined"
