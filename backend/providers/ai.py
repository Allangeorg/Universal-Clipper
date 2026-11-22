from __future__ import annotations

import base64
import json
import logging
import math
import re
from pathlib import Path
from typing import Any

from backend.providers.openrouter import ProviderError, chat, parse_json_object
from backend.providers.preferences import get_preferences

log = logging.getLogger(__name__)


def _load_skill(name: str) -> str:
    path = Path(__file__).resolve().parent.parent / "skills" / name
    try:
        return path.read_text(encoding="utf-8")[:7000]
    except OSError:
        return ""


def rerank_candidates(candidates: list[dict[str, Any]], api_key: str | None = None,
                      transcript_allowed: bool = True, editing_intelligence: str = "balanced",
                      campaign_brief: str = "") -> list[dict[str, Any]]:
    """Use OpenRouter to improve heuristic ranks; preserve heuristic fallback."""
    if not candidates or not api_key:
        return candidates
    prefs = get_preferences()
    if not transcript_allowed or not prefs.get("send_transcript_to_ai", True):
        # Audio/visual evidence remains available, but semantic ranking needs text.
        return candidates
    compact = []
    for item in candidates[:max(1, int(prefs.get("max_ai_candidates", 12)))]:
        text = str(item.get("transcript", ""))
        first_words = " ".join(text.split()[:14])
        compact.append({
            "id": item["id"],
            "start": round(item["start"], 2),
            "end": round(item["end"], 2),
            "duration": round(item.get("duration", item["end"] - item["start"]), 2),
            "opening_hook_words": first_words,
            "text": text[:1100],
            "signal_scores": item.get("components", {}),
            "content_type": item.get("content_type", "general"),
        })
    intelligence = editing_intelligence if editing_intelligence in {"conservative", "balanced", "aggressive"} else "balanced"
    style_guidance = {
        "conservative": "Prefer complete, coherent moments with enough context. Avoid abrupt starts, excessive cuts and hype without payoff.",
        "balanced": "Balance a strong hook in the first 2 seconds, a complete payoff, visual/audio energy, context independence and natural pacing. Do not overvalue mere loudness.",
        "aggressive": "Prefer immediate viral hooks (seconds 0-2), high-stakes curiosity gaps, surprising turns, strong reactions and visually dynamic moments, while still requiring a comprehensible payoff.",
    }[intelligence]
    brief_directive = ""
    if str(campaign_brief or "").strip():
        brief_directive = (
            f"\n\nCAMPAIGN BRIEF & CLIENT REQUIREMENTS:\n{campaign_brief.strip()}\n"
            "Prioritize moments that best satisfy this specific campaign brief (e.g. strong initial hooks within the first 1-2 seconds, "
            "designated creator/gameplay themes, entertaining reactions, high retention potential). For each candidate, also evaluate 'brief_alignment' (0-10) "
            "and craft an engaging ready-to-post 'social_caption' including mandatory hashtags or calls to action from the brief."
        )
    context_skill = _load_skill("CONTEXT_DIRECTOR.md")
    editorial_skill = _load_skill("EDITORIAL_DIRECTOR.md")
    viral_playbook = _load_skill("VIRAL_PLAYBOOK.md")
    viral_directives = (
        "\nVIRAL RETENTION CRITERIA (TIKTOK / REELS / SHORTS):\n"
        "1. THE 2-SECOND RETENTION HOOK: Evaluate 'opening_hook_words'. Does the clip open immediately on an intriguing premise, shocking fact, bold question, or intense moment? "
        "If the clip begins with throat-clearing, filler chatter ('so basically', 'um', 'like'), or boring small talk, grade 'hook' below 4.0 and penalize overall score. "
        "If it begins with a strong curiosity gap, high energy, or unexpected statement, score 'hook' 8.5 to 10.0.\n"
        "2. NARRATIVE COMPLETENESS & PAYOFF: Does the clip deliver a satisfying resolution, punchline, victory, or epiphany by the end? Never reward clips cut off mid-thought.\n"
        "3. VIRAL RETENTION OVERALL SCORE (0-10): Predict viewer completion rate. Boring, flat, repetitive, or monotone clips MUST be scored below 5.0. "
        "High-energy, hilarious, shocking, or educational masterclasses receive 8.5-10.0.\n"
        "4. CALLOUT OVERLAY: Generate a punchy 3-5 word uppercase text banner (e.g. 'HE DID NOT EXPECT THIS 💀', 'THE $10,000 CODING HACK') to hook mute viewers immediately."
    )
    prompt = (
        viral_playbook + "\n" + editorial_skill + "\n" + context_skill + viral_directives + "\nYou are an elite short-form video editor and viral retention director. Evaluate these candidate moments with deep context comprehension. "
        + style_guidance + brief_directive + " "
        "Reward specificity, emotion, viral hook retention, and clear standalone story value. Do not invent missing facts. The timestamps and ids are immutable. "
        "Return ONLY JSON: {\"rankings\":[{\"id\":\"candidate id\",\"score\":0,\"reason\":\"specific short reason\",\"title\":\"concise engaging title\",\"narrative_role\":\"Hook & Climax|Core Insight|Explosive Reaction|Story Arc\",\"context_synopsis\":\"1-sentence premise\",\"hook\":0,\"payoff\":0,\"context\":0,\"emotion\":0,\"completeness\":0,\"standalone_viability\":0,\"brief_alignment\":0,\"callout_overlay\":\"3-5 word hook banner\",\"social_caption\":\"...\",\"punch_in_recommended\":false}]}"
        " Scores must be numeric from 0 to 10. Candidates: " + json.dumps(compact, ensure_ascii=False)
    )
    try:
        content = chat(api_key, prefs["llm_model"], [{"role": "user", "content": prompt}])
        parsed = parse_json_object(content)
        rankings = {str(item.get("id")): item for item in parsed.get("rankings", []) if isinstance(item, dict) and item.get("id")}
        for candidate in candidates:
            item = rankings.get(candidate["id"])
            if not item:
                continue
            ai_score = _clamp(item.get("score", candidate["score"]))
            local_score = float(candidate.get("score", 0))
            candidate.setdefault("components", {})["ai_semantic"] = round(ai_score, 2)
            if item.get("brief_alignment") is not None:
                b_align = _clamp(item.get("brief_alignment", 0))
                candidate["components"]["brief_alignment"] = round(b_align, 2)
                ai_score = ai_score * 0.7 + b_align * 0.3
            if item.get("standalone_viability") is not None:
                s_viab = _clamp(item.get("standalone_viability", 0))
                candidate["components"]["standalone_viability"] = round(s_viab, 2)
                ai_score = ai_score * 0.8 + s_viab * 0.2
            if item.get("title"):
                candidate["title"] = str(item["title"]).strip()[:80]
            if item.get("narrative_role"):
                candidate["narrative_role"] = str(item["narrative_role"]).strip()
            if item.get("context_synopsis"):
                candidate["context_synopsis"] = str(item["context_synopsis"]).strip()
            if item.get("callout_overlay"):
                candidate["callout_overlay"] = str(item["callout_overlay"]).strip()
            if item.get("social_caption"):
                candidate["social_caption"] = str(item["social_caption"]).strip()
            # Give AI semantic understanding 70% weight (vs 30% local volume/motion heuristic)
            ai_weight = 0.70 if intelligence in {"balanced", "aggressive"} else 0.52
            blended = round(local_score * (1.0 - ai_weight) + ai_score * ai_weight, 2)
            # Retention bonuses for strong viral hooks and high standalone value
            hook_val = _clamp(item.get("hook", 0))
            viab_val = _clamp(item.get("standalone_viability", 0))
            if hook_val >= 8.0:
                blended = min(10.0, blended + 0.4)
            if viab_val >= 8.5:
                blended = min(10.0, blended + 0.3)
            candidate["score"] = round(blended, 2)
            sub_scores = {key: _clamp(item.get(key, 0)) for key in ("hook", "payoff", "context", "emotion", "completeness", "standalone_viability")}
            candidate["components"].update({f"ai_{key}": round(value, 2) for key, value in sub_scores.items() if value > 0})
            reason = str(item.get("reason", "")).strip()
            if reason:
                candidate["reason"] = (candidate.get("reason", "") + "; AI: " + reason)[:600]
            candidate["ai_reviewed"] = True
    except Exception as exc:
        log.warning("OpenRouter reranking unavailable; keeping signal scores: %s", exc)
        for candidate in candidates:
            candidate.setdefault("ai_reviewed", False)
    return sorted(candidates, key=lambda item: item.get("score", 0), reverse=True)


