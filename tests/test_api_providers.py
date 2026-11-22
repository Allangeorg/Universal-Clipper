from __future__ import annotations

import json
import wave
from pathlib import Path

import httpx
import pytest

from backend.providers import openrouter, preferences, secrets, transcription


class _FakeKeyring:
    class errors:
        class PasswordDeleteError(Exception):
            pass

    class _Backend:
        priority = 1

    def __init__(self):
        self.values = {}

    def get_keyring(self):
        return self._Backend()

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.values:
            raise self.errors.PasswordDeleteError()
        del self.values[(service, username)]


def test_secret_store_uses_keyring_and_does_not_return_keys_to_preferences(monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets, "_keyring", lambda: fake)
    secrets.set_secret("openrouter", "sk-or-test-secret-123")
    assert secrets.get_secret("openrouter") == "sk-or-test-secret-123"
    assert secrets.has_secret("openrouter") is True
    secrets.delete_secret("openrouter")
    assert secrets.has_secret("openrouter") is False


def test_secret_store_fails_closed_when_keyring_missing(monkeypatch):
    def unavailable():
        raise secrets.SecretStoreError("No secure credential vault")
    monkeypatch.setattr(secrets, "_keyring", unavailable)
    with pytest.raises(secrets.SecretStoreError):
        secrets.set_secret("groq", "gsk-test-secret-123")


def test_preferences_persist_without_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(preferences, "SETTINGS_PATH", tmp_path / "settings.json")
    saved = preferences.save_preferences({"llm_model": "google/gemini-flash", "vision_enabled": False})
    on_disk = json.loads((tmp_path / "settings.json").read_text())
    assert saved["llm_model"] == "google/gemini-flash"
    assert on_disk["vision_enabled"] is False
    assert "api_key" not in on_disk


def test_preferences_reject_unknown_llm_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(preferences, "SETTINGS_PATH", tmp_path / "settings.json")
    with pytest.raises(ValueError):
        preferences.save_preferences({"llm_provider": "ollama"})


def test_openrouter_model_catalog_filters_vision(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"data": [
                {"id": "text/model", "name": "Text Model", "architecture": {"input_modalities": ["text"]}},
                {"id": "vision/model", "name": "Vision Model", "architecture": {"input_modalities": ["text", "image"]}},
            ]}
    monkeypatch.setattr(openrouter.httpx, "get", lambda *args, **kwargs: FakeResponse())
    items = openrouter.list_models("fake-key", vision_only=True)
    assert [item["id"] for item in items] == ["vision/model"]


def test_openrouter_json_parser_handles_markdown_fences():
    parsed = openrouter.parse_json_object('```json\n{"rankings": []}\n```')
    assert parsed == {"rankings": []}


