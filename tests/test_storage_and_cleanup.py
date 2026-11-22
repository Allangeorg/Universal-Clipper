import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.main import app, _format_bytes
from backend import database
from backend.config import SOURCE_DIR, WORK_DIR, EXPORT_DIR


def test_format_bytes():
    assert _format_bytes(500) == "500 B"
    assert "KB" in _format_bytes(2048)
    assert "MB" in _format_bytes(5 * 1024 * 1024)
    assert "GB" in _format_bytes(2 * 1024 * 1024 * 1024)


def test_get_storage_api():
    client = TestClient(app)
    res = client.get("/api/storage")
    assert res.status_code == 200
    data = res.json()
    assert "sources_bytes" in data
    assert "sources_formatted" in data
    assert "work_bytes" in data
    assert "exports_bytes" in data
    assert "total_bytes" in data
    assert isinstance(data["jobs"], list)


def test_clear_job_source_and_delete_project(monkeypatch):
    test_id = "test_cleanup_01"
    # Create fake job in DB
    database.create_job(
        test_id,
        source_path=str(SOURCE_DIR / test_id / "source.mp4"),
        source_name="Cleanup Test Video",
        source_kind="upload",
        options={"duration_mode": "exact", "exact_seconds": 60},
    )

    # Create dummy source and work files
    job_src_dir = SOURCE_DIR / test_id
    job_src_dir.mkdir(parents=True, exist_ok=True)
    fake_video = job_src_dir / "source.mp4"
    fake_video.write_bytes(b"0" * 1024 * 100)  # 100 KB

    job_work_dir = WORK_DIR / test_id
    job_work_dir.mkdir(parents=True, exist_ok=True)
    (job_work_dir / "audio.wav").write_bytes(b"0" * 500)

    client = TestClient(app)

    # 1. Clear source specifically for this job
    clear_res = client.post(f"/api/jobs/{test_id}/clear-source")
    assert clear_res.status_code == 200
    assert clear_res.json()["status"] == "cleared"
    assert clear_res.json()["freed_bytes"] >= 1024 * 100
    assert not fake_video.exists()

    # 2. Check storage endpoint reflects the job
    storage_res = client.get("/api/storage")
    assert storage_res.status_code == 200

    # 3. Delete the project
    del_res = client.delete(f"/api/jobs/{test_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"

    # Verify deleted from DB
    assert database.get_job(test_id) is None
    assert not job_work_dir.exists()