def judge_candidates_with_vision(candidates: list[dict[str, Any]], source: Path,
                                 api_key: str | None, frame_extractor,
                                 transcript_allowed: bool = True) -> list[dict[str, Any]]:
    """Review sampled stills and respect the transcript-sharing preference."""
    prefs = get_preferences()
    if not (api_key and prefs.get("vision_enabled") and prefs.get("send_video_frames_to_ai")):
        return candidates
    model = str(prefs.get("vision_model") or "").strip()
    if not model or model == "auto":
        from backend.providers.openrouter import resolve_best_model
        model = resolve_best_model(api_key, kind="vision")
    if not model:
        log.info("Candidate vision review skipped: no vision model could be resolved.")
        return candidates
    limit = min(len(candidates), max(1, min(8, int(prefs.get("max_ai_candidates", 12)))))
    for candidate in candidates[:limit]:
        try:
            moments = [candidate["start"] + (candidate["end"] - candidate["start"]) * ratio for ratio in (0.2, 0.72)]
            parts: list[dict[str, Any]] = [{"type": "text", "text": (
                "Assess this candidate short video using the sampled stills and transcript text. "
                "Analyze visual retention, subject clarity, dynamic facial expression/motion, and 9:16 vertical crop suitability. "
                "Check whether key action or faces fall into the safe zone (avoiding upper 12% and bottom 22% platform overlays). "
                "Flag any static/boring slides, dead frames, or awkward visual cuts. "
                "Return ONLY JSON: {\"visual_score\":0-10,\"subject\":\"...\",\"visual_reason\":\"...\",\"crop_hint\":\"center|left|right\",\"needs_review\":false}. "
                f"Candidate {candidate['id']} at {candidate['start']:.2f}-{candidate['end']:.2f}s. "
                + (f"Transcript: {candidate.get('transcript', '')[:700]}" if transcript_allowed else "Transcript sharing is disabled; judge visual evidence only.")
            )}]
            for timestamp in moments:
                frame = frame_extractor(source, timestamp)
                if not frame:
                    continue
                encoded = base64.b64encode(frame).decode("ascii")
                parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
            if len(parts) == 1:
                continue
            response = chat(api_key, model, [{"role": "user", "content": parts}], timeout=120)
            review = parse_json_object(response)
            visual_score = _clamp(review.get("visual_score", 0))
            candidate.setdefault("components", {})["ai_visual"] = round(visual_score, 2)
            candidate["score"] = round(float(candidate.get("score", 0)) * 0.8 + visual_score * 0.2, 2)
            candidate["visual_review"] = {
                "subject": str(review.get("subject", ""))[:180],
                "reason": str(review.get("visual_reason", ""))[:350],
                "crop_hint": str(review.get("crop_hint", "center"))[:20],
                "needs_review": bool(review.get("needs_review", False)),
            }
            candidate["reason"] = (candidate.get("reason", "") + "; visual review: " + candidate["visual_review"]["reason"])[:700]
        except Exception as exc:
            log.warning("Vision review skipped for candidate %s: %s", candidate.get("id"), exc)
            candidate["visual_review"] = {"skipped": True, "reason": str(exc)[:250]}
    return sorted(candidates, key=lambda item: item.get("score", 0), reverse=True)


