from __future__ import annotations

import numpy as np
import pytest

from backend.main import _build_job_options
from backend.pipeline.discover import _duration_targets, discover_candidates


def _signals(duration: int = 240) -> dict:
    scores = np.zeros(duration, dtype=np.float32)
    for second in (30, 82, 134, 186):
        if second + 3 <= duration:
            scores[second:second + 3] = [0.72, 1.0, 0.68]
    return {"score": scores.tolist()}


def test_custom_duration_range_generates_only_requested_lengths():
    clips = discover_candidates(
        240, _signals(), {"available": False, "seconds": [], "score": []}, None,
        min_duration=35, max_duration=75, editing_intelligence="balanced",
    )
    assert clips
    assert all(34.9 <= clip["duration"] <= 75.1 for clip in clips)
    assert {clip["editing_intelligence"] for clip in clips} == {"balanced"}


def test_exact_duration_mode_generates_requested_length():
    clips = discover_candidates(
        240, _signals(), {"available": False, "seconds": [], "score": []}, None,
        min_duration=47, max_duration=47, editing_intelligence="balanced",
    )
    assert clips
    assert all(abs(clip["duration"] - 47) < 0.01 for clip in clips)


def test_duration_target_sampling_uses_custom_range_or_exact_value():
    assert _duration_targets(40, 40) == [40]
    assert _duration_targets(20, 60) == [20, 30, 40, 50, 60]


def test_source_shorter_than_requested_minimum_does_not_violate_duration():
    clips = discover_candidates(
        24, _signals(24), {"available": False}, None,
        min_duration=30, max_duration=60,
    )
    assert clips == []


def test_balanced_9x16_job_options_are_persistable():
    options = _build_job_options("range", 45, 30, 60, "balanced", "9:16")
    assert options == {
        "duration_mode": "range", "clip_min_seconds": 30, "clip_max_seconds": 60,
        "exact_seconds": 45, "editing_intelligence": "balanced", "aspect_ratio": "9:16",
        "campaign_brief": "", "logo_path": "", "logo_position": "top-right",
        "logo_scale": 0.20, "logo_opacity": 0.90,
    }


def test_exact_custom_duration_becomes_equal_minimum_and_maximum():
    options = _build_job_options("exact", 53, 30, 60, "balanced", "9:16")
    assert options["clip_min_seconds"] == options["clip_max_seconds"] == 53


def test_invalid_duration_range_is_rejected():
    with pytest.raises(Exception):
        _build_job_options("range", 45, 70, 60, "balanced", "9:16")


def test_invalid_editing_intelligence_is_rejected():
    with pytest.raises(Exception):
        _build_job_options("range", 45, 30, 60, "random", "9:16")


def test_new_default_job_settings_are_exact_60_seconds_adaptive_framing():
    from backend.main import UrlJobRequest, ExportRequest
    url_request = UrlJobRequest(url="https://youtu.be/abcdefghijk")
    assert url_request.duration_mode == "exact"
    assert url_request.exact_seconds == 60
    export_request = ExportRequest()
    assert export_request.framing_mode == "adaptive"
    assert export_request.ai_edit is True
    assert export_request.crf == 16


def test_export_interval_is_fitted_to_exact_duration_contract():
    from backend.main import _fit_clip_interval
    start, end = _fit_clip_interval(40, 70, 300, "exact", 60, 60)
    assert end - start == pytest.approx(60.0)
    assert (start, end) == pytest.approx((25.0, 85.0))


def test_range_interval_is_extended_to_minimum_not_returned_short():
    from backend.main import _fit_clip_interval
    start, end = _fit_clip_interval(50, 70, 200, "range", 45, 90)
    assert end - start == pytest.approx(45.0)


def test_export_duration_contract_rejects_too_short_source():
    from backend.main import _fit_clip_interval
    assert _fit_clip_interval(0, 30, 45, "exact", 60, 60) is None
