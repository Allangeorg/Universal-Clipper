from __future__ import annotations

import bisect
import math
import re
from typing import Any

# Punctuation markers that denote strong thought completions
SENTENCE_TERMINATORS = {".", "!", "?", "…"}
CLAUSE_TERMINATORS = {",", ";", ":", "-", "—"}

# Conversational opening hook cues
HOOK_STARTERS = {
    "so", "wait", "look", "listen", "watch", "here's", "the", "this", "what",
    "why", "how", "if", "when", "imagine", "nobody", "everyone", "never",
    "always", "actually", "honestly", "bro", "guys", "dude", "okay"
}


def find_transcript_segments_in_range(
    segments: list[dict[str, Any]],
    start: float,
    end: float,
    padding: float = 4.0
) -> list[dict[str, Any]]:
    """Return all transcript segments overlapping [start - padding, end + padding]."""
    if not segments:
        return []
    starts = [float(s.get("start", 0.0)) for s in segments]
    left = max(0, bisect.bisect_right(starts, max(0.0, start - padding)) - 1)
    right = min(len(segments), bisect.bisect_right(starts, end + padding) + 1)
    return segments[left:right]


def snap_boundaries_to_context(
    start: float,
    end: float,
    duration_mode: str,
    target_duration: float,
    source_duration: float,
    transcript: dict[str, Any] | None,
    max_shift: float = 3.5
) -> tuple[float, float, dict[str, Any]]:
    """Snap candidate start and end timestamps to speech sentence boundaries and thought closures.

    Strictly honors the duration contract:
    - If duration_mode == 'exact': window length is rigidly preserved as target_duration.
    - If duration_mode == 'range': start and end snap to sentence boundaries within bounds.
    """
    start = max(0.0, float(start))
    end = min(source_duration, max(start + 0.1, float(end)))
    contract_len = end - start if duration_mode != "exact" else target_duration

    details: dict[str, Any] = {
        "original_start": round(start, 3),
        "original_end": round(end, 3),
        "snapped_start": False,
        "snapped_end": False,
        "shift_seconds": 0.0,
    }

    if not transcript or not transcript.get("segments"):
        return round(start, 3), round(end, 3), details

    segments = transcript.get("segments", [])
    nearby = find_transcript_segments_in_range(segments, start, end, padding=max_shift + 1.0)
    if not nearby:
        return round(start, 3), round(end, 3), details

    # 1. Search for best start snap candidate:
    # A segment start that occurs within [start - max_shift, start + max_shift].
    # Favor segment starts that begin a new sentence (previous segment ends with .!?)
    best_start = start
    best_start_score = 0.0

    for i, seg in enumerate(nearby):
        seg_start = float(seg.get("start", 0.0))
        seg_text = str(seg.get("text", "")).strip()
        delta = abs(seg_start - start)
        if delta <= max_shift:
            # Check if this segment represents a sentence beginning
            is_sentence_start = False
            prev_seg = nearby[i - 1] if i > 0 else None
            if prev_seg:
                prev_text = str(prev_seg.get("text", "")).strip()
                if any(prev_text.endswith(p) for p in SENTENCE_TERMINATORS):
                    is_sentence_start = True
            elif seg_start < 1.0:
                is_sentence_start = True

            first_word = seg_text.split()[0].lower().strip(".,!?;:'\"") if seg_text else ""
            has_hook_starter = first_word in HOOK_STARTERS

            # Score based on proximity and sentence completeness
            score = 10.0 - (delta * 1.5)
            if is_sentence_start:
                score += 4.0
            if has_hook_starter:
                score += 2.0

            if score > best_start_score:
                best_start_score = score
                best_start = seg_start

    # If in exact mode, end is strictly locked to best_start + target_duration
    if duration_mode == "exact":
        new_start = max(0.0, min(source_duration - target_duration, best_start))
        new_end = new_start + target_duration
        if new_end > source_duration:
            new_start = max(0.0, source_duration - target_duration)
            new_end = new_start + target_duration
        details["snapped_start"] = abs(new_start - start) > 0.1
        details["shift_seconds"] = round(new_start - start, 3)
        return round(new_start, 3), round(new_end, 3), details

    # In range mode, evaluate both start and end independently
    new_start = best_start
    best_end = end
    best_end_score = 0.0

    for seg in nearby:
        seg_end = float(seg.get("end", 0.0))
        seg_text = str(seg.get("text", "")).strip()
        delta = abs(seg_end - end)
        if delta <= max_shift:
            is_thought_end = any(seg_text.endswith(p) for p in SENTENCE_TERMINATORS)
            is_clause_end = any(seg_text.endswith(p) for p in CLAUSE_TERMINATORS)

            score = 10.0 - (delta * 1.5)
            if is_thought_end:
                score += 5.0
            elif is_clause_end:
                score += 2.0

            if score > best_end_score:
                best_end_score = score
                best_end = seg_end

    new_end = max(new_start + 5.0, min(source_duration, best_end))
    details["snapped_start"] = abs(new_start - start) > 0.1
    details["snapped_end"] = abs(new_end - end) > 0.1
    details["shift_seconds"] = round(new_start - start, 3)
    return round(new_start, 3), round(new_end, 3), details


def extract_context_narrative_metadata(
    transcript_text: str,
    audio_peak: float,
    visual_event: float,
    hook_strength: float,
    brief: str = ""
) -> dict[str, Any]:
    """Generate deterministic narrative context metadata (role, hook insight, standalone rating)

    Provides immediate context awareness even prior to optional OpenRouter AI reranking.
    """
    clean_text = transcript_text.strip()
    words = clean_text.split()
    word_count = len(words)

    # Detect narrative role based on textual cues & sensory signals
    role = "Core Insight & Discussion"
    if audio_peak >= 7.5 or visual_event >= 7.5:
        if any(w in clean_text.lower() for w in ["win", "clutch", "headshot", "died", "kill", "insane", "goal"]):
            role = "High-Energy Climax"
        elif any(w in clean_text.lower() for w in ["laugh", "omg", "no way", "what", "bruh", "bro"]):
            role = "Explosive Reaction"
        else:
            role = "Action Highlight"
    elif hook_strength >= 6.0:
        role = "Hook & Story Setup"
    elif word_count >= 25 and clean_text.rstrip().endswith((".", "!", "?")):
        role = "Complete Story / Argument"

    # Context Synopsis
    first_sentence = clean_text.split(".")[0].strip() if "." in clean_text else clean_text
    if len(first_sentence) > 85:
        first_sentence = first_sentence[:82].rsplit(" ", 1)[0] + "…"
    synopsis = first_sentence or "Engaging visual and audio moment identified by activity signals."

    # Standalone Viability Score (0-10)
    # Rewards sufficient word count, presence of punchline punctuation, and hook cues
    viability = 5.0
    if word_count >= 8:
        viability += 1.0
    if word_count >= 18:
        viability += 1.0
    if any(clean_text.endswith(p) for p in SENTENCE_TERMINATORS):
        viability += 1.5
    if hook_strength >= 5.0:
        viability += 1.0
    if audio_peak >= 7.0 or visual_event >= 7.0:
        viability += 0.5
    viability = min(10.0, round(viability, 1))

    return {
        "narrative_role": role,
        "context_synopsis": synopsis,
        "standalone_viability": viability,
    }
