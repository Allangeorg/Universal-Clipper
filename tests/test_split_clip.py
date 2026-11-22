from pathlib import Path

import pytest

from backend import database


def test_split_clip_replaces_one_record_with_two_parts(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.sqlite3")
    database.init_db()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"placeholder")
    database.create_job("job123", source, "source.mp4", "upload")
    database.add_clips("job123", [{
        "id": "clip12345678", "start": 10.0, "end": 40.0, "score": 8.4,
        "components": {"audio": 8.0}, "title": "Good moment", "reason": "signal peak",
        "transcript": "test words", "selected": True,
    }])
    ids = database.split_clip_record("job123", "clip12345678", 25.0)
    parts = database.get_clips("job123")
    assert len(ids) == 2
    assert len(parts) == 2
    assert [(part["start"], part["end"]) for part in parts] == [(10.0, 25.0), (25.0, 40.0)]
    assert [part["duration"] for part in parts] == [15.0, 15.0]
    assert all(part["selected"] for part in parts)


def test_split_clip_rejects_too_close_split_point(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.sqlite3")
    database.init_db()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"placeholder")
    database.create_job("job234", source, "source.mp4", "upload")
    database.add_clips("job234", [{
        "id": "clip23456789", "start": 10.0, "end": 20.0, "score": 7.0,
        "components": {}, "title": "Moment", "reason": "", "transcript": "", "selected": True,
    }])
    with pytest.raises(ValueError, match="at least 0.2 seconds"):
        database.split_clip_record("job234", "clip23456789", 10.1)
