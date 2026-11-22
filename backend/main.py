from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import database
from backend.config import BRANDING_DIR, DB_PATH, EXPORT_DIR, FFMPEG, FFPROBE, MAX_UPLOAD_MB, SOURCE_DIR, STATIC_DIR, WORK_DIR
from backend.jobs import manager
from backend.pipeline.downloader import normalize_video_url
from backend.pipeline.render import OUTPUTS
from backend.pipeline.editor import render_edited_clip
from backend.pipeline.frames import extract_frame_jpeg
from backend.pipeline.discover import discover_candidates, apply_quality_penalties
from backend.pipeline.profiles import classify_content
from backend.pipeline.transcript import load_transcript
from backend.providers.openrouter import ProviderError, list_models, list_transcription_models, test_connection
from backend.providers.ai import create_editorial_plan, interpret_editing_command, rerank_candidates
from backend.providers.preferences import get_preferences, save_preferences, get_effective_vision_model
from backend.providers.secrets import SecretStoreError, delete_secret, get_secret, has_secret, set_secret

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("universal-clipper")

@asynccontextmanager
async def lifespan(_: FastAPI):
    database.init_db()
    manager.start()
    yield


app = FastAPI(title="Universal Clipper", version="0.6.0", description="API-first AI video editing studio", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mts", ".m2ts"}


class UrlJobRequest(BaseModel):
    url: str = Field(min_length=8, max_length=3000)
    duration_mode: str = "exact"
    exact_seconds: int = Field(default=60, ge=5, le=600)
    min_seconds: int = Field(default=30, ge=5, le=600)
    max_seconds: int = Field(default=60, ge=5, le=600)
    editing_intelligence: str = "balanced"
    aspect_ratio: str = "9:16"
    campaign_brief: str = ""
    logo_path: str = ""
    logo_position: str = "top-right"
    logo_scale: float = Field(default=0.20, ge=0.05, le=0.50)
    logo_opacity: float = Field(default=0.90, ge=0.1, le=1.0)


class StorageClearRequest(BaseModel):
    target: str = "sources"  # "sources", "work", "all", "job_source"
    job_id: str | None = None



class SelectionRequest(BaseModel):
    selected: bool


class ExportRequest(BaseModel):
    aspect_ratio: str = "9:16"
    clip_ids: list[str] | None = None
    preserve_audio: bool = True
    framing_mode: str = "adaptive"
    normalize_audio: bool = True
    captions_enabled: bool = True
    ai_edit: bool = True
    caption_style: str = "karaoke"
    crf: int = Field(default=16, ge=14, le=30)
    preset: str = "fast"
    punch_in: bool = False
    campaign_brief: str = ""
    logo_path: str = ""
    logo_position: str = "top-right"
    logo_scale: float = Field(default=0.20, ge=0.05, le=0.50)
    logo_opacity: float = Field(default=0.90, ge=0.1, le=1.0)


class SplitClipRequest(BaseModel):
    split_at: float = Field(ge=0)


class RediscoverRequest(BaseModel):
    duration_mode: str = "exact"
    exact_seconds: int = Field(default=60, ge=5, le=600)
    min_seconds: int = Field(default=30, ge=5, le=600)
    max_seconds: int = Field(default=60, ge=5, le=600)
    editing_intelligence: str = "balanced"
    campaign_brief: str = ""
    logo_path: str = ""
    logo_position: str = "top-right"
    logo_scale: float = Field(default=0.20, ge=0.05, le=0.50)
    logo_opacity: float = Field(default=0.90, ge=0.1, le=1.0)


class StorageClearRequest(BaseModel):
    target: str = "sources"  # "sources", "work", "all", "job_source"
    job_id: str | None = None



class EditRenderRequest(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    aspect_ratio: str = "9:16"
    framing_mode: str = "adaptive"
    ai_reframe: bool = True
    focus_x: float = Field(default=0.5, ge=0, le=1)
    focus_y: float = Field(default=0.5, ge=0, le=1)
    zoom: float = Field(default=1.0, ge=1.0, le=2.5)
    smart_reframe: bool = True
    normalize_audio: bool = True
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: float = Field(default=1.0, ge=0, le=2.0)
    brightness: float = Field(default=0.0, ge=-0.25, le=0.25)
    contrast: float = Field(default=1.0, ge=0.5, le=2.0)
    saturation: float = Field(default=1.0, ge=0, le=2.0)
    fade_in: float = Field(default=0.0, ge=0, le=3.0)
    fade_out: float = Field(default=0.0, ge=0, le=3.0)
    preserve_audio: bool = True
    captions_enabled: bool = False
    caption_style: str = "bold"
    caption_position: str = "bottom"
    logo_path: str = ""
    logo_position: str = "top-right"
    logo_scale: float = Field(default=0.20, ge=0.05, le=0.50)
    logo_opacity: float = Field(default=0.90, ge=0.1, le=1.0)


class StorageClearRequest(BaseModel):
    target: str = "sources"  # "sources", "work", "all", "job_source"
    job_id: str | None = None

    font_size: int = Field(default=64, ge=24, le=110)
    text_colour: str = "#FFFFFF"
    outline_colour: str = "#000000"
    title_overlay: str = Field(default="", max_length=120)
    caption_text: str = Field(default="", max_length=4000)
    crf: int = Field(default=18, ge=14, le=30)
    preset: str = "fast"
    emphasis_words: list[str] = Field(default_factory=list, max_length=20)
    punch_in: bool = False


class AiAssistRequest(BaseModel):
    command: str = Field(min_length=2, max_length=500)
    current_settings: dict = Field(default_factory=dict)


class PreferencesRequest(BaseModel):
    llm_provider: str = "openrouter"
    llm_model: str = "openrouter/auto"
    vision_model: str = ""
    vision_enabled: bool = True
    transcription_provider: str = "openrouter"
    transcription_model: str = "openai/whisper-large-v3-turbo"
    transcription_enabled: bool = True
    max_ai_candidates: int = Field(default=12, ge=1, le=24)
    send_transcript_to_ai: bool = True
    send_video_frames_to_ai: bool = True


class ApiKeyRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=4096)


def _fit_clip_interval(start: float, end: float, source_duration: float,
                       duration_mode: str, minimum: float, maximum: float) -> tuple[float, float] | None:
    """Fit a legacy candidate to the job's saved duration contract.

    Exact mode always outputs the requested source interval. Range mode preserves
    an in-range candidate and extends/trims only to the nearest allowed bound.
    Returns None when the source cannot satisfy the contract.
    """
    source_duration = max(0.0, float(source_duration))
    current = max(0.0, float(end) - float(start))
    target = float(minimum) if duration_mode == "exact" else min(float(maximum), max(float(minimum), current))
    if source_duration + 0.01 < target or target <= 0:
        return None
    centre = (float(start) + float(end)) / 2.0
    fitted_start = max(0.0, min(max(0.0, source_duration - target), centre - target / 2.0))
    fitted_end = fitted_start + target
    if fitted_end > source_duration + 0.001 or abs((fitted_end - fitted_start) - target) > 0.001:
        return None
    return round(fitted_start, 3), round(fitted_end, 3)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_job_options(duration_mode: str, exact_seconds: int, min_seconds: int,
                       max_seconds: int, editing_intelligence: str, aspect_ratio: str,
                       campaign_brief: str = "", logo_path: str = "", logo_position: str = "top-right",
                       logo_scale: float = 0.20, logo_opacity: float = 0.90) -> dict:
    duration_mode = str(duration_mode or "range").lower()
    editing_intelligence = str(editing_intelligence or "balanced").lower()
    if duration_mode not in {"range", "exact"}:
        raise HTTPException(400, "Duration mode must be range or exact.")
    if editing_intelligence not in {"conservative", "balanced", "aggressive"}:
        raise HTTPException(400, "Editing intelligence must be conservative, balanced or aggressive.")
    if aspect_ratio not in OUTPUTS:
        raise HTTPException(400, "Unsupported output aspect ratio.")
    if duration_mode == "exact":
        minimum = maximum = int(exact_seconds)
    else:
        minimum, maximum = int(min_seconds), int(max_seconds)
        if minimum > maximum:
            raise HTTPException(400, "Minimum clip duration cannot exceed maximum clip duration.")
    if minimum < 5 or maximum > 600:
        raise HTTPException(400, "Clip duration must be between 5 and 600 seconds.")
    return {
        "duration_mode": duration_mode,
        "clip_min_seconds": minimum,
        "clip_max_seconds": maximum,
        "exact_seconds": int(exact_seconds),
        "editing_intelligence": editing_intelligence,
        "aspect_ratio": aspect_ratio,
        "campaign_brief": str(campaign_brief or "").strip(),
        "logo_path": str(logo_path or "").strip(),
        "logo_position": str(logo_position or "top-right").lower(),
        "logo_scale": float(logo_scale or 0.20),
        "logo_opacity": float(logo_opacity or 0.90),
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict:
    prefs = get_preferences()
    try:
        import yt_dlp
        yt_dlp_version = getattr(yt_dlp.version, "__version__", "installed")
        yt_dlp_available = True
    except Exception:
        yt_dlp_version = None
        yt_dlp_available = False
    js_runtime = "Deno" if shutil.which("deno") else "Node" if shutil.which("node") else None
    return {
        "status": "ok", "app": "Universal Clipper", "version": "0.6.0",
        "ffmpeg": bool(shutil.which(FFMPEG) or Path(FFMPEG).is_file()),
        "ffprobe": bool(shutil.which(FFPROBE) or Path(FFPROBE).is_file()),
        "yt_dlp_available": yt_dlp_available,
        "yt_dlp_version": yt_dlp_version,
        "youtube_js_runtime": js_runtime,
        "ai_provider": "OpenRouter",
        "openrouter_configured": has_secret("openrouter"),
        "vision_model_configured": bool(get_effective_vision_model() or prefs.get("vision_model")),
        "active_vision_model": get_effective_vision_model(),
        "transcription_provider": prefs.get("transcription_provider"),
        "transcription_configured": has_secret("openrouter" if prefs.get("transcription_provider") == "openrouter" else prefs.get("transcription_provider")) if prefs.get("transcription_provider") in {"openrouter", "groq", "openai"} else False,
        "local_models_required": False,
    }


@app.get("/api/settings")
def read_settings() -> dict:
    prefs = get_preferences()
    return {
        **prefs,
        "credentials": {
            "openrouter": {"configured": has_secret("openrouter"), "source": "environment" if _env_key("openrouter") else "os-vault" if has_secret("openrouter") else "none"},
            "groq": {"configured": has_secret("groq"), "source": "environment" if _env_key("groq") else "os-vault" if has_secret("groq") else "none"},
            "openai": {"configured": has_secret("openai"), "source": "environment" if _env_key("openai") else "os-vault" if has_secret("openai") else "none"},
        },
        "local_models_required": False,
    }


@app.put("/api/settings/preferences")
def update_preferences(request: PreferencesRequest) -> dict:
    try:
        saved = save_preferences(request.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"saved": True, **saved}


@app.put("/api/settings/keys/{provider}")
def save_api_key(provider: str, request: ApiKeyRequest) -> dict:
    try:
        set_secret(provider, request.api_key)
    except SecretStoreError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"saved": True, "provider": provider, "configured": True, "message": "Key saved in the operating-system credential vault."}


@app.delete("/api/settings/keys/{provider}")
def remove_api_key(provider: str) -> dict:
    try:
        delete_secret(provider)
    except SecretStoreError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"deleted": True, "provider": provider}


