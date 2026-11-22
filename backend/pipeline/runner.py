from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable

from backend import database
from backend.config import EXPORT_DIR, WORK_DIR
from backend.database import get_job
from backend.pipeline.audio import analyze_audio
from backend.pipeline.discover import apply_quality_penalties, discover_candidates
from backend.pipeline.downloader import download_video, normalize_video_url
from backend.pipeline.media import extract_audio, probe
from backend.pipeline.profiles import classify_content
from backend.pipeline.transcript import load_transcript
from backend.pipeline.visual import analyze_visual
from backend.pipeline.frames import extract_frame_jpeg
from backend.providers.ai import judge_candidates_with_vision, rerank_candidates
from backend.providers.preferences import get_preferences
from backend.providers.secrets import SecretStoreError, get_secret
from backend.providers.transcription import transcribe_audio_api

log = logging.getLogger(__name__)
Progress = Callable[[str, float, str], None]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def run_pipeline(job_id: str, progress: Progress) -> None:
    job = get_job(job_id)
    if not job:
        raise RuntimeError("Job not found in the database.")
    source = Path(job["source_path"])
    work = WORK_DIR / job_id
    work.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    job_options = job.get("options") or {}
    duration_mode = str(job_options.get("duration_mode", "exact")).lower()
    exact_seconds = int(job_options.get("exact_seconds", 60))
    if duration_mode == "exact":
        clip_min_seconds = int(job_options.get("clip_min_seconds") or job_options.get("min_seconds") or exact_seconds)
        clip_max_seconds = int(job_options.get("clip_max_seconds") or job_options.get("max_seconds") or exact_seconds)
    else:
        clip_min_seconds = int(job_options.get("clip_min_seconds") or job_options.get("min_seconds") or 30)
        clip_max_seconds = int(job_options.get("clip_max_seconds") or job_options.get("max_seconds") or 60)
        if clip_min_seconds > clip_max_seconds:
            clip_min_seconds, clip_max_seconds = clip_max_seconds, clip_min_seconds
    editing_intelligence = str(job_options.get("editing_intelligence", "balanced"))
    output_aspect_ratio = str(job_options.get("aspect_ratio", "9:16"))

    progress("prepare", 0.05, "Checking source media")
    if job["source_kind"] == "url":
        downloaded = _read_json(work / "download.json")
        if downloaded and Path(downloaded.get("path", "")).exists():
            source = Path(downloaded["path"])
        else:
            progress("download", 0.08, "Downloading source video with yt-dlp")
            from backend.config import SOURCE_DIR
            video_url = normalize_video_url(str(job["source_path"]))
            source, download_meta = download_video(video_url, SOURCE_DIR / job_id)
            _write_json(work / "download.json", {"path": str(source), "metadata": download_meta})
            database.update_job(job_id, source_name=download_meta.get("title") or job["source_name"])
    metadata = _read_json(work / "metadata.json")
    if not metadata:
        metadata = probe(source)
        _write_json(work / "metadata.json", metadata)
    database.update_job(job_id, duration=metadata["duration"])

    audio_path = work / "audio.wav"
    audio_data = _read_json(work / "audio_features.json")
    progress("audio", 0.15, "Analysing audio signals")
    if metadata["has_audio"]:
        if not audio_path.exists():
            extract_audio(source, audio_path)
        if (not audio_data or audio_data.get("analysis_version") != 1) and audio_path.exists():
            audio_data = analyze_audio(audio_path)
            audio_data["analysis_version"] = 1
            _write_json(work / "audio_features.json", audio_data)
    else:
        warnings.append("No audio stream was found; speech and audio event detection were skipped.")
        audio_data = {"seconds": [], "score": []}

    visual_data = _read_json(work / "visual_features.json")
    progress("visual", 0.32, "Scanning visual motion and scene changes")
    if not visual_data or visual_data.get("analysis_version") != 2 or "black_gaps" not in visual_data:
        visual_data = analyze_visual(source, metadata["duration"])
        _write_json(work / "visual_features.json", visual_data)
    black_gaps = visual_data.get("black_gaps", []) or []
    if black_gaps:
        warnings.append(f"Source contains {len(black_gaps)} full-black video section(s). Affected candidates are down-ranked and flagged for review.")

    preferences = get_preferences()
    transcript_path = work / "transcript.json"
    transcript = load_transcript(transcript_path)
    if not transcript and audio_path.exists() and preferences.get("transcription_enabled") and preferences.get("transcription_provider") != "disabled":
        provider = preferences["transcription_provider"]
        try:
            credential_provider = "openrouter" if provider == "openrouter" else provider
            api_key = get_secret(credential_provider)
            if not api_key:
                warnings.append(f"Add a {provider.title()} API key in Settings to enable cloud speech transcription.")
            else:
                def transcription_progress(value: float, message: str) -> None:
                    progress("transcribe", 0.42 + (0.15 * value), message)
                progress("transcribe", 0.42, f"Preparing audio for {provider.title()} transcription")
                transcript = transcribe_audio_api(audio_path, transcript_path, provider, api_key,
                                                  preferences["transcription_model"], transcription_progress)
        except Exception as exc:  # media-signal analysis remains useful without speech APIs
            warnings.append(f"Cloud transcription unavailable: {exc}")
            log.warning("Cloud transcription unavailable for %s: %s", job_id, exc)
    elif not transcript and not audio_path.exists():
        warnings.append("No audio track is available for speech transcription.")
    elif not transcript:
        warnings.append("Cloud transcription is disabled in Settings.")

    profile = classify_content(transcript)
    progress("discover", 0.62, f"Finding and ranking moments · profile: {profile['kind']}")
    clips_path = work / "candidates.json"
    candidate_meta_path = work / "candidate_meta.json"
    transcript_fingerprint = None
    if transcript:
        transcript_payload = json.dumps(transcript, sort_keys=True, ensure_ascii=False).encode("utf-8")
        transcript_fingerprint = hashlib.sha256(transcript_payload).hexdigest()
    candidate_signature = {
        "content_type": profile["kind"],
        "transcript_fingerprint": transcript_fingerprint,
        "discovery_version": 6,
        "duration_mode": job_options.get("duration_mode", "exact"),
        "exact_seconds": job_options.get("exact_seconds", clip_min_seconds),
        "clip_min_seconds": clip_min_seconds,
        "clip_max_seconds": clip_max_seconds,
        "editing_intelligence": editing_intelligence,
        "aspect_ratio": output_aspect_ratio,
        "campaign_brief": job_options.get("campaign_brief", ""),
        "black_gaps": black_gaps,
    }
    cached_candidate_meta = _read_json(candidate_meta_path)
    clips = _read_json(clips_path)
    if not isinstance(clips, list) or cached_candidate_meta != candidate_signature:
        clips = discover_candidates(
            metadata["duration"], audio_data, visual_data, transcript,
            min_duration=clip_min_seconds, max_duration=clip_max_seconds,
            content_type=profile["kind"], black_gaps=black_gaps,
            editing_intelligence=editing_intelligence,
            duration_mode=duration_mode, exact_seconds=exact_seconds,
        )
        _write_json(clips_path, clips)
        _write_json(candidate_meta_path, candidate_signature)

    # AI layers are separate artifacts. A user can add an API key later and retry
    # without repeating media preparation, audio analysis, visual scanning or transcription.
    ai_review_path = work / "ai_review.json"
    ai_signature = {
        "ai_pipeline_version": 2,
        "llm_model": preferences.get("llm_model"),
        "vision_model": preferences.get("vision_model"),
        "vision_enabled": preferences.get("vision_enabled"),
        "send_transcript_to_ai": preferences.get("send_transcript_to_ai"),
        "send_video_frames_to_ai": preferences.get("send_video_frames_to_ai"),
        "max_ai_candidates": preferences.get("max_ai_candidates"),
        "editing_intelligence": editing_intelligence,
        "clip_min_seconds": clip_min_seconds,
        "clip_max_seconds": clip_max_seconds,
        "campaign_brief": job_options.get("campaign_brief", ""),
        "black_gaps": black_gaps,
    }
    cached_ai = _read_json(ai_review_path)
    if isinstance(cached_ai, dict) and cached_ai.get("signature") == ai_signature and isinstance(cached_ai.get("clips"), list):
        clips = cached_ai["clips"]
    else:
        try:
            openrouter_key = get_secret("openrouter")
        except SecretStoreError as exc:
            openrouter_key = None
            warnings.append(str(exc))
        if openrouter_key:
            progress("ai_rank", 0.76, "Ranking candidates with OpenRouter")
            clips = rerank_candidates(
                clips, openrouter_key, transcript_allowed=bool(transcript),
                editing_intelligence=editing_intelligence,
                campaign_brief=str(job_options.get("campaign_brief", "") or ""),
            )
            if preferences.get("vision_enabled") and preferences.get("send_video_frames_to_ai"):
                progress("vision_review", 0.86, "Reviewing shortlisted frames with a vision model")
                clips = judge_candidates_with_vision(
                    clips, source, openrouter_key, extract_frame_jpeg,
                    transcript_allowed=bool(transcript) and preferences.get("send_transcript_to_ai", True),
                )
            ai_succeeded = any(item.get("ai_reviewed") for item in clips) or any(
                isinstance(item.get("visual_review"), dict) and not item["visual_review"].get("skipped")
                for item in clips
            )
            if ai_succeeded:
                _write_json(ai_review_path, {"signature": ai_signature, "clips": clips})
            else:
                warnings.append("The AI provider did not return a usable ranking or vision review. Signal-based candidate scores were retained; retry after checking provider settings.")
        else:
            warnings.append("OpenRouter is not configured. Candidates use local audio/visual signal scores; add a key in Settings for AI ranking and frame review.")
    clips = apply_quality_penalties(clips)
    database.add_clips(job_id, clips)

    ai_review_content = _read_json(ai_review_path) if ai_review_path.exists() else None
    summary = {
        "black_gaps": black_gaps,
        "metadata": metadata,
        "transcript_available": bool(transcript),
        "transcription_provider": (transcript or {}).get("provider"),
        "ai_ranking_available": bool(isinstance(ai_review_content, dict) and ai_review_content.get("signature") == ai_signature),
        "audio_analysis_available": bool(audio_data and audio_data.get("score")),
        "visual_analysis_available": bool(visual_data and visual_data.get("available")),
        "candidate_count": len(clips),
        "content_profile": profile,
        "clip_settings": {
            "duration_mode": job_options.get("duration_mode", "exact"),
            "exact_seconds": job_options.get("exact_seconds", clip_min_seconds),
            "min_seconds": clip_min_seconds, "max_seconds": clip_max_seconds,
            "editing_intelligence": editing_intelligence,
            "aspect_ratio": output_aspect_ratio,
        },
        "warnings": warnings,
        "work_directory": str(work),
        "export_directory": str(EXPORT_DIR / job_id),
    }
    _write_json(work / "summary.json", summary)
    if not clips and metadata["duration"] < clip_min_seconds:
        warnings.append(
            f"This source is {metadata['duration']:.1f}s long, shorter than your {clip_min_seconds}s minimum clip duration. "
            "Lower the minimum duration or upload a longer source."
        )
        summary["warnings"] = warnings
        _write_json(work / "summary.json", summary)
    progress("complete", 1.0, f"Analysis complete — {len(clips)} candidate clips found")
    database.update_job(job_id, status="completed", stage="complete", progress=1.0,
                        message=f"Analysis complete — {len(clips)} candidate clips found",
                        result_json=json.dumps(summary, ensure_ascii=False), error=None)