def _clamp(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(10.0, number))



def _validate_framing_plan(raw: Any, duration: float) -> dict[str, Any]:
    """Validate cloud vision boxes before allowing them to influence FFmpeg crops.

    Coordinates are normalised [left, top, right, bottom] in the 0..1000 range.
    Invalid or sparse output deliberately resolves to full-frame preservation.
    """
    fallback = {"layout": "full_frame", "keyframes": [], "reason": "No reliable subject track returned."}
    if not isinstance(raw, dict):
        return fallback
    layout = str(raw.get("layout", "full_frame")).lower()
    if layout not in {"single_subject", "two_panel", "three_panel", "full_frame"}:
        layout = "full_frame"
    primary = str(raw.get("primary_subject_id", "person_1"))[:40]
    secondary = str(raw.get("secondary_subject_id", "person_2"))[:40]
    tertiary = str(raw.get("tertiary_subject_id", "person_3"))[:40]
    clean_frames: list[dict[str, Any]] = []
    keyframes = raw.get("keyframes", [])
    if not isinstance(keyframes, list):
        keyframes = []
    for item in keyframes[:24]:
        if not isinstance(item, dict):
            continue
        try:
            t = max(0.0, min(max(0.1, duration), float(item.get("t", 0))))
        except (TypeError, ValueError):
            continue
        subjects = item.get("subjects", [])
        if not isinstance(subjects, list):
            continue
        clean_subjects = []
        for subject in subjects[:4]:
            if not isinstance(subject, dict):
                continue
            sid = str(subject.get("id", ""))[:40]
            box = subject.get("bbox", [])
            if not sid or not isinstance(box, list) or len(box) != 4:
                continue
            try:
                x1, y1, x2, y2 = [max(0.0, min(1000.0, float(v))) for v in box]
            except (TypeError, ValueError):
                continue
            if x2 - x1 < 18 or y2 - y1 < 18:
                continue
            clean_subjects.append({"id": sid, "bbox": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)]})
        clean_frames.append({"t": round(t, 3), "shot_cut": bool(item.get("shot_cut", False)), "subjects": clean_subjects})
    clean_frames.sort(key=lambda item: item["t"])
    counts: dict[str, int] = {}
    co_visible = 0
    for frame in clean_frames:
        ids = {s["id"] for s in frame["subjects"]}
        for sid in ids:
            counts[sid] = counts.get(sid, 0) + 1
        if primary in ids and secondary in ids:
            co_visible += 1
    visible_frames = sum(1 for frame in clean_frames if frame["subjects"])
    confidence = _clamp(raw.get("confidence", 0)) / 10.0
    if layout == "single_subject" and visible_frames < 3:
        layout = "full_frame"
    if layout == "single_subject" and counts.get(primary, 0) < 3 and counts:
        primary = max(counts, key=counts.get)
    if layout == "three_panel":
        if counts.get(primary, 0) < 3 or counts.get(secondary, 0) < 3 or counts.get(tertiary, 0) < 3:
            layout = "two_panel" if counts.get(primary, 0) >= 3 and counts.get(secondary, 0) >= 3 else "single_subject"
        elif confidence < 0.65:
            layout = "two_panel"
    if layout == "two_panel":
        if counts.get(primary, 0) < 3 or counts.get(secondary, 0) < 3 or co_visible < 2:
            layout = "single_subject" if counts.get(primary, 0) >= 3 else "full_frame"
        elif confidence < 0.68:
            layout = "single_subject" if counts.get(primary, 0) >= 3 else "full_frame"
    if layout == "single_subject" and confidence < 0.55:
        layout = "full_frame"
    if layout == "full_frame":
        default_reason = "Vision confidence was too low for a safe subject crop." if confidence < 0.55 else "Vision could not establish a stable subject."
        return {"layout": "full_frame", "keyframes": clean_frames, "reason": str(raw.get("reason") or default_reason)[:220]}
    return {
        "layout": layout,
        "primary_subject_id": primary,
        "secondary_subject_id": secondary if layout in {"two_panel", "three_panel"} else None,
        "tertiary_subject_id": tertiary if layout == "three_panel" else None,
        "keyframes": clean_frames,
        "confidence": confidence,
        "reason": str(raw.get("reason", "Stable visual subject track."))[:220],
    }