@app.post("/api/settings/test/{provider}")
def test_api_key(provider: str) -> dict:
    try:
        key = get_secret(provider)
        if not key:
            raise HTTPException(400, f"Add a {provider.title()} API key first.")
        if provider == "openrouter":
            return test_connection(key)
        if provider in {"groq", "openai"}:
            base = "https://api.groq.com/openai/v1" if provider == "groq" else "https://api.openai.com/v1"
            response = httpx.get(base + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
            if response.status_code >= 400:
                raise HTTPException(response.status_code, _provider_error(response))
            model_count = len(response.json().get("data", []))
            return {"ok": True, "provider": provider.title(), "message": f"Connected. {model_count} models are visible."}
        raise HTTPException(400, "Unsupported API provider.")
    except SecretStoreError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not reach {provider.title()}: {exc}") from exc


@app.get("/api/settings/models")
def available_models(vision: bool = False) -> dict:
    try:
        key = get_secret("openrouter")
        if not key:
            raise HTTPException(400, "Save an OpenRouter API key before loading models.")
        models = list_models(key, vision_only=vision)
        return {"provider": "OpenRouter", "vision_only": vision, "models": models[:250]}
    except SecretStoreError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/settings/transcription-models")
def transcription_models() -> dict:
    prefs = get_preferences()
    provider = prefs.get("transcription_provider", "openrouter")
    if provider == "disabled":
        return {"provider": "Disabled", "models": []}
    if provider == "openrouter":
        try:
            key = get_secret("openrouter")
            if not key:
                raise HTTPException(400, "Save your OpenRouter key first. The same key is used for chat, vision and transcription.")
            return {"provider": "OpenRouter", "models": list_transcription_models(key)}
        except SecretStoreError as exc:
            raise HTTPException(503, str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(502, str(exc)) from exc
    defaults = {
        "groq": [{"id": "whisper-large-v3-turbo", "name": "Whisper Large V3 Turbo"}, {"id": "whisper-large-v3", "name": "Whisper Large V3"}],
        "openai": [{"id": "gpt-transcribe", "name": "GPT Transcribe"}, {"id": "whisper-1", "name": "Whisper 1"}],
    }
    return {"provider": provider.title(), "models": defaults.get(provider, [])}


@app.post("/api/settings/test-transcription")
def test_transcription_connection() -> dict:
    provider = get_preferences().get("transcription_provider", "openrouter")
    if provider == "disabled":
        raise HTTPException(400, "Cloud transcription is disabled in Settings.")
    credential_provider = "openrouter" if provider == "openrouter" else provider
    try:
        key = get_secret(credential_provider)
        if not key:
            raise HTTPException(400, f"Add a {credential_provider.title()} API key first.")
        if provider == "openrouter":
            models = list_transcription_models(key)
            return {"ok": True, "provider": "OpenRouter speech-to-text", "message": f"Connected. {len(models)} transcription models are available with your OpenRouter key."}
        base = "https://api.groq.com/openai/v1" if provider == "groq" else "https://api.openai.com/v1"
        response = httpx.get(base + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
        if response.status_code >= 400:
            raise HTTPException(response.status_code, _provider_error(response))
        return {"ok": True, "provider": provider.title(), "message": f"Connected to {provider.title()} speech API."}
    except SecretStoreError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not reach {provider.title()}: {exc}") from exc


@app.get("/api/jobs")
def jobs() -> dict:
    return {"jobs": database.list_jobs()}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _resolve_job_source(job: dict) -> Path:
    source = Path(job["source_path"])
    if job["source_kind"] == "url":
        summary = job.get("result") or {}
        work_path = Path(summary.get("work_directory", ""))
        download_meta = work_path / "download.json"
        if download_meta.exists():
            import json
            try:
                source = Path(json.loads(download_meta.read_text(encoding="utf-8")).get("path", ""))
            except (OSError, ValueError):
                pass
    if not source.is_file():
        raise HTTPException(404, "The source media file is no longer available on this device.")
    return source


@app.get("/api/jobs/{job_id}/source")
def preview_source(job_id: str) -> FileResponse:
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(404, "Project not found")
    source = _resolve_job_source(job)
    return FileResponse(source, media_type="video/mp4", filename=source.name,
                        content_disposition_type="inline", headers={"Cache-Control": "no-store"})


@app.post("/api/jobs/{job_id}/clips/{clip_id}/render")
def render_clip_edit(job_id: str, clip_id: str, request: EditRenderRequest) -> dict:
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(404, "Project not found")
    clip = next((item for item in job["clips"] if item["id"] == clip_id), None)
    if not clip:
        raise HTTPException(404, "Clip not found in this project")
    if job["status"] != "completed":
        raise HTTPException(409, "Wait for analysis to finish before opening the editor")
    if request.aspect_ratio not in OUTPUTS:
        raise HTTPException(400, f"Aspect ratio must be one of: {', '.join(OUTPUTS)}")
    if request.framing_mode not in {"adaptive", "full_frame", "smart_crop", "manual_crop"}:
        raise HTTPException(400, "Choose AI adaptive, full-frame preservation, experimental crop, or manual crop.")
    if request.end <= request.start:
        raise HTTPException(400, "The clip end must be after its start.")
    source = _resolve_job_source(job)
    summary = job.get("result") or {}
    transcript = None
    work_dir = Path(summary.get("work_directory", ""))
    transcript_path = work_dir / "transcript.json"
    if transcript_path.is_file():
        import json
        try:
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            transcript = None
    settings = request.model_dump()
    job_opts = job.get("options") or {}
    if not settings.get("logo_path") and job_opts.get("logo_path"):
        settings["logo_path"] = job_opts["logo_path"]
        settings["logo_position"] = job_opts.get("logo_position", "top-right")
        settings["logo_scale"] = job_opts.get("logo_scale", 0.20)
        settings["logo_opacity"] = job_opts.get("logo_opacity", 0.90)
    if request.framing_mode == "adaptive" and request.ai_reframe:
        try:
            key = get_secret("openrouter")
            if key:
                edit_candidate = {"start": request.start, "end": request.end,
                                  "content_type": (summary.get("content_profile") or {}).get("kind", "general"),
                                  "transcript": " ".join(str(seg.get("text", "")) for seg in (transcript or {}).get("segments", []))[:3500]}
                plan = create_editorial_plan(edit_candidate, source, key, extract_frame_jpeg,
                                             transcript_allowed=bool(transcript) and get_preferences().get("send_transcript_to_ai", True))
                settings["framing_plan"] = plan.get("framing_plan")
                settings["ai_edit_plan"] = plan
                if request.captions_enabled and not request.title_overlay:
                    settings["title_overlay"] = plan.get("title", "")
                if request.captions_enabled:
                    settings["caption_style"] = plan.get("caption_style", settings.get("caption_style"))
                    settings["caption_position"] = plan.get("caption_position", settings.get("caption_position"))
                    settings["font_size"] = plan.get("font_size", settings.get("font_size"))
                    settings["emphasis_words"] = plan.get("emphasis_words", [])
            else:
                settings["framing_mode"] = "full_frame"
        except Exception as exc:
            log.warning("AI adaptive framing unavailable: %s", exc)
            settings["framing_mode"] = "full_frame"
            settings["ai_reframe_warning"] = str(exc)[:250]
    if request.preset not in {"ultrafast", "veryfast", "faster", "fast", "medium", "slow"}:
        settings["preset"] = "medium"
    out_dir = EXPORT_DIR / job_id / "edited"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{clip_id}_{uuid.uuid4().hex[:8]}.mp4"
    if request.captions_enabled and not transcript and not request.caption_text.strip():
        raise HTTPException(400, "No transcript is available. Enable cloud transcription and re-run analysis, or enter manual caption text in the editor.")
    try:
        result = render_edited_clip(source, target, request.start, request.end, settings, transcript)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    database.set_clip_output(clip_id, str(target))
    return {"clip_id": clip_id, "filename": target.name,
            "url": f"/api/exports/{job_id}/{target.relative_to(EXPORT_DIR / job_id).as_posix()}",
            "duration": result["duration"], "bytes": result["bytes"],
            "settings": result["settings"], "reframe": result.get("reframe", {}),
            "edit_plan": settings.get("ai_edit_plan"),
            "black_gaps": result.get("black_gaps", []),
            "warnings": result.get("warnings", []) + ([settings["ai_reframe_warning"]] if settings.get("ai_reframe_warning") else [])}


@app.post("/api/jobs/{job_id}/clips/{clip_id}/ai-assist")
def ai_assist_clip(job_id: str, clip_id: str, request: AiAssistRequest) -> dict:
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(404, "Project not found")
    clip = next((item for item in job["clips"] if item["id"] == clip_id), None)
    if not clip:
        raise HTTPException(404, "Clip not found")
    source_duration = float(job.get("duration") or 0.0)
    try:
        api_key = get_secret("openrouter")
    except SecretStoreError:
        api_key = None
    return interpret_editing_command(request.command, request.current_settings, clip, source_duration, api_key)


@app.post("/api/jobs/upload")
async def upload_video(
    file: UploadFile = File(...),
    duration_mode: str = Form("exact"),
    exact_seconds: int = Form(60),
    min_seconds: int = Form(30),
    max_seconds: int = Form(60),
    editing_intelligence: str = Form("balanced"),
    aspect_ratio: str = Form("9:16"),
    campaign_brief: str = Form(""),
    logo_path: str = Form(""),
    logo_position: str = Form("top-right"),
    logo_scale: float = Form(0.20),
    logo_opacity: float = Form(0.90),
) -> dict:
    if duration_mode == "range" and min_seconds > max_seconds:
        min_seconds, max_seconds = max_seconds, min_seconds
    options = _build_job_options(
        duration_mode, exact_seconds, min_seconds, max_seconds, editing_intelligence, aspect_ratio,
        campaign_brief=campaign_brief, logo_path=logo_path, logo_position=logo_position,
        logo_scale=logo_scale, logo_opacity=logo_opacity
    )
    original_name = Path(file.filename or "video.mp4").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file extension. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
    job_id = uuid.uuid4().hex[:12]
    folder = SOURCE_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"source{suffix}"
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    total = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, f"File exceeds the configured {MAX_UPLOAD_MB} MB upload limit")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    if total == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, "The uploaded file is empty")
    database.create_job(job_id, destination, original_name, "upload", options)
    manager.enqueue(job_id)
    return {"job_id": job_id, "status": "queued", "source_name": original_name}


@app.post("/api/jobs/url")
def create_url_job(request: UrlJobRequest) -> dict:
    try:
        video_url = normalize_video_url(request.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    parsed = urlparse(video_url)
    job_id = uuid.uuid4().hex[:12]
    # IMPORTANT: URLs are strings, never pathlib.Path objects. Path() rewrites
    # URL separators on Windows and was the cause of "urlopen error no host given".
    min_s, max_s = request.min_seconds, request.max_seconds
    if request.duration_mode == "range" and min_s > max_s:
        min_s, max_s = max_s, min_s
    options = _build_job_options(
        request.duration_mode, request.exact_seconds, min_s, max_s,
        request.editing_intelligence, request.aspect_ratio,
        campaign_brief=request.campaign_brief,
        logo_path=request.logo_path,
        logo_position=request.logo_position,
        logo_scale=request.logo_scale,
        logo_opacity=request.logo_opacity
    )
    database.create_job(job_id, video_url, parsed.netloc, "url", options)
    manager.enqueue(job_id)
    return {"job_id": job_id, "status": "queued", "source_name": parsed.netloc}


@app.post("/api/branding/logo")
async def upload_branding_logo(file: UploadFile = File(...)) -> dict:
    name = Path(file.filename or "logo.png").name
    suffix = Path(name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(400, "Supported logo formats: PNG, JPG, WEBP")
    dest = BRANDING_DIR / f"logo_{uuid.uuid4().hex[:8]}{suffix}"
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "Logo image must be under 10MB")
    dest.write_bytes(content)
    return {"status": "ok", "logo_path": str(dest), "filename": name, "logo_url": f"/api/branding/file/{dest.name}"}


@app.get("/api/branding/file/{filename}")
def get_branding_file(filename: str) -> FileResponse:
    target = (BRANDING_DIR / Path(filename).name).resolve()
    if not target.is_file():
        raise HTTPException(404, "Branding file not found")
    return FileResponse(target)


@app.post("/api/jobs/{job_id}/logo")
async def upload_job_logo(job_id: str, file: UploadFile = File(...)) -> dict:
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    name = Path(file.filename or "logo.png").name
    suffix = Path(name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(400, "Supported logo formats: PNG, JPG, WEBP")
    dest = BRANDING_DIR / f"{job_id}_logo{suffix}"
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "Logo image must be under 10MB")
    dest.write_bytes(content)
    opts = job.get("options") or {}
    opts["logo_path"] = str(dest)
    database.update_job(job_id, options=opts)
    return {"status": "ok", "logo_url": f"/api/jobs/{job_id}/logo", "filename": name}


@app.get("/api/jobs/{job_id}/logo")
def get_job_logo(job_id: str) -> FileResponse:
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    opts = job.get("options") or {}
    logo_path = opts.get("logo_path")
    if not logo_path or not Path(logo_path).is_file():
        raise HTTPException(404, "No logo uploaded for this job")
    return FileResponse(logo_path)


@app.delete("/api/jobs/{job_id}/logo")
def delete_job_logo(job_id: str) -> dict:
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    opts = job.get("options") or {}
    logo_path = opts.get("logo_path")
    if logo_path and Path(logo_path).is_file():
        Path(logo_path).unlink(missing_ok=True)
    opts["logo_path"] = ""
    database.update_job(job_id, options=opts)
    return {"status": "deleted"}


def _format_bytes(num_bytes: int | float) -> str:
    num = float(num_bytes)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(num) < 1024.0:
            return f"{num:.2f} {unit}" if unit in ('MB', 'GB', 'TB') else f"{int(num)} {unit}"
        num /= 1024.0
    return f"{num:.2f} PB"


@app.delete("/api/jobs/{job_id}")
def delete_project_endpoint(job_id: str) -> dict:
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(404, "Project not found")

    freed_bytes = 0
    for folder in (SOURCE_DIR / job_id, WORK_DIR / job_id, EXPORT_DIR / job_id):
        if folder.exists():
            freed_bytes += sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
            shutil.rmtree(folder, ignore_errors=True)

    if job.get("source_path"):
        try:
            sp = Path(job["source_path"])
            if sp.is_file() and (SOURCE_DIR in sp.parents or WORK_DIR in sp.parents):
                freed_bytes += sp.stat().st_size
                sp.unlink(missing_ok=True)
        except Exception:
            pass

    database.delete_job(job_id)
    return {"status": "deleted", "job_id": job_id, "freed_bytes": freed_bytes, "freed_formatted": _format_bytes(freed_bytes)}


@app.post("/api/jobs/{job_id}/clear-source")
def clear_job_source_endpoint(job_id: str) -> dict:
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(404, "Project not found")

    freed_bytes = 0
    jsrc_dir = SOURCE_DIR / job_id
    if jsrc_dir.exists():
        freed_bytes += sum(f.stat().st_size for f in jsrc_dir.rglob("*") if f.is_file())
        shutil.rmtree(jsrc_dir, ignore_errors=True)

    if job.get("source_path"):
        try:
            sp = Path(job["source_path"])
            if sp.is_file() and (SOURCE_DIR in sp.parents or WORK_DIR in sp.parents):
                freed_bytes += sp.stat().st_size
                sp.unlink(missing_ok=True)
        except Exception:
            pass

    summary = job.get("result") or {}
    work_path = Path(summary.get("work_directory", "")) if summary.get("work_directory") else (WORK_DIR / job_id)
    download_meta = work_path / "download.json"
    if download_meta.exists():
        try:
            meta_src = Path(json.loads(download_meta.read_text(encoding="utf-8")).get("path", ""))
            if meta_src.is_file():
                freed_bytes += meta_src.stat().st_size
                meta_src.unlink(missing_ok=True)
        except Exception:
            pass

    return {
        "status": "cleared",
        "job_id": job_id,
        "freed_bytes": freed_bytes,
        "freed_formatted": _format_bytes(freed_bytes),
    }


@app.get("/api/storage")
def get_storage_usage() -> dict:
    def _dir_size(p: Path) -> int:
        if not p.exists():
            return 0
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

    sources_bytes = _dir_size(SOURCE_DIR)
    work_bytes = _dir_size(WORK_DIR)
    exports_bytes = _dir_size(EXPORT_DIR)
    branding_bytes = _dir_size(BRANDING_DIR)
    db_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    total_bytes = sources_bytes + work_bytes + exports_bytes + branding_bytes + db_bytes

    jobs = database.list_jobs(limit=100)
    job_sources = []
    for j in jobs:
        jid = j["id"]
        jsrc_dir = SOURCE_DIR / jid
        size = 0
        if jsrc_dir.exists():
            size += sum(f.stat().st_size for f in jsrc_dir.rglob("*") if f.is_file())
        if j.get("source_path"):
            try:
                sp = Path(j["source_path"])
                if sp.is_file() and (SOURCE_DIR in sp.parents or WORK_DIR in sp.parents):
                    if not (jsrc_dir.exists() and sp.is_relative_to(jsrc_dir)):
                        size += sp.stat().st_size
            except Exception:
                pass

        job_sources.append({
            "job_id": jid,
            "source_name": j.get("source_name", "Untitled"),
            "status": j.get("status", "unknown"),
            "size_bytes": size,
            "size_formatted": _format_bytes(size),
            "has_source": size > 0,
        })

    return {
        "sources_bytes": sources_bytes,
        "sources_formatted": _format_bytes(sources_bytes),
        "work_bytes": work_bytes,
        "work_formatted": _format_bytes(work_bytes),
        "exports_bytes": exports_bytes,
        "exports_formatted": _format_bytes(exports_bytes),
        "branding_bytes": branding_bytes,
        "branding_formatted": _format_bytes(branding_bytes),
        "db_bytes": db_bytes,
        "total_bytes": total_bytes,
        "total_formatted": _format_bytes(total_bytes),
        "jobs": job_sources,
    }


@app.post("/api/storage/clear")
def clear_storage_endpoint(request: StorageClearRequest) -> dict:
    freed_bytes = 0

    if request.target in ("sources", "all"):
        if SOURCE_DIR.exists():
            for item in SOURCE_DIR.iterdir():
                try:
                    if item.is_file():
                        freed_bytes += item.stat().st_size
                        item.unlink(missing_ok=True)
                    elif item.is_dir():
                        freed_bytes += sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                        shutil.rmtree(item, ignore_errors=True)
                except Exception as exc:
                    log.warning("Failed to clear source item %s: %s", item, exc)

    if request.target in ("work", "all"):
        if WORK_DIR.exists():
            for item in WORK_DIR.iterdir():
                try:
                    if item.is_file():
                        freed_bytes += item.stat().st_size
                        item.unlink(missing_ok=True)
                    elif item.is_dir():
                        freed_bytes += sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                        shutil.rmtree(item, ignore_errors=True)
                except Exception as exc:
                    log.warning("Failed to clear work item %s: %s", item, exc)

    if request.target == "job_source" and request.job_id:
        jid = request.job_id
        jsrc_dir = SOURCE_DIR / jid
        if jsrc_dir.exists():
            freed_bytes += sum(f.stat().st_size for f in jsrc_dir.rglob("*") if f.is_file())
            shutil.rmtree(jsrc_dir, ignore_errors=True)
        job = database.get_job(jid)
        if job and job.get("source_path"):
            try:
                sp = Path(job["source_path"])
                if sp.is_file() and (SOURCE_DIR in sp.parents or WORK_DIR in sp.parents):
                    freed_bytes += sp.stat().st_size
                    sp.unlink(missing_ok=True)
            except Exception:
                pass

    return {
        "status": "success",
        "target": request.target,
        "freed_bytes": freed_bytes,
        "freed_formatted": _format_bytes(freed_bytes),
    }



@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict:
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] not in {"failed", "completed"}:
        raise HTTPException(409, "This job is already queued or processing")
    if job.get("source_kind") == "url":
        try:
            repaired_url = normalize_video_url(str(job.get("source_path", "")))
        except ValueError as exc:
            raise HTTPException(400, f"This saved URL cannot be repaired: {exc}. Create a new job and paste the YouTube link again.") from exc
        database.update_job(job_id, source_path=repaired_url)
    database.update_job(job_id, status="queued", stage="queued", progress=0, message="Retry queued", error=None)
    manager.enqueue(job_id)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/jobs/{job_id}/rediscover")
def rediscover_job_clips(job_id: str, request: RediscoverRequest) -> dict:
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    work = WORK_DIR / job_id
    if not work.exists():
        raise HTTPException(400, "Job analysis directory not found. Please re-run the job.")
    meta_path = work / "metadata.json"
    if not meta_path.exists():
        raise HTTPException(400, "Media metadata not found for this job.")
    metadata = _read_json(meta_path)
    if not metadata or not metadata.get("duration"):
        raise HTTPException(400, "Valid media duration not found for this job.")

    current_options = job.get("options") or {}
    aspect_ratio = current_options.get("aspect_ratio", "9:16")
    min_s, max_s = request.min_seconds, request.max_seconds
    if request.duration_mode == "range" and min_s > max_s:
        min_s, max_s = max_s, min_s
    brief = request.campaign_brief or current_options.get("campaign_brief", "")
    logo_path = request.logo_path or current_options.get("logo_path", "")
    logo_pos = request.logo_position or current_options.get("logo_position", "top-right")
    logo_scale = request.logo_scale or current_options.get("logo_scale", 0.20)
    logo_opacity = request.logo_opacity or current_options.get("logo_opacity", 0.90)
    new_options = _build_job_options(
        request.duration_mode, request.exact_seconds,
        min_s, max_s,
        request.editing_intelligence, aspect_ratio,
        campaign_brief=brief,
        logo_path=logo_path,
        logo_position=logo_pos,
        logo_scale=logo_scale,
        logo_opacity=logo_opacity,
    )

    audio_data = _read_json(work / "audio_features.json")
    visual_data = _read_json(work / "visual_features.json")
    transcript = load_transcript(work / "transcript.json")
    black_gaps = (visual_data or {}).get("black_gaps", []) or []

    profile = classify_content(transcript)
    clips = discover_candidates(
        float(metadata["duration"]), audio_data, visual_data, transcript,
        min_duration=new_options["clip_min_seconds"],
        max_duration=new_options["clip_max_seconds"],
        content_type=profile["kind"],
        black_gaps=black_gaps,
        editing_intelligence=new_options["editing_intelligence"],
        duration_mode=new_options["duration_mode"],
        exact_seconds=new_options["exact_seconds"],
    )

    try:
        openrouter_key = get_secret("openrouter")
    except SecretStoreError:
        openrouter_key = None
    if openrouter_key:
        clips = rerank_candidates(
            clips, openrouter_key, transcript_allowed=bool(transcript),
            editing_intelligence=new_options["editing_intelligence"],
            campaign_brief=new_options.get("campaign_brief", ""),
        )
    clips = apply_quality_penalties(clips)

    _write_json(work / "candidates.json", clips)
    candidate_signature = {
        "content_type": profile["kind"],
        "discovery_version": 8,
        "duration_mode": new_options["duration_mode"],
        "exact_seconds": new_options["exact_seconds"],
        "clip_min_seconds": new_options["clip_min_seconds"],
        "clip_max_seconds": new_options["clip_max_seconds"],
        "editing_intelligence": new_options["editing_intelligence"],
        "campaign_brief": new_options.get("campaign_brief", ""),
        "aspect_ratio": aspect_ratio,
        "black_gaps": black_gaps,
    }
    _write_json(work / "candidate_meta.json", candidate_signature)

    database.update_job(job_id, options=new_options)
    database.add_clips(job_id, clips)

    return {
        "job_id": job_id,
        "clips": database.get_clips(job_id),
        "options": new_options,
        "count": len(clips),
    }


@app.patch("/api/jobs/{job_id}/clips/{clip_id}")
def update_selection(job_id: str, clip_id: str, request: SelectionRequest) -> dict:
    if not database.set_clip_selection(job_id, clip_id, request.selected):
        raise HTTPException(404, "Clip not found")
    return {"clip_id": clip_id, "selected": request.selected}


@app.post("/api/jobs/{job_id}/clips/{clip_id}/split")
def split_clip(job_id: str, clip_id: str, request: SplitClipRequest) -> dict:
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(404, "Project not found")
    if not any(item["id"] == clip_id for item in job["clips"]):
        raise HTTPException(404, "Clip not found")
    try:
        new_ids = database.split_clip_record(job_id, clip_id, request.split_at)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"split": True, "clip_ids": new_ids}


