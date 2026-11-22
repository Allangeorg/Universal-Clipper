from __future__ import annotations

from typing import Any

PROFILE_TERMS = {
    "gaming": {"kill", "killed", "headshot", "enemy", "squad", "clutch", "respawn", "loot", "weapon", "boss", "round", "ranked", "gameplay", "gaming", "damage", "teamwipe"},
    "sports": {"goal", "match", "team", "player", "score", " ಪಂದ್ಯ", "champion", "tournament", "penalty", "kickoff", "basket", "racing", "race", "knockout"},
    "podcast": {"opinion", "honestly", "podcast", "guest", "host", "conversation", "agree", "disagree", "perspective", "experience"},
    "interview": {"question", "answer", "interview", "career", "journey", "tell", "yourself", "hiring", "company"},
    "tutorial": {"tutorial", "step", "install", "click", "configure", "code", "example", "function", "first", "next", "solution", "mistake"},
    "vlog": {"today", "yesterday", "travel", "visited", "went", "morning", "trip", "journey", "experience", "day"},
    "reaction": {"react", "reaction", "wow", "omg", "seriously", "insane", "unbelievable", "laugh", "funny"},
}

PROFILE_WEIGHTS = {
    "gaming": {"audio_peak": .18, "audio_activity": .10, "visual_event": .28, "visual_activity": .18, "speech_density": .08, "hook_strength": .12, "complete_thought": .06},
    "sports": {"audio_peak": .22, "audio_activity": .12, "visual_event": .30, "visual_activity": .16, "speech_density": .06, "hook_strength": .10, "complete_thought": .04},
    "podcast": {"audio_peak": .08, "audio_activity": .07, "visual_event": .06, "visual_activity": .04, "speech_density": .22, "hook_strength": .28, "complete_thought": .25},
    "interview": {"audio_peak": .08, "audio_activity": .07, "visual_event": .08, "visual_activity": .04, "speech_density": .22, "hook_strength": .22, "complete_thought": .29},
    "tutorial": {"audio_peak": .05, "audio_activity": .05, "visual_event": .18, "visual_activity": .12, "speech_density": .20, "hook_strength": .10, "complete_thought": .30},
    "vlog": {"audio_peak": .12, "audio_activity": .10, "visual_event": .22, "visual_activity": .16, "speech_density": .13, "hook_strength": .15, "complete_thought": .12},
    "reaction": {"audio_peak": .18, "audio_activity": .12, "visual_event": .22, "visual_activity": .16, "speech_density": .10, "hook_strength": .14, "complete_thought": .08},
    "general": {"audio_peak": .18, "audio_activity": .10, "visual_event": .20, "visual_activity": .08, "speech_density": .16, "hook_strength": .18, "complete_thought": .10},
}


def classify_content(transcript: dict[str, Any] | None) -> dict[str, Any]:
    """Conservative transcript-keyword profile suggestion, not a trained classifier."""
    text = " ".join(str(segment.get("text", "")) for segment in (transcript or {}).get("segments", []))
    tokens = {word.strip(".,!?;:'\"()[]{}-").lower() for word in text.split()}
    if not tokens:
        return {"kind": "general", "confidence": 0.0, "reason": "No transcript available; using balanced media-signal weights."}
    counts = {kind: len(tokens & terms) for kind, terms in PROFILE_TERMS.items()}
    best_kind = max(counts, key=counts.get)
    best = counts[best_kind]
    total_hits = sum(counts.values())
    if best < 2:
        return {"kind": "general", "confidence": round(min(.35, best / 6), 2), "reason": "No strong content-type cues; using balanced weights."}
    confidence = min(.82, .35 + best / max(4, len(tokens) * .035) * .25 + best / max(1, total_hits) * .22)
    return {"kind": best_kind, "confidence": round(confidence, 2), "reason": f"Suggested from transcript cues ({best_kind}); override support will be added in a later UI milestone."}
