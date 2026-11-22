from __future__ import annotations

from unittest.mock import patch
import pytest

from backend.pipeline.context import snap_boundaries_to_context, extract_context_narrative_metadata
from backend.pipeline.discover import discover_candidates
from backend.providers.ai import rerank_candidates


def test_snap_boundaries_exact_duration_contract():
    transcript = {
        "segments": [
            {"start": 8.0, "end": 14.5, "text": "This was the initial setup."},
            {"start": 15.2, "end": 22.0, "text": "So wait until you see this insane glitch!"},
            {"start": 22.5, "end": 74.0, "text": "Everything was working until the game crashed."},
            {"start": 75.0, "end": 80.0, "text": "And that is why nobody plays it anymore."}
        ]
    }
    # Raw seed generated window at [16.5, 76.5] (60s exact)
    snapped_start, snapped_end, details = snap_boundaries_to_context(
        start=16.5,
        end=76.5,
        duration_mode="exact",
        target_duration=60.0,
        source_duration=120.0,
        transcript=transcript,
        max_shift=3.0
    )
    # The start should snap to 15.2 (beginning of "So wait until you see..."),
    # and exact duration of 60.0s must be rigorously preserved!
    assert snapped_start == 15.2
    assert round(snapped_end - snapped_start, 3) == 60.0
    assert snapped_end == 75.2
    assert details["snapped_start"] is True


def test_snap_boundaries_range_duration_contract():
    transcript = {
        "segments": [
            {"start": 10.0, "end": 20.0, "text": "Here is the context."},
            {"start": 20.5, "end": 45.0, "text": "Then the unbelievable happened!"}
        ]
    }
    snapped_start, snapped_end, details = snap_boundaries_to_context(
        start=9.5,
        end=46.0,
        duration_mode="range",
        target_duration=35.0,
        source_duration=60.0,
        transcript=transcript,
        max_shift=2.5
    )
    assert snapped_start == 10.0
    assert snapped_end == 45.0
    assert details["snapped_start"] is True
    assert details["snapped_end"] is True


def test_extract_context_narrative_metadata():
    high_energy = extract_context_narrative_metadata(
        transcript_text="No way bro he actually hit that insane headshot! That was crazy.",
        audio_peak=8.5,
        visual_event=7.0,
        hook_strength=8.0
    )
    assert high_energy["narrative_role"] == "High-Energy Climax"
    assert high_energy["standalone_viability"] >= 8.0
    assert "No way bro" in high_energy["context_synopsis"]


def test_discover_candidates_enriches_context_metadata():
    transcript = {
        "segments": [
            {"start": 5.0, "end": 15.0, "text": "Wait what happened here? Look at this secret trick."},
            {"start": 15.5, "end": 65.0, "text": "You can double your points every single round."}
        ]
    }
    candidates = discover_candidates(
        duration=100.0,
        audio=None,
        visual=None,
        transcript=transcript,
        min_duration=30,
        max_duration=60,
        duration_mode="exact",
        exact_seconds=60
    )
    assert len(candidates) > 0
    top = candidates[0]
    assert "narrative_role" in top
    assert "context_synopsis" in top
    assert "standalone_viability" in top
    assert top["duration"] == 60.0


def test_rerank_candidates_with_context_director():
    candidates = [
        {"id": "c1", "start": 10.0, "end": 70.0, "score": 7.0, "reason": "action moment", "title": "Moment 1",
         "transcript": "Wait check this out. This is the biggest revelation of the entire year."}
    ]
    fake_ai_response = '{"rankings": [' \
        '{"id": "c1", "score": 9.5, "title": "Biggest Revelation", "narrative_role": "Core Insight", ' \
        '"context_synopsis": "Creator discovers the hidden mechanic.", "standalone_viability": 9.8, ' \
        '"callout_overlay": "WAIT TILL THE END 🤯", "reason": "Engaging standalone revelation"}' \
    ']}'
    with patch("backend.providers.ai.chat", return_value=fake_ai_response):
        ranked = rerank_candidates(candidates, "fake-key")
        assert len(ranked) == 1
        top = ranked[0]
        assert top["narrative_role"] == "Core Insight"
        assert top["context_synopsis"] == "Creator discovers the hidden mechanic."
        assert top["callout_overlay"] == "WAIT TILL THE END 🤯"
        assert top["components"]["standalone_viability"] == 9.8
        assert top["score"] >= 8.0