@app.post("/api/jobs/{job_id}/export")
def export_clips(job_id: str, request: ExportRequest) -> dict:
    if request.aspect_ratio not in OUTPUTS:
        raise HTTPException(400, f"Aspect ratio must be one of: {', '.join(OUTPUTS)}")
    if request.framing_mode not in {"adaptive", "full_frame", "smart_crop", "manual_crop"}:
        raise HTTPException(400, "Choose AI adaptive, full-frame preservation, experimental crop, or manual crop.")
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "completed":
        raise HTTPException(409, "Wait for analysis to finish before exporting")
    source = _resolve_job_source(job)
    clips = [clip for clip in job["clips"] if clip["selected"] and (request.clip_ids is None or clip["id"] in request.clip_ids)]
    if not clips:
        raise HTTPException(400, "No clips are selected for export")
    out_dir = EXPORT_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = job.get("result") or {}
    work_dir = Path(summary.get("work_directory", "")) if summary.get("work_directory") else (WORK_DIR / job_id)
    transcript = None
    transcript_path = work_dir / "transcript.json"
    if transcript_path.is_file():
        import json
        try:
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            transcript = None
    rendered = []
    try:
        openrouter_key = get_secret("openrouter") if request.ai_edit else None
    except SecretStoreError:
        openrouter_key = None
    prefs = get_preferences()
    job_options = job.get("options") or {}
    duration_mode = str(job_options.get("duration_mode", "exact"))
    min_len = float(job_options.get("clip_min_seconds", job_options.get("exact_seconds", 60)))
    max_len = float(job_options.get("clip_max_seconds", min_len))
    source_duration = float(job.get("duration") or summary.get("metadata", {}).get("duration", 0) or 0)
    brief = request.campaign_brief or job_options.get("campaign_brief", "")
    logo_path = request.logo_path or job_options.get("logo_path", "")
    logo_pos = request.logo_position or job_options.get("logo_position", "top-right")
    logo_scale = request.logo_scale or job_options.get("logo_scale", 0.20)
    logo_opacity = request.logo_opacity or job_options.get("logo_opacity", 0.90)
    for index, clip in enumerate(clips, start=1):
        clip_id = clip["id"]
        clip = dict(clip)
        target = out_dir / f"{index:02d}_{clip_id}_{request.aspect_ratio.replace(':', 'x')}.mp4"
        # Reconcile legacy candidates to the job's saved duration contract. This
        # repairs stale 30s candidates from earlier builds when the project was
        # explicitly configured for a longer exact duration.
        fitted = _fit_clip_interval(float(clip["start"]), float(clip["end"]), source_duration,
                                    duration_mode, min_len, max_len)
        if fitted is None:
            contract_desc = f"exact {min_len:.1f}s" if duration_mode == "exact" else f"{min_len:.1f}–{max_len:.1f}s"
            rendered.append({"clip_id": clip_id, "error": f"Source is only {source_duration:.1f}s; it cannot satisfy the {duration_mode} duration requirement ({contract_desc})."})
            continue
        clip["start"], clip["end"] = fitted
        clip["duration"] = round(clip["end"] - clip["start"], 3)
        plan = None
        plan_warnings: list[str] = []
        if request.ai_edit and openrouter_key:
            plan_file = work_dir / f"plan_{clip_id}.json"
            if plan_file.exists():
                try:
                    plan = _read_json(plan_file)
                except Exception:
                    plan = None
            if not plan:
                try:
                    plan = create_editorial_plan(
                        clip, source, openrouter_key, extract_frame_jpeg,
                        transcript_allowed=bool(transcript) and prefs.get("send_transcript_to_ai", True),
                        campaign_brief=brief,
                    )
                    if plan:
                        _write_json(plan_file, plan)
                except Exception as exc:
                    plan_warnings.append(f"AI edit plan unavailable; standard export used: {exc}")
        elif request.ai_edit:
            plan_warnings.append("OpenRouter is not configured; export uses your selected framing and caption defaults.")
        captions_enabled = request.captions_enabled and bool(transcript)
        if plan and plan.get("burned_captions_visible"):
            captions_enabled = False
            plan_warnings.append("Existing captions appear to be burned into the source; duplicate captions were avoided.")
        treatment = (plan or {}).get("visual_treatment", "natural")
        grade = {
            "natural": (0.0, 1.0, 1.0),
            "cinematic": (-0.01, 1.08, 0.92),
            "vivid": (0.0, 1.03, 1.10),
            "muted": (0.0, 1.02, 0.84),
        }.get(treatment, (0.0, 1.0, 1.0))
        settings = {
            "aspect_ratio": request.aspect_ratio, "preserve_audio": request.preserve_audio,
            "framing_mode": (("adaptive" if plan and plan.get("framing_plan") else "full_frame")
                             if request.framing_mode == "adaptive" else request.framing_mode),
            "framing_plan": (plan or {}).get("framing_plan"), "normalize_audio": request.normalize_audio,
            "captions_enabled": captions_enabled,
            "caption_style": (plan or {}).get("caption_style", request.caption_style),
            "caption_position": (plan or {}).get("caption_position", "bottom"),
            "font_size": (plan or {}).get("font_size", 68),
            "text_colour": "#FFFFFF", "outline_colour": "#000000",
            "title_overlay": (plan or {}).get("callout_overlay") or (plan or {}).get("title", ""),
            "emphasis_words": (plan or {}).get("emphasis_words", []),
            "focus_x": 0.5, "focus_y": 0.5, "zoom": 1.0, "speed": 1.0,
            "volume": 1.0, "brightness": grade[0], "contrast": grade[1], "saturation": grade[2],
            "fade_in": (plan or {}).get("fade_in", 0.12), "fade_out": (plan or {}).get("fade_out", 0.12),
            "crf": request.crf, "preset": request.preset, "punch_in": bool(request.punch_in),
            "logo_path": logo_path, "logo_position": logo_pos,
            "logo_scale": logo_scale, "logo_opacity": logo_opacity,
        }
        try:
            result = render_edited_clip(source, target, clip["start"], clip["end"], settings, transcript)
        except Exception as exc:
            rendered.append({"clip_id": clip_id, "error": str(exc)})
            continue
        database.set_clip_output(clip_id, str(target))
        plan_path = target.with_suffix(".edit-plan.json")
        if plan:
            plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        rendered.append({"clip_id": clip_id, "title": clip["title"], "filename": target.name,
                         "url": f"/api/exports/{job_id}/{target.name}", "duration": result["duration"],
                         "requested_duration": round(float(clip["end"]) - float(clip["start"]), 3),
                         "duration_contract": {"mode": duration_mode, "minimum": min_len, "maximum": max_len},
                         "bytes": result["bytes"], "width": result.get("width"), "height": result.get("height"),
                         "video_bitrate": result.get("video_bitrate"), "framing_mode": request.framing_mode,
                         "edit_plan": plan, "warnings": plan_warnings + result.get("warnings", []),
                         "reframe": result.get("reframe", {}), "black_gaps": result.get("black_gaps", []),
                         "social_caption": (plan or {}).get("social_caption") or clip.get("social_caption", "")})
    return {"job_id": job_id, "aspect_ratio": request.aspect_ratio, "framing_mode": request.framing_mode,
            "rendered": rendered,
            "success_count": sum(1 for item in rendered if "url" in item),
            "captions_enabled": request.captions_enabled and bool(transcript),
            "captions_available": bool(transcript), "ai_edit_enabled": request.ai_edit,
            "ai_edit_available": bool(openrouter_key)}


