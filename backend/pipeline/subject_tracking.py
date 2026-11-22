from __future__ import annotations

"""Cloud-anchored, local optical-flow subject tracking.

A vision-capable OpenRouter model supplies sparse, semantic subject boxes.  This
module follows those anchors between samples with OpenCV pyramidal Lucas-Kanade
optical flow, detects scene cuts, and emits a denser crop path.  It does not load
or run a local generative model or object-detection network.
"""

import logging
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

log = logging.getLogger(__name__)


def _gray_small(frame: np.ndarray, max_width: int = 640) -> tuple[np.ndarray, float]:
    height, width = frame.shape[:2]
    scale = min(1.0, max_width / max(1, width))
    if scale < 1.0:
        frame = cv2.resize(
            frame,
            (max(2, int(round(width * scale))), max(2, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return gray, scale


def _histogram(gray: np.ndarray) -> np.ndarray:
    hist = cv2.calcHist([gray], [0], None, [32], [0, 256])
    cv2.normalize(hist, hist)
    return hist


def _seed_points(gray: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray | None:
    x, y, w, h = box
    height, width = gray.shape[:2]
    x1 = max(0, min(width - 1, int(round(x))))
    y1 = max(0, min(height - 1, int(round(y))))
    x2 = max(x1 + 1, min(width, int(round(x + w))))
    y2 = max(y1 + 1, min(height, int(round(y + h))))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    mask = np.zeros_like(gray)
    # Keep feature points away from the exact bounding-box edge where background
    # texture is most likely to contaminate the subject trajectory.
    pad_x = int((x2 - x1) * 0.06)
    pad_y = int((y2 - y1) * 0.04)
    mask[max(0, y1 + pad_y):max(y1 + pad_y + 1, y2 - pad_y),
         max(0, x1 + pad_x):max(x1 + pad_x + 1, x2 - pad_x)] = 255
    points = cv2.goodFeaturesToTrack(
        gray, maxCorners=72, qualityLevel=0.012, minDistance=4,
        blockSize=7, mask=mask, useHarrisDetector=False,
    )
    return points.astype(np.float32) if points is not None and len(points) >= 4 else None


def _clamp_box(box: tuple[float, float, float, float], width: int, height: int) -> tuple[float, float, float, float]:
    x, y, w, h = box
    w = max(8.0, min(float(width), w))
    h = max(8.0, min(float(height), h))
    x = max(0.0, min(float(width) - w, x))
    y = max(0.0, min(float(height) - h, y))
    return x, y, w, h


def refine_subject_keyframes(source: Path, start: float, end: float, plan: dict[str, Any],
                             sample_fps: float = 5.0, output_fps: float = 2.0) -> tuple[dict[str, Any], dict[str, Any]]:
    """Refine sparse cloud subject boxes into a smooth, denser crop path.

    Cloud boxes are authoritative anchors. Optical flow only bridges motion
    between them; it never invents a new subject or crosses a detected scene
    cut. If tracking is weak, the last good box is held until the next cloud
    anchor. The caller may safely fall back to full-frame composition.
    """
    if not source.is_file() or not isinstance(plan, dict):
        return plan, {"tracking": "anchor-only", "reason": "Source or framing plan unavailable."}
    layout = str(plan.get("layout", "full_frame"))
    if layout not in {"single_subject", "two_panel", "three_panel"}:
        return plan, {"tracking": "not-needed", "reason": "Full-frame layout selected."}
    clip_duration = max(0.1, float(end) - float(start))
    anchors: dict[float, dict[str, Any]] = {}
    for item in plan.get("keyframes", []) if isinstance(plan.get("keyframes"), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            t = max(0.0, min(clip_duration, float(item.get("t", 0))))
        except (TypeError, ValueError):
            continue
        subjects = item.get("subjects", [])
        parsed: dict[str, tuple[float, float, float, float]] = {}
        for subject in subjects if isinstance(subjects, list) else []:
            if not isinstance(subject, dict) or not subject.get("id"):
                continue
            box = subject.get("bbox", [])
            if not isinstance(box, list) or len(box) != 4:
                continue
            try:
                x1, y1, x2, y2 = [float(value) for value in box]
            except (TypeError, ValueError):
                continue
            if x2 > x1 and y2 > y1:
                parsed[str(subject["id"])[:40]] = (x1, y1, x2, y2)
        anchors[round(t, 3)] = {"subjects": parsed, "shot_cut": bool(item.get("shot_cut", False))}
    if len(anchors) < 3:
        return plan, {"tracking": "anchor-only", "reason": "Fewer than three cloud anchor frames."}

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        return plan, {"tracking": "anchor-only", "reason": "OpenCV could not open the source for optical flow."}
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        source_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        source_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if fps <= 0 or source_width < 2 or source_height < 2:
            return plan, {"tracking": "anchor-only", "reason": "Source frame metadata is invalid."}
        scale = min(1.0, 640.0 / source_width)
        small_width = max(2, int(round(source_width * scale)))
        small_height = max(2, int(round(source_height * scale)))
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(start)) * 1000.0)
        first_frame_number = int(cap.get(cv2.CAP_PROP_POS_FRAMES) or 0)
        stride = max(1, int(round(fps / max(1.0, float(sample_fps)))))
        actual_sample_fps = fps / stride
        tolerance = min(0.18, max(0.08, 0.58 / actual_sample_fps))
        # Keep a bounded crop-expression size for FFmpeg/Windows while retaining
        # denser camera motion for ordinary 15–120 second Shorts.
        output_step = max(1.0 / max(0.5, float(output_fps)), clip_duration / 239.0)

        anchor_times = sorted(anchors)
        anchor_cursor = 0
        states: dict[str, dict[str, Any]] = {}
        previous_gray: np.ndarray | None = None
        previous_hist: np.ndarray | None = None
        output_frames: list[dict[str, Any]] = []
        previous_emit = -output_step
        sampled = 0
        tracked = 0
        cuts = 0
        max_track_age = max(1, int(round(actual_sample_fps * 0.9)))
        frame_number = first_frame_number

        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            relative_t = (frame_number - first_frame_number) / fps
            frame_number += 1
            if relative_t > clip_duration + 0.05:
                break
            # Skip decode work between tracking samples while preserving exact
            # source-frame timestamps and alignment.
            for _ in range(stride - 1):
                if not cap.grab():
                    break
                frame_number += 1
            gray, _ = _gray_small(frame)
            if gray.shape != (small_height, small_width):
                gray = cv2.resize(gray, (small_width, small_height), interpolation=cv2.INTER_AREA)
            current_hist = _histogram(gray)
            scene_cut = False
            if previous_hist is not None:
                scene_cut = cv2.compareHist(previous_hist, current_hist, cv2.HISTCMP_BHATTACHARYYA) > 0.58
            previous_hist = current_hist

            # Match a cloud anchor to the nearest sampled frame. Cloud boxes are
            # refreshed every few seconds; flow bridges the intervals.
            while anchor_cursor + 1 < len(anchor_times) and anchor_times[anchor_cursor + 1] < relative_t - tolerance:
                anchor_cursor += 1
            nearest_anchor_time = min(
                anchor_times[max(0, anchor_cursor - 1):min(len(anchor_times), anchor_cursor + 2)],
                key=lambda value: abs(value - relative_t),
            )
            anchor = anchors[nearest_anchor_time] if abs(nearest_anchor_time - relative_t) <= tolerance else None
            cloud_cut = bool(anchor and anchor.get("shot_cut"))
            shot_cut = scene_cut or cloud_cut
            if shot_cut:
                states.clear()
                cuts += 1

            # Track each known identity independently. No detections are allowed
            # to switch identities; only a new cloud anchor can re-seed a track.
            if previous_gray is not None and not shot_cut:
                for sid, state in list(states.items()):
                    points = state.get("points")
                    if points is None or len(points) < 4:
                        state["age"] += 1
                        continue
                    next_points, status, error = cv2.calcOpticalFlowPyrLK(
                        previous_gray, gray, points, None,
                        winSize=(21, 21), maxLevel=3,
                        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 24, 0.02),
                    )
                    if next_points is None or status is None:
                        state["age"] += 1
                        continue
                    good = status.reshape(-1) == 1
                    if error is not None:
                        good &= error.reshape(-1) < 32.0
                    old_good = points.reshape(-1, 2)[good]
                    new_good = next_points.reshape(-1, 2)[good]
                    if len(new_good) < 5:
                        state["age"] += 1
                        continue
                    displacement = np.median(new_good - old_good, axis=0)
                    dx, dy = float(displacement[0]), float(displacement[1])
                    x, y, w, h = state["box"]
                    state["box"] = _clamp_box((x + dx, y + dy, w, h), small_width, small_height)
                    state["points"] = new_good.reshape(-1, 1, 2).astype(np.float32)
                    state["age"] = 0
                    tracked += 1
                    if len(new_good) < 26:
                        extra = _seed_points(gray, state["box"])
                        if extra is not None:
                            combined = np.concatenate([state["points"], extra], axis=0)
                            state["points"] = combined[:72]

            if anchor:
                for sid, normalized in anchor["subjects"].items():
                    x1, y1, x2, y2 = normalized
                    box = _clamp_box((x1 / 1000 * small_width, y1 / 1000 * small_height,
                                      (x2 - x1) / 1000 * small_width, (y2 - y1) / 1000 * small_height),
                                     small_width, small_height)
                    points = _seed_points(gray, box)
                    # A cloud anchor refreshes the drifted flow box. It is always
                    # preferred over extrapolated flow at that exact timestamp.
                    states[sid] = {"box": box, "points": points, "age": 0}

            # Retain a track briefly through an occlusion, then wait for a fresh
            # cloud anchor instead of following background pixels indefinitely.
            for sid in list(states):
                if states[sid]["age"] > max_track_age:
                    del states[sid]

            emit = (relative_t - previous_emit >= output_step - 0.025) or anchor is not None or shot_cut
            if emit:
                subjects_out = []
                for sid, state in states.items():
                    if state["age"] > max_track_age:
                        continue
                    x, y, w, h = state["box"]
                    subjects_out.append({
                        "id": sid,
                        "bbox": [
                            round(x / small_width * 1000, 2),
                            round(y / small_height * 1000, 2),
                            round((x + w) / small_width * 1000, 2),
                            round((y + h) / small_height * 1000, 2),
                        ],
                    })
                output_frames.append({"t": round(relative_t, 3), "shot_cut": bool(shot_cut), "subjects": subjects_out})
                previous_emit = relative_t
            previous_gray = gray
            sampled += 1
            if relative_t >= clip_duration:
                break

        if not output_frames or not any(frame["subjects"] for frame in output_frames):
            return plan, {"tracking": "anchor-only", "reason": "Optical flow could not maintain a stable subject track; cloud anchors were retained."}
        refined = dict(plan)
        refined["keyframes"] = output_frames
        refined["tracking_engine"] = "cloud-anchors+opencv-lucas-kanade"
        return refined, {
            "tracking": "refined",
            "engine": "cloud-anchors+opencv-lucas-kanade",
            "sample_fps": round(actual_sample_fps, 2),
            "output_keyframe_fps": round(1.0 / output_step, 2),
            "frames_sampled": sampled,
            "crop_keyframes": len(output_frames),
            "flow_updates": tracked,
            "scene_cuts": cuts,
            "subject_tracks": len({subject["id"] for item in output_frames for subject in item["subjects"]}),
        }
    finally:
        cap.release()