def create_editorial_plan(candidate: dict[str, Any], source: Path, api_key: str,
                          frame_extractor, transcript_allowed: bool = True,
                          campaign_brief: str = "") -> dict[str, Any]:
    """Create a bounded, renderable edit plan using cloud vision.

    It never changes source in/out points. The framing plan is a sparse set of
    normalized subject boxes; the local renderer smooths those into crop paths.
    If the model cannot provide reliable boxes, the renderer safely preserves
    the entire source frame instead of forcing a centre crop.
    """
    prefs = get_preferences()
    start = float(candidate.get("start", 0.0))
    end = float(candidate.get("end", start))
    duration = max(0.1, end - start)
    transcript = str(candidate.get("transcript", "")) if transcript_allowed and prefs.get("send_transcript_to_ai", True) else ""
    # Twelve to twenty-four time-labelled stills are a compromise between
    # visual continuity, model cost and prompt size. No source video is
    # uploaded. Respect the explicit frame-sharing switch.
    allow_frames = bool(prefs.get("vision_enabled", True) and prefs.get("send_video_frames_to_ai", True))
    raw_vision = str(prefs.get("vision_model") or "").strip()
    if allow_frames and not raw_vision:
        raise ProviderError("Select a vision-capable OpenRouter model in AI Settings (Load vision models) to use adaptive framing. You can disable sampled-frame sharing to use transcript-only editing.")
    if allow_frames and raw_vision == "auto":
        from backend.providers.openrouter import resolve_best_model
        model = resolve_best_model(api_key, kind="vision")
    else:
        model = str((prefs.get("vision_model") if allow_frames else (prefs.get("vision_model") or prefs.get("llm_model"))) or "").strip()
    if not model:
        raise ProviderError("Select a vision-capable OpenRouter model in AI Settings (Load vision models) to use adaptive framing. You can disable sampled-frame sharing to use transcript-only editing.")
    sample_count = min(24, max(8, int(math.ceil(duration / 3.0))))
    samples = [0.01 + i * (0.98 / max(1, sample_count - 1)) for i in range(sample_count)] if allow_frames else []
    if not transcript and not allow_frames:
        raise ProviderError("AI editing needs either transcript sharing or sampled-frame sharing. Enable one in AI Settings, or disable the AI edit plan.")
    brief_directive = ""
    if str(campaign_brief or "").strip():
        brief_directive = (
            f"\nCAMPAIGN BRIEF & CLIENT DIRECTIVES:\n{campaign_brief.strip()}\n"
            "Ensure the opening 1-2 seconds provide a powerful hook aligned with the brief. "
            "Craft a complete, ready-to-publish 'social_caption' that complies with all brief rules "
            "(including any mandatory hashtags or calls to action)."
        )
    parts: list[dict[str, Any]] = [{"type": "text", "text": (
        _load_skill("VIRAL_PLAYBOOK.md") + "\n" +
        _load_skill("REFRAME_DIRECTOR.md") + "\n" + _load_skill("QUALITY_GATE.md") + "\nYou are a professional short-form editor and visual reframing director. Create a practical edit plan. "
        "Do not alter the clip's exact source start/end or duration. Do not invent events, people, quotes or pixels. "
        "Inspect all ordered frames and choose ONE composition: full_frame for gameplay/screen recordings/wide slides or uncertain subjects; "
        "single_subject when one visible person is a clear continuous subject; two_panel only when two identifiable people are visible together "
        "in multiple sampled frames and each deserves a panel. For single_subject/two_panel return boxes for the visible faces/head+shoulders. "
        "Use stable person IDs across frames when they are clearly the same person; otherwise use a new ID after a shot change. "
        "Coordinates must be [left,top,right,bottom] normalized from 0 to 1000 relative to the full frame. Do not guess a box when a person is not visible. "
        "For every sampled image return its exact supplied time. Set shot_cut=true only when the image clearly begins a different camera shot. "
        "Avoid tight crops: leave headroom and shoulders. Never crop text or critical screen UI. "
        + brief_directive + "\n"
        "Return ONLY JSON with keys: title (max 6 words), caption_style (karaoke|bold|clean|boxed|minimal|pop|glow), "
        "caption_position (top|middle|bottom), font_size (48-84), emphasis_words (exact transcript words only), "
        "burned_captions_visible (boolean), visual_treatment (natural|cinematic|vivid|muted), fade_in, fade_out, "
        "editing_notes (max 3), quality_risks (max 4), social_caption (string), callout_overlay (string), punch_in_recommended (boolean), and framing {layout:full_frame|single_subject|two_panel|three_panel, "
        "primary_subject_id, secondary_subject_id, confidence:0-10, reason, keyframes:[{t:number,shot_cut:boolean,subjects:[{id:string,bbox:[x1,y1,x2,y2]}]}]}. "
        "Do not create cuts, B-roll, overlays, or transitions that are not supported by the source."
        f"\nClip duration: {duration:.2f}s. Content type: {candidate.get('content_type','general')}."
        f"\nTranscript: {transcript[:3500] if transcript else '[No transcript available; rely on images only.]'}"
        "\nImages below are in temporal order. Each label includes exact relative time in seconds."
    )}]
    frames_added = 0
    import base64
    import concurrent.futures

    def _fetch_frame(frac: float) -> tuple[float, bytes | None]:
        rel_t = duration * frac
        return rel_t, frame_extractor(source, start + rel_t, 768)

    if allow_frames and samples:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(samples))) as pool:
            results = list(pool.map(_fetch_frame, samples))
        for relative_t, frame in results:
            if not frame:
                continue
            parts.append({"type": "text", "text": f"SAMPLE_TIME={relative_t:.3f} seconds; inspect this exact frame:"})
            parts.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(frame).decode("ascii")}})
            frames_added += 1
    if allow_frames and frames_added < 4:
        raise ProviderError("Not enough source frames could be extracted for reliable AI editing (need at least 4).")
    if not allow_frames:
        parts[0]["text"] += "\nPrivacy setting: video-frame sharing is disabled. Do not request or infer a crop path; return framing.layout=full_frame and use only permitted transcript text."
    content = chat(api_key, model, [{"role": "user", "content": parts}], timeout=180, max_tokens=6000)
    raw = parse_json_object(content)
    allowed_styles = {"karaoke", "bold", "clean", "boxed", "minimal", "pop", "glow"}
    style = str(raw.get("caption_style", "karaoke")).lower()
    position = str(raw.get("caption_position", "bottom")).lower()
    if style not in allowed_styles:
        style = "karaoke"
    if position not in {"top", "middle", "bottom"}:
        position = "bottom"
    title = re.sub(r"\s+", " ", str(raw.get("title", "")).strip())[:72]
    visual_treatment = str(raw.get("visual_treatment", "natural")).lower()
    if visual_treatment not in {"natural", "cinematic", "vivid", "muted"}:
        visual_treatment = "natural"
    def safe_float(value: Any, default: float, low: float, high: float) -> float:
        try:
            return max(low, min(high, float(value)))
        except (TypeError, ValueError):
            return default
    fade_in = safe_float(raw.get("fade_in", 0.12), 0.12, 0.0, 0.35)
    fade_out = safe_float(raw.get("fade_out", 0.12), 0.12, 0.0, 0.35)
    known_words = {re.sub(r"[^\w'-]", "", word).casefold(): word for word in transcript.split()}
    emphasis: list[str] = []
    raw_emphasis = raw.get("emphasis_words", [])
    for word in raw_emphasis if isinstance(raw_emphasis, list) else []:
        key = re.sub(r"[^\w'-]", "", str(word)).casefold()
        if key in known_words and known_words[key] not in emphasis:
            emphasis.append(known_words[key])
        if len(emphasis) >= 10:
            break
    def clean_list(value: Any, limit: int = 3) -> list[str]:
        if not isinstance(value, list):
            return []
        return [re.sub(r"\s+", " ", str(item)).strip()[:220] for item in value[:limit] if str(item).strip()]
    try:
        font_size = max(48, min(84, int(raw.get("font_size", 68))))
    except (TypeError, ValueError):
        font_size = 68
    framing_plan = _validate_framing_plan(raw.get("framing"), duration) if allow_frames else {
        "layout": "full_frame", "keyframes": [], "reason": "Frame sharing is disabled; full source frame preserved."}
    social_caption = str(raw.get("social_caption", "")).strip()[:1000]
    callout_overlay = str(raw.get("callout_overlay", "")).strip()[:120]
    if social_caption and isinstance(candidate, dict):
        candidate["social_caption"] = social_caption
    return {
        "title": title, "caption_style": style, "caption_position": position,
        "font_size": font_size, "emphasis_words": emphasis,
        "visual_treatment": visual_treatment, "fade_in": fade_in, "fade_out": fade_out,
        "burned_captions_visible": bool(raw.get("burned_captions_visible", False)),
        "editing_notes": clean_list(raw.get("editing_notes"), 3),
        "quality_risks": clean_list(raw.get("quality_risks"), 4),
        "social_caption": social_caption,
        "callout_overlay": callout_overlay,
        "punch_in_recommended": bool(raw.get("punch_in_recommended", False)),
        "frames_reviewed": frames_added, "model": model,
        "framing_plan": framing_plan, "duration_preserved": True,
    }