@app.get("/api/exports/{job_id}/{filename:path}")
def get_export(job_id: str, filename: str) -> FileResponse:
    if not re.fullmatch(r"[a-f0-9]{12}", job_id):
        raise HTTPException(404, "Export not found")
    target = (EXPORT_DIR / job_id / filename).resolve()
    allowed_root = (EXPORT_DIR / job_id).resolve()
    try:
        target.relative_to(allowed_root)
    except ValueError:
        raise HTTPException(404, "Export not found")
    if not target.is_file() or target.suffix.lower() != ".mp4":
        raise HTTPException(404, "Export not found")
    return FileResponse(target, media_type="video/mp4", filename=target.name)


@app.get("/api/jobs/{job_id}/transcript")
def get_transcript(job_id: str) -> dict:
    job = database.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    summary = job.get("result") or {}
    work_dir = Path(summary.get("work_directory", ""))
    path = work_dir / "transcript.json"
    if not path.exists():
        raise HTTPException(404, "No API transcript is available. Configure cloud speech transcription in Settings and retry the job.")
    import json
    return json.loads(path.read_text(encoding="utf-8"))


def _env_key(provider: str) -> bool:
    import os
    env_name = {"openrouter": "OPENROUTER_API_KEY", "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider, "")
    return bool(os.getenv(env_name)) if env_name else False


def _provider_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error", payload)
        return str(error.get("message") if isinstance(error, dict) else error)[:800]
    except ValueError:
        return (response.text or "Provider request failed")[:800]
