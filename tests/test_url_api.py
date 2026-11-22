from __future__ import annotations

from fastapi.testclient import TestClient

from backend import database
from backend import main as app_module


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "url-test.sqlite3")
    monkeypatch.setattr(app_module.manager, "start", lambda: None)
    monkeypatch.setattr(app_module.manager, "enqueue", lambda job_id: None)
    return TestClient(app_module.app)


def test_url_api_persists_raw_url_as_text_not_path(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        response = client.post("/api/jobs/url", json={"url": "https://www.youtube.com/watch?v=AbCdEf12345"})
        assert response.status_code == 200, response.text
        job_id = response.json()["job_id"]
        job = database.get_job(job_id)
        assert job["source_path"] == "https://www.youtube.com/watch?v=AbCdEf12345"


def test_retry_repairs_url_saved_by_old_windows_build(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        job_id = "abc123def456"
        old_corrupted_url = r"https:\www.youtube.com\watch?v=AbCdEf12345"
        database.create_job(job_id, old_corrupted_url, "www.youtube.com", "url")
        database.update_job(job_id, status="failed", stage="failed", error="urlopen error no host given")
        response = client.post(f"/api/jobs/{job_id}/retry")
        assert response.status_code == 200, response.text
        job = database.get_job(job_id)
        assert job["source_path"] == "https://www.youtube.com/watch?v=AbCdEf12345"


def test_url_api_persists_custom_duration_and_balanced_9x16_requirements(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        response = client.post("/api/jobs/url", json={
            "url": "https://www.youtube.com/watch?v=AbCdEf12345",
            "duration_mode": "exact", "exact_seconds": 52,
            "editing_intelligence": "balanced", "aspect_ratio": "9:16",
        })
        assert response.status_code == 200, response.text
        job = database.get_job(response.json()["job_id"])
        assert job["options"]["clip_min_seconds"] == 52
        assert job["options"]["clip_max_seconds"] == 52
        assert job["options"]["aspect_ratio"] == "9:16"
        assert job["options"]["editing_intelligence"] == "balanced"


def test_upload_api_persists_custom_range_balanced_and_vertical_defaults(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/api/jobs/upload",
            files={"file": ("sample.mp4", b"not-a-real-video-but-uploaded", "video/mp4")},
            data={
                "duration_mode": "range", "min_seconds": "37", "max_seconds": "83",
                "exact_seconds": "45", "editing_intelligence": "balanced", "aspect_ratio": "9:16",
            },
        )
        assert response.status_code == 200, response.text
        job = database.get_job(response.json()["job_id"])
        assert job["options"]["clip_min_seconds"] == 37
        assert job["options"]["clip_max_seconds"] == 83
        assert job["options"]["editing_intelligence"] == "balanced"
        assert job["options"]["aspect_ratio"] == "9:16"
