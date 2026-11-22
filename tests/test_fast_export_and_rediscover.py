import pytest
from fastapi.testclient import TestClient
from backend.main import app, _build_job_options
from backend.pipeline.discover import discover_candidates
from backend.pipeline.editor import get_optimized_video_encoder
from backend import database


@pytest.fixture
def client():
    return TestClient(app)


def test_endpoint_autoswaps_min_greater_than_max(client):
    """Verify that entering min=120 and max=66 in rediscover auto-swaps to min=66, max=120."""
    from backend.config import WORK_DIR
    import json
    import uuid
    
    database.init_db()
    job_id = f"test_swap_{uuid.uuid4().hex[:8]}"
    work = WORK_DIR / job_id
    work.mkdir(parents=True, exist_ok=True)
    metadata = {"duration": 300.0, "width": 1920, "height": 1080, "has_audio": True}
    audio_data = {"seconds": list(range(300)), "score": [0.6] * 300}
    visual_data = {"available": False, "seconds": [], "score": [], "black_gaps": []}
    (work / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (work / "audio_features.json").write_text(json.dumps(audio_data), encoding="utf-8")
    (work / "visual_features.json").write_text(json.dumps(visual_data), encoding="utf-8")
    
    init_options = _build_job_options("exact", 60, 30, 60, "balanced", "9:16")
    database.create_job(job_id, str(work / "mock.mp4"), "mock.mp4", "upload", init_options)
    
    # Enter min=120 and max=66 (swapped bounds)
    resp = client.post(f"/api/jobs/{job_id}/rediscover", json={
        "duration_mode": "range",
        "exact_seconds": 60,
        "min_seconds": 120,
        "max_seconds": 66,
        "editing_intelligence": "balanced"
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["options"]["clip_min_seconds"] == 66
    assert data["options"]["clip_max_seconds"] == 120
    for clip in data["clips"]:
        assert 66 <= clip["duration"] <= 120


def test_discover_candidates_with_swapped_bounds():
    """Verify discover_candidates gracefully handles min_duration > max_duration."""
    duration = 300.0
    audio = {"seconds": list(range(300)), "score": [0.5] * 300}
    visual = {"available": False, "seconds": [], "score": []}
    candidates = discover_candidates(
        duration=duration,
        audio=audio,
        visual=visual,
        transcript=None,
        min_duration=120,
        max_duration=66,
        duration_mode="range",
    )
    assert len(candidates) > 0
    for c in candidates:
        assert 66 <= c["duration"] <= 120


def test_get_optimized_video_encoder_returns_valid_flags():
    """Verify get_optimized_video_encoder returns valid flags without errors."""
    flags = get_optimized_video_encoder(requested_preset="fast", requested_crf=18)
    assert "-c:v" in flags
    assert any(codec in flags for codec in ["h264_nvenc", "libx264"])
    assert "-profile:v" in flags
    assert "high" in flags
    assert "-pix_fmt" in flags
    assert "yuv420p" in flags


def test_rediscover_endpoint(client, tmp_path, monkeypatch):
    """Verify POST /api/jobs/{job_id}/rediscover re-calculates candidate clips with new durations."""
    from backend.config import WORK_DIR
    import json
    import uuid
    
    database.init_db()
    job_id = f"test_rediscover_{uuid.uuid4().hex[:8]}"
    work = WORK_DIR / job_id
    work.mkdir(parents=True, exist_ok=True)
    
    # Write mock analysis files
    metadata = {"duration": 200.0, "width": 1920, "height": 1080, "has_audio": True}
    audio_data = {"seconds": list(range(200)), "score": [0.6] * 200}
    visual_data = {"available": False, "seconds": [], "score": [], "black_gaps": []}
    (work / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (work / "audio_features.json").write_text(json.dumps(audio_data), encoding="utf-8")
    (work / "visual_features.json").write_text(json.dumps(visual_data), encoding="utf-8")
    
    init_options = _build_job_options("exact", 60, 30, 60, "balanced", "9:16")
    database.create_job(job_id, str(work / "mock.mp4"), "mock.mp4", "upload", init_options)
    
    # Call rediscover with min=45, max=90
    resp = client.post(f"/api/jobs/{job_id}/rediscover", json={
        "duration_mode": "range",
        "exact_seconds": 60,
        "min_seconds": 45,
        "max_seconds": 90,
        "editing_intelligence": "balanced"
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["job_id"] == job_id
    assert data["options"]["clip_min_seconds"] == 45
    assert data["options"]["clip_max_seconds"] == 90
    assert len(data["clips"]) > 0
    for clip in data["clips"]:
        assert 45 <= clip["duration"] <= 90