def interpret_editing_command(command: str, current_settings: dict[str, Any],
                              clip: dict[str, Any], source_duration: float,
                              api_key: str | None = None) -> dict[str, Any]:
    """Translate natural language editing instructions into validated timeline changes."""
    cmd = command.strip().lower()
    updated = dict(current_settings)
    applied_changes: list[str] = []

    # Fast deterministic rules
    if "cinematic" in cmd:
        updated.update({"contrast": 1.08, "brightness": -0.01, "saturation": 0.92, "caption_style": "clean"})
        applied_changes.append("Applied cinematic grade: slight contrast boost, muted saturation, clean captions.")
    elif "vivid" in cmd or "pop" in cmd:
        updated.update({"contrast": 1.05, "saturation": 1.15, "caption_style": "bold"})
        applied_changes.append("Applied vivid grade: enhanced saturation and bold pop captions.")
    elif "muted" in cmd or "flat" in cmd:
        updated.update({"contrast": 1.01, "saturation": 0.85})
        applied_changes.append("Applied muted natural grade.")

    has_caption_kw = "subtitle" in cmd or "caption" in cmd
    if has_caption_kw and ("top" in cmd or "higher" in cmd or "upper" in cmd):
        updated["caption_position"] = "top"
        applied_changes.append("Moved subtitles to top margin.")
    elif has_caption_kw and ("middle" in cmd or "center" in cmd or "centre" in cmd):
        updated["caption_position"] = "middle"
        applied_changes.append("Centered subtitles vertically.")
    elif has_caption_kw and ("bottom" in cmd or "lower" in cmd):
        updated["caption_position"] = "bottom"
        applied_changes.append("Positioned subtitles at bottom margin.")

    if "karaoke" in cmd:
        updated.update({"caption_style": "karaoke", "captions_enabled": True})
        applied_changes.append("Enabled animated karaoke word-highlight captions.")
    elif "pop" in cmd and "caption" in cmd:
        updated.update({"caption_style": "pop", "captions_enabled": True})
        applied_changes.append("Enabled animated pop-in scaling captions.")
    elif "glow" in cmd and "caption" in cmd:
        updated.update({"caption_style": "glow", "captions_enabled": True})
        applied_changes.append("Set neon glow captions.")
    elif "bold" in cmd and "caption" in cmd:
        updated.update({"caption_style": "bold", "captions_enabled": True})
        applied_changes.append("Set bold pop captions.")

    if "punch in" in cmd or "dynamic zoom" in cmd or "emphasis zoom" in cmd:
        updated["punch_in"] = True
        applied_changes.append("Enabled dynamic punch-in zoom on emphasis moments.")

    if "three panel" in cmd or "3 speaker" in cmd or "three speaker" in cmd or "podcast 3" in cmd:
        updated["framing_mode"] = "adaptive"
        applied_changes.append("Configured three-panel multi-speaker layout.")
    elif "split" in cmd or "two speaker" in cmd or "interview" in cmd:
        updated["framing_mode"] = "adaptive"
        applied_changes.append("Switched to adaptive multi-speaker layout.")
    elif "full frame" in cmd or "entire" in cmd or "whole video" in cmd or "blur" in cmd:
        updated["framing_mode"] = "full_frame"
        applied_changes.append("Preserved full source frame with blurred vertical fill.")
    elif "crop" in cmd and "manual" in cmd:
        updated["framing_mode"] = "manual_crop"
        applied_changes.append("Switched to manual crop.")

    if "zoom in" in cmd or "closer" in cmd:
        new_zoom = min(2.5, float(updated.get("zoom", 1.0)) + 0.25)
        updated["zoom"] = round(new_zoom, 2)
        applied_changes.append(f"Zoomed in to {new_zoom:.2f}×.")
    elif "zoom out" in cmd:
        new_zoom = max(1.0, float(updated.get("zoom", 1.0)) - 0.25)
        updated["zoom"] = round(new_zoom, 2)
        applied_changes.append(f"Zoomed out to {new_zoom:.2f}×.")

    if "mute" in cmd or "no audio" in cmd or "remove audio" in cmd:
        updated["preserve_audio"] = False
        applied_changes.append("Muted audio track.")
    elif "keep audio" in cmd or "original audio" in cmd:
        updated["preserve_audio"] = True
        applied_changes.append("Restored original audio track.")

    if "louder" in cmd or "boost volume" in cmd:
        updated["volume"] = min(2.0, float(updated.get("volume", 1.0)) + 0.2)
        applied_changes.append("Increased audio volume.")
    elif "quieter" in cmd:
        updated["volume"] = max(0.2, float(updated.get("volume", 1.0)) - 0.2)
        applied_changes.append("Reduced audio volume.")

    # Exact duration request e.g. "make this exactly 45 seconds", "60s"
    dur_match = re.search(r"(\d+)\s*(?:seconds|second|sec|s\b)", cmd)
    if dur_match:
        target_dur = float(dur_match.group(1))
        if 5 <= target_dur <= 600:
            start = float(updated.get("start", clip.get("start", 0.0)))
            if start + target_dur <= source_duration:
                updated["start"] = start
                updated["end"] = round(start + target_dur, 3)
                applied_changes.append(f"Adjusted timeline duration to exactly {target_dur:.0f} seconds.")
            else:
                new_start = max(0.0, source_duration - target_dur)
                updated["start"] = round(new_start, 3)
                updated["end"] = round(new_start + target_dur, 3)
                applied_changes.append(f"Shifted timeline to accommodate exactly {target_dur:.0f} seconds within source boundaries.")

    if not applied_changes:
        applied_changes.append(f"Understood request '{command}'. Settings preserved.")

    return {
        "command": command,
        "applied_changes": applied_changes,
        "settings": updated,
    }