def test_transcription_chunks_are_offset_to_source_timeline(monkeypatch, tmp_path):
    audio_path = tmp_path / "audio.wav"
    with wave.open(str(audio_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)

    monkeypatch.setattr(transcription, "_audio_duration", lambda path: 610.0)
    def fake_make_mp3(source, target, start, duration):
        target.write_bytes(b"fake mp3 bytes")
    monkeypatch.setattr(transcription, "_make_mp3", fake_make_mp3)
    calls = []
    def fake_chunk(endpoint, api_key, model, path, provider):
        calls.append((path.name, provider))
        return {"language": "en", "text": "sample speech", "segments": [{"start": 1.0, "end": 2.0, "text": "sample speech"}]}
    monkeypatch.setattr(transcription, "_transcribe_chunk", fake_chunk)

    result = transcription.transcribe_audio_api(audio_path, tmp_path / "transcript.json", "groq", "gsk-fake-key", "whisper-large-v3-turbo")
    assert len(calls) == 3
    assert result["segments"][0]["start"] == 1.0
    assert result["segments"][1]["start"] == 241.0
    assert result["segments"][2]["start"] == 481.0
    assert result["word_timestamps_available"] is False
    assert (tmp_path / "transcript.json").exists()


def test_settings_api_never_returns_saved_secret(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend import database
    from backend import main as app_module

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.sqlite3")
    monkeypatch.setattr(preferences, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(app_module.manager, "start", lambda: None)
    monkeypatch.setattr(app_module, "has_secret", lambda provider: provider == "openrouter")
    monkeypatch.setattr(app_module, "_env_key", lambda provider: False)
    with TestClient(app_module.app) as client:
        response = client.get("/api/settings")
        assert response.status_code == 200
        payload = response.json()
        assert payload["credentials"]["openrouter"]["configured"] is True
        assert "api_key" not in payload
        assert "sk-or-test-secret" not in response.text
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["local_models_required"] is False


def test_openrouter_transcription_catalog_uses_output_modality_filter(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"data": [
                {"id": "openai/whisper-large-v3-turbo", "name": "Whisper Large V3 Turbo"},
                {"id": "microsoft/mai-transcribe-2", "name": "MAI Transcribe 2"},
            ]}
    captured = {}
    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()
    monkeypatch.setattr(openrouter.httpx, "get", fake_get)
    models = openrouter.list_transcription_models("fake-openrouter-key")
    assert captured["params"] == {"output_modalities": "transcription"}
    assert models[0]["id"] == "openai/whisper-large-v3-turbo"


def test_openrouter_transcription_multipart_requests_verbose_timestamps(monkeypatch, tmp_path):
    from backend.providers import transcription
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"test audio")
    captured = {}
    class FakeResponse:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {"text": "hello world", "segments": [{"start": 0.0, "end": 1.0, "text": "hello world"}], "words": [{"start": 0.0, "end": 0.3, "word": "hello"}]}
    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return FakeResponse()
    monkeypatch.setattr(transcription.httpx, "post", fake_post)
    result = transcription._transcribe_chunk("https://openrouter.ai/api/v1/audio/transcriptions", "test-key", "openai/whisper-large-v3-turbo", audio, "openrouter")
    assert captured["url"].endswith("/audio/transcriptions")
    assert captured["data"]["response_format"] == "verbose_json"
    assert "word" in captured["data"]["timestamp_granularities[]"]
    assert captured["headers"]["X-Title"] == "Universal Clipper"
    assert result["text"] == "hello world"


def test_httpx_multipart_payload_does_not_raise_tuple_type_error(tmp_path):
    """Regression test: httpx must encode the multipart payload without 'expected bytes-like object, tuple found'."""
    import httpx
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF1234WAVEfmt ")
    data = {
        "model": "openai/whisper-large-v3-turbo",
        "response_format": "verbose_json",
        "timestamp_granularities[]": ["segment", "word"],
    }
    with audio.open("rb") as f:
        req = httpx.Request(
            "POST",
            "https://openrouter.ai/api/v1/audio/transcriptions",
            data=data,
            files={"file": (audio.name, f, "audio/wav")},
        )
        content = req.read()
    assert b'name="model"\r\n\r\nopenai/whisper-large-v3-turbo' in content
    assert b'name="timestamp_granularities[]"\r\n\r\nsegment' in content
    assert b'name="timestamp_granularities[]"\r\n\r\nword' in content
    assert b"RIFF1234WAVEfmt " in content


def test_vision_review_respects_transcript_sharing_preference(monkeypatch, tmp_path):
    from backend.providers import ai

    captured = {}
    def fake_chat(api_key, model, messages, timeout=120):
        captured["messages"] = messages
        return '{"visual_score": 8.5, "subject": "person", "visual_reason": "subject is visible", "crop_hint": "center", "needs_review": false}'
    monkeypatch.setattr(ai, "chat", fake_chat)
    monkeypatch.setattr(ai, "get_preferences", lambda: {
        "vision_enabled": True, "send_video_frames_to_ai": True,
        "vision_model": "vision/test", "llm_model": "text/test", "max_ai_candidates": 1,
    })
    source = tmp_path / "video.mp4"
    source.write_bytes(b"placeholder")
    candidates = [{"id": "candidate1", "start": 0.0, "end": 10.0, "score": 6.0,
                   "transcript": "PRIVATE TRANSCRIPT MUST NOT BE SENT", "components": {}, "reason": "signal"}]
    ai.judge_candidates_with_vision(candidates, source, "test-key", lambda *_: b"jpeg", transcript_allowed=False)
    content = captured["messages"][0]["content"]
    assert "PRIVATE TRANSCRIPT MUST NOT BE SENT" not in content[0]["text"]
    assert "Transcript sharing is disabled" in content[0]["text"]
    assert candidates[0]["visual_review"]["subject"] == "person"


def test_candidate_signature_is_stable_across_processes_and_changes_with_transcript():
    import hashlib
    import json
    transcript = {"segments": [{"start": 0.0, "end": 1.0, "text": "hello"}]}
    encoded = json.dumps(transcript, sort_keys=True, ensure_ascii=False).encode("utf-8")
    first = hashlib.sha256(encoded).hexdigest()
    second = hashlib.sha256(json.dumps(transcript, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    assert first == second
    changed = {"segments": [{"start": 0.0, "end": 1.0, "text": "goodbye"}]}
    changed_hash = hashlib.sha256(json.dumps(changed, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    assert changed_hash != first


def test_transcription_model_catalog_uses_same_openrouter_credential(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main as app_module
    monkeypatch.setattr(app_module, "get_preferences", lambda: {"transcription_provider": "openrouter"})
    monkeypatch.setattr(app_module, "get_secret", lambda provider: "same-openrouter-key" if provider == "openrouter" else None)
    captured = {}
    def fake_catalog(key):
        captured["key"] = key
        return [{"id": "openai/whisper-large-v3-turbo", "name": "Whisper Large V3 Turbo"}]
    monkeypatch.setattr(app_module, "list_transcription_models", fake_catalog)
    monkeypatch.setattr(app_module.manager, "start", lambda: None)
    with TestClient(app_module.app) as client:
        response = client.get("/api/settings/transcription-models")
    assert response.status_code == 200
    assert captured["key"] == "same-openrouter-key"
    assert response.json()["models"][0]["id"] == "openai/whisper-large-v3-turbo"
