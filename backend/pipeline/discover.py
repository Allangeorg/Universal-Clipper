from __future__ import annotations

import bisect
import math
import re
import uuid
from typing import Any

import numpy as np

HOOK_CUES = {
    "wait", "what", "why", "how", "insane", "crazy", "unbelievable", "never", "secret",
    "mistake", "instead", "because", "finally", "actually", "impossible", "worst", "best",
    "first", "only", "everyone", "nobody", "watch", "look", "listen", "remember",
    "killed", "kill", "clutch", "headshot", "win", "won", "goal", "save", "score", "fail",
    "shocking", "truth", "exposed", "hack", "trick", "glitch", "banned", "warning",
    "ruined", "regret", "lost", "died", "million", "billion", "dollars", "fired", "quit",
    "wrong", "dangerous", "reveal", "discovered", "stunt", "broken", "insanity",
    "speechless", "cheated", "trapped", "escaped", "survived", "proof", "confession",
    "illegal", "bizarre", "genius", "hidden", "unreal", "disaster", "embarrassing"
}

VIRAL_HOOK_PATTERNS = [
    re.compile(r"\b(stop\s+(doing|making|using)|don't\s+(do|make|buy)|never\s+(do|buy|use))\b", re.IGNORECASE),
    re.compile(r"\b(nobody\s+(talks\s+about|knows|tells\s+you)|everyone\s+thinks)\b", re.IGNORECASE),
    re.compile(r"\b(here's\s+why|this\s+is\s+why|the\s+reason\s+why)\b", re.IGNORECASE),
    re.compile(r"\b(the\s+truth\s+about|the\s+biggest\s+mistake|worst\s+mistake)\b", re.IGNORECASE),
    re.compile(r"\b(you\s+won't\s+believe|can't\s+believe|wait\s+till\s+the\s+end)\b", re.IGNORECASE),
    re.compile(r"\b(watch\s+this|look\s+at\s+this|listen\s+closely)\b", re.IGNORECASE),
    re.compile(r"\b(i\s+was\s+wrong|changed\s+everything|mind\s+blown)\b", re.IGNORECASE),
    re.compile(r"\b(holy\s+shit|what\s+the\s+hell|what\s+just\s+happened|no\s+way)\b", re.IGNORECASE),
    re.compile(r"\b(how\s+to\s+actually|how\s+i\s+made|how\s+i\s+built|how\s+i\s+got)\b", re.IGNORECASE),
    re.compile(r"\b(the\s+secret\s+to|game\s+changer|cheat\s+code)\b", re.IGNORECASE),
    re.compile(r"\b(banned|exposed|illegal|warning|danger)\b", re.IGNORECASE),
    re.compile(r"\b(did\s+you\s+know|have\s+you\s+ever|why\s+does\s+nobody)\b", re.IGNORECASE),
    re.compile(r"\b(bro\s+what|clutch|1v\d+|headshot|insane\s+play)\b", re.IGNORECASE),
]


def _normalise(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    low, high = np.percentile(values, 15), np.percentile(values, 98)
    if high - low < 1e-7:
        return np.zeros_like(values)
    return np.clip((values - low) / (high - low), 0, 1)


def _timeline_to_seconds(timestamps: list[float], scores: list[float], duration: float) -> np.ndarray:
    count = max(1, int(math.ceil(duration)))
    if not timestamps or not scores:
        return np.zeros(count, dtype=np.float32)
    return np.interp(np.arange(count), np.asarray(timestamps), np.asarray(scores), left=0, right=0).astype(np.float32)


def _transcript_features(transcript: dict[str, Any] | None, start: float, end: float) -> tuple[str, float, float, bool]:
    if not transcript:
        return "", 0.0, 0.0, False
    segments = transcript.get("segments", [])
    if not segments:
        return "", 0.0, 0.0, False
    starts = [float(s.get("start", 0)) for s in segments]
    left = max(0, bisect.bisect_right(starts, start) - 1)
    right = min(len(segments), bisect.bisect_right(starts, end) + 1)
    pieces: list[str] = []
    words: list[str] = []
    opening_words: list[str] = []
    for segment in segments[left:right]:
        s_start = float(segment.get("start", 0))
        s_end = float(segment.get("end", 0))
        if s_end >= start and s_start <= end:
            text = str(segment.get("text", "")).strip()
            if text:
                pieces.append(text)
                seg_words = text.split()
                words.extend(seg_words)
                # Spoken words in the critical initial 4 seconds
                if s_start <= start + 4.2:
                    opening_words.extend(seg_words)
    text = " ".join(pieces)
    if not words:
        return text, 0.0, 0.0, False

    # Evaluate immediate thumb-stop hook strength in first 4 seconds
    opening_text = " ".join(opening_words)
    has_opening_pattern = any(p.search(opening_text) for p in VIRAL_HOOK_PATTERNS)
    opening_cues = sum(1 for w in opening_words if w.strip(".,!?;:'\"()[]{}-").lower() in HOOK_CUES)

    initial_hook_score = 0.0
    if has_opening_pattern:
        initial_hook_score = 1.0
    elif opening_cues >= 2:
        initial_hook_score = 0.90
    elif opening_cues == 1:
        initial_hook_score = 0.70

    # Body narrative cues
    body_cue_hits = sum(1 for word in words if word.strip(".,!?;:'\"()[]{}-").lower() in HOOK_CUES)
    body_hook = min(1.0, body_cue_hits / max(1.0, len(words) * 0.045))

    # Weight opening hook heavily (65%) vs body tension (35%)
    hook = min(1.0, initial_hook_score * 0.65 + body_hook * 0.35) if (initial_hook_score > 0 or body_hook > 0) else 0.0
    density = min(1.0, (len(words) / max(1.0, end - start)) / 2.3)
    complete = text.rstrip().endswith((".", "!", "?", "…"))
    return text, density, hook, complete


def _local_peaks(values: np.ndarray, min_gap: int = 8, threshold: float = 0.28) -> list[int]:
    if values.size < 3:
        return []
    smooth = np.convolve(values, np.ones(3, dtype=np.float32) / 3, mode="same")
    candidates = [i for i in range(1, len(smooth) - 1) if smooth[i] >= smooth[i - 1] and smooth[i] > smooth[i + 1] and smooth[i] >= threshold]
    candidates.sort(key=lambda i: float(smooth[i]), reverse=True)
    chosen: list[int] = []
    for index in candidates:
        if all(abs(index - other) >= min_gap for other in chosen):
            chosen.append(index)
    return sorted(chosen)


def _duration_targets(min_duration: int, max_duration: int) -> list[int]:
    """Return distinct, deterministic candidate lengths inside the user's range.

    Equal min/max means exact duration. A range explores five evenly spaced
    lengths so AI ranking can choose a better story fit without ignoring bounds.
    """
    minimum = int(min_duration)
    maximum = int(max_duration)
    if minimum == maximum:
        return [minimum]
    return sorted({int(round(minimum + (maximum - minimum) * fraction))
                   for fraction in (0.0, 0.25, 0.5, 0.75, 1.0)})


def discover_candidates(duration: float, audio: dict[str, Any] | None, visual: dict[str, Any] | None,
                        transcript: dict[str, Any] | None, min_duration: int = 15,
                        max_duration: int = 60, max_clips: int = 20, content_type: str = "general",
                        black_gaps: list[dict[str, float]] | None = None,
                        editing_intelligence: str = "balanced",
                        duration_mode: str = "range",
                        exact_seconds: int | None = None) -> list[dict[str, Any]]:
    count = max(1, int(math.ceil(duration)))
    duration_mode = str(duration_mode or "range").lower()
    if duration_mode == "exact":
        exact_target = max(5, int(exact_seconds if exact_seconds is not None else min_duration))
        min_duration = max_duration = exact_target
        duration_targets = [exact_target]
    else:
        min_duration = max(5, int(min_duration))
        max_duration = max(5, int(max_duration))
        if min_duration > max_duration:
            min_duration, max_duration = max_duration, min_duration
        duration_targets = _duration_targets(min_duration, max_duration)
    editing_intelligence = editing_intelligence if editing_intelligence in {"conservative", "balanced", "aggressive"} else "balanced"
    # If the source cannot satisfy the requested minimum, don't silently emit
    # shorter clips that violate the user's custom duration.
    if duration + 0.001 < min_duration:
        return []
    max_duration = min(max_duration, int(math.floor(duration)))
    if max_duration < min_duration:
        return []
    audio_scores = np.asarray((audio or {}).get("score", []), dtype=np.float32)
    if audio_scores.size < count:
        audio_scores = np.pad(audio_scores, (0, count - audio_scores.size))
    else:
        audio_scores = audio_scores[:count]

    visual_scores = _timeline_to_seconds((visual or {}).get("seconds", []), (visual or {}).get("score", []), duration)
    if not (visual or {}).get("available", False):
        visual_scores[:] = 0

    has_audio = bool(audio_scores.size and np.max(audio_scores) > 0.03)
    has_visual = bool((visual or {}).get("available", False) and np.max(visual_scores) > 0.03)
    combined = np.zeros(count, dtype=np.float32)
    weight = 0.0
    if has_audio:
        combined += audio_scores * 0.48
        weight += 0.48
    if has_visual:
        combined += visual_scores * 0.52
        weight += 0.52
    if weight:
        combined /= weight
    if not has_audio and not has_visual and not transcript:
        return []

    # 1. Discover transcript-driven hook starts (starts right at the hook sentence!)
    hook_starts: list[float] = []
    if transcript:
        segments = transcript.get("segments", [])
        for seg in segments:
            text = str(seg.get("text", "")).strip()
            s_time = float(seg.get("start", 0))
            words = [w.strip(".,!?;:'\"()[]{}-").lower() for w in text.split()]
            if not words:
                continue
            has_pattern = any(p.search(text) for p in VIRAL_HOOK_PATTERNS)
            has_cue = any(w in HOOK_CUES for w in words[:4])
            is_question = text.endswith("?") and len(words) >= 4
            if has_pattern or has_cue or is_question:
                hook_starts.append(s_time)

    # 2. Seed around independent signal peaks (audio/visual)
    peaks = _local_peaks(combined, min_gap=max(6, min_duration // 2), threshold=0.25)
    seeds: list[tuple[float, str]] = [(float(p), "climax") for p in peaks]
    for hs in hook_starts:
        seeds.append((hs, "hook_first"))

    if transcript:
        segments = transcript.get("segments", [])
        # Topic-density candidates help quieter interviews and tutorials
        if len(segments) > 0:
            step = max(10, min_duration // 2)
            for start_sec in range(0, max(1, count - min_duration + 1), step):
                _, density, _, _ = _transcript_features(transcript, start_sec, min(duration, start_sec + max_duration))
                if density >= 0.30:
                    seeds.append((float(start_sec + min_duration // 2), "balanced"))

    if not seeds:
        # Do not return an empty result merely because all signals are quiet.
        clip_len = min_duration if min_duration == max_duration else min_duration
        if duration + 0.001 >= clip_len:
            stride = max(1, clip_len // 2)
            seeds = [(float(s), "balanced") for s in range(clip_len // 2, max(clip_len // 2 + 1, count - clip_len // 2 + 1), stride)]
            if not seeds:
                seeds = [(float(clip_len / 2), "balanced")]

    candidates: list[dict[str, Any]] = []
    black_gaps = black_gaps or []
    seen_windows: set[tuple[int, int]] = set()

    for seed_val, seed_type in seeds:
        seed_pos = max(0.0, min(float(count - 1), seed_val))
        for clip_len in duration_targets:
            clip_len = min(max_duration, max(min_duration, int(clip_len)))
            if seed_type == "hook_first":
                # Start DIRECTLY at the hook (with 0.25s breath intake) so viewers hear it in seconds 0-2!
                start = max(0.0, min(duration - clip_len, seed_pos - 0.25))
            elif seed_type == "climax":
                # Position peak at ~65% of the clip duration to capture setup and reaction hold
                lead_in = max(1.5, clip_len * 0.65)
                start = max(0.0, min(duration - clip_len, seed_pos - lead_in))
            else:
                lead_in = max(1.5, clip_len * 0.35)
                start = max(0.0, min(duration - clip_len, seed_pos - lead_in))

            end = start + clip_len
            if transcript and transcript.get("segments"):
                from backend.pipeline.context import snap_boundaries_to_context
                start, end, _ = snap_boundaries_to_context(
                    start, end, duration_mode, float(clip_len), duration, transcript
                )
            # Exact interval construction: never truncate at source duration and
            # never accept the former 75%-of-minimum loophole.
            end = start + clip_len
            if end > duration + 0.001:
                start = max(0.0, duration - clip_len)
                end = start + clip_len
            if start < -0.001 or end > duration + 0.001 or abs((end - start) - clip_len) > 0.01:
                continue
            end = round(end, 3)
            start = round(start, 3)
            if abs((end - start) - clip_len) > 0.01:
                continue

            # Deduplicate near-identical candidate windows early
            win_key = (int(round(start * 2)), int(round(end * 2)))
            if win_key in seen_windows:
                continue
            seen_windows.add(win_key)
            a, b = int(start), min(count, max(int(start) + 1, int(math.ceil(end))))
            a_score = float(np.max(audio_scores[a:b])) if has_audio else 0.0
            a_mean = float(np.mean(audio_scores[a:b])) if has_audio else 0.0
            v_score = float(np.percentile(visual_scores[a:b], 90)) if has_visual else 0.0
            v_mean = float(np.mean(visual_scores[a:b])) if has_visual else 0.0
            text, speech, hook, complete = _transcript_features(transcript, start, end)
            components: dict[str, float] = {
                "audio_peak": round(a_score * 10, 2),
                "audio_activity": round(a_mean * 10, 2),
                "visual_event": round(v_score * 10, 2),
                "visual_activity": round(v_mean * 10, 2),
                "speech_density": round(speech * 10, 2),
                "hook_strength": round(hook * 10, 2),
                "complete_thought": 10.0 if complete else (5.0 if text else 0.0),
            }
            from backend.pipeline.profiles import PROFILE_WEIGHTS
            weights: dict[str, float] = dict(PROFILE_WEIGHTS.get(content_type, PROFILE_WEIGHTS["general"]))
            if not has_audio:
                weights["audio_peak"] = weights["audio_activity"] = 0
            if not has_visual:
                weights["visual_event"] = weights["visual_activity"] = 0
            if not transcript:
                weights["speech_density"] = weights["hook_strength"] = weights["complete_thought"] = 0
            # Editing intelligence modifies the candidate discovery philosophy,
            # not the user's duration constraints. Balanced preserves the neutral
            # profile weights; conservative favours coherent complete thoughts;
            # aggressive favours high-energy visual/audio moments and hooks.
            multipliers = {
                "conservative": {"audio_peak": .78, "audio_activity": .82, "visual_event": .80,
                                 "visual_activity": .85, "speech_density": 1.12, "hook_strength": 1.08,
                                 "complete_thought": 1.35},
                "balanced": {},
                "aggressive": {"audio_peak": 1.28, "audio_activity": 1.18, "visual_event": 1.30,
                               "visual_activity": 1.18, "speech_density": .90, "hook_strength": 1.22,
                               "complete_thought": .82},
            }[editing_intelligence]
            for key, multiplier in multipliers.items():
                if key in weights:
                    weights[key] *= multiplier
            total_weight = sum(weights.values()) or 1
            score = sum(components[key] * value for key, value in weights.items()) / total_weight
            if not has_audio and not has_visual and not transcript:
                score = 0.0
            overlapping_black = [
                gap for gap in black_gaps
                if float(gap.get("end", 0)) > start and float(gap.get("start", 0)) < end
            ]
            black_seconds = sum(
                max(0.0, min(end, float(gap.get("end", 0))) - max(start, float(gap.get("start", 0))))
                for gap in overlapping_black
            )
            # Strongly down-rank clips with prolonged blank video, but preserve
            # the raw score until final ranking so the AI layer cannot erase the
            # quality signal. Candidates are pre-sorted using ranking_score.
            black_penalty = min(3.0, 0.75 + max(0.0, black_seconds - 0.45) * 0.45) if black_seconds >= 0.45 else 0.0
            components["visual_integrity"] = round(max(0.0, 10.0 * (1.0 - black_seconds / max(0.1, end - start))), 2)
            from backend.pipeline.context import extract_context_narrative_metadata
            context_meta = extract_context_narrative_metadata(text, a_score * 10, v_score * 10, hook * 10)
            components["standalone_viability"] = context_meta["standalone_viability"]

            if not text:
                title = "Detected media event" if (a_score > 0.5 or v_score > 0.5) else "Suggested review window"
            else:
                title = " ".join(text.split()[:9]).strip(" .,!?:;") or "Untitled highlight"
                if len(title) > 76:
                    title = title[:73].rsplit(" ", 1)[0] + "…"
            reasons = []
            if black_seconds >= 0.45:
                reasons.append(f"contains {black_seconds:.1f}s of full-black video")
            if a_score >= 0.55: reasons.append("strong audio peak")
            if v_score >= 0.55: reasons.append("visible motion/scene activity")
            if hook >= 0.25: reasons.append("hook-like language")
            if complete: reasons.append("complete spoken thought")
            if context_meta["narrative_role"]: reasons.append(context_meta["narrative_role"])
            candidates.append({
                "id": uuid.uuid4().hex[:12], "start": round(start, 3), "end": round(end, 3),
                "duration": round(end - start, 3), "score": round(float(score), 2),
                "ranking_score": round(max(0.0, float(score) - black_penalty), 2),
                "black_gap_seconds": round(black_seconds, 3), "black_gap_penalty": round(black_penalty, 2),
                "quality_warnings": ([f"Contains {black_seconds:.1f}s of full-black video; review before publishing."] if black_seconds >= 0.45 else []),
                "components": components, "content_type": content_type,
                "editing_intelligence": editing_intelligence, "title": title,
                "narrative_role": context_meta["narrative_role"],
                "context_synopsis": context_meta["context_synopsis"],
                "standalone_viability": context_meta["standalone_viability"],
                "callout_overlay": "",
                "reason": ", ".join(reasons) if reasons else "Candidate selected from available media signals",
                "transcript": text[:1800], "selected": True,
            })

    candidates.sort(key=lambda item: item.get("ranking_score", item["score"]), reverse=True)
    deduped: list[dict[str, Any]] = []
    for candidate in candidates:
        overlap = False
        for existing in deduped:
            intersection = max(0.0, min(candidate["end"], existing["end"]) - max(candidate["start"], existing["start"]))
            shorter = min(candidate["end"] - candidate["start"], existing["end"] - existing["start"])
            if shorter > 0 and intersection / shorter > 0.68:
                overlap = True
                break
        if not overlap:
            deduped.append(candidate)
        if len(deduped) >= max_clips:
            break
    return sorted(deduped, key=lambda item: item.get("ranking_score", item["score"]), reverse=True)


def apply_quality_penalties(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply deterministic source-integrity penalties after optional AI judging."""
    for item in candidates:
        if item.pop("quality_penalty_applied", False):
            continue
        penalty = max(0.0, min(3.0, float(item.get("black_gap_penalty", 0.0) or 0.0)))
        if penalty:
            item["score_before_quality_penalty"] = round(float(item.get("score", 0.0)), 2)
            item["score"] = round(max(0.0, float(item.get("score", 0.0)) - penalty), 2)
        item["quality_penalty_applied"] = True
        item.pop("ranking_score", None)
    return sorted(candidates, key=lambda item: float(item.get("score", 0.0)), reverse=True)
