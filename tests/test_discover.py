from backend.pipeline.discover import discover_candidates


def test_discovery_produces_ranked_candidates_from_audio_and_visual_signals():
    duration = 180
    audio_scores = [0.05] * duration
    visual_scores = [0.03] * duration
    for second in range(65, 72):
        audio_scores[second] = 0.95
        visual_scores[second] = 0.85
    for second in range(130, 134):
        audio_scores[second] = 0.72
        visual_scores[second] = 0.65
    audio = {"score": audio_scores}
    visual = {"seconds": list(range(duration)), "score": visual_scores, "available": True}
    clips = discover_candidates(duration, audio, visual, transcript=None, max_clips=10)
    assert clips
    assert len(clips) <= 10
    assert clips[0]["score"] >= clips[-1]["score"]
    assert all(0 <= clip["start"] < clip["end"] <= duration for clip in clips)


def test_discovery_returns_no_fake_candidates_when_no_signal_exists():
    duration = 120
    audio = {"score": [0.0] * duration}
    visual = {"seconds": list(range(duration)), "score": [0.0] * duration, "available": True}
    clips = discover_candidates(duration, audio, visual, transcript=None)
    assert clips == []


def test_black_video_gaps_reduce_candidate_rank_and_are_explained():
    duration = 180
    audio_scores = [0.05] * duration
    visual_scores = [0.05] * duration
    for second in range(40, 52):
        audio_scores[second] = 0.98
        visual_scores[second] = 0.92
    for second in range(110, 122):
        audio_scores[second] = 0.90
        visual_scores[second] = 0.88
    black_gaps = [{"start": 44.0, "end": 48.0, "duration": 4.0}]
    clips = discover_candidates(
        duration,
        {"score": audio_scores},
        {"seconds": list(range(duration)), "score": visual_scores, "available": True},
        transcript=None,
        max_clips=20,
        black_gaps=black_gaps,
    )
    flagged = [clip for clip in clips if clip["black_gap_seconds"] > 0]
    clean = [clip for clip in clips if clip["black_gap_seconds"] == 0]
    assert flagged and clean
    assert all(clip["black_gap_penalty"] > 0 for clip in flagged)
    assert any("full-black video" in clip["reason"] for clip in flagged)


def test_quality_penalty_is_applied_once_and_candidates_are_resorted():
    from backend.pipeline.discover import apply_quality_penalties

    clips = [
        {"id": "black", "score": 8.5, "black_gap_penalty": 2.0},
        {"id": "clean", "score": 7.0, "black_gap_penalty": 0.0},
    ]
    once = apply_quality_penalties(clips)
    assert [clip["id"] for clip in once] == ["clean", "black"]
    assert next(item for item in once if item["id"] == "black")["score"] == 6.5
    twice = apply_quality_penalties(once)
    assert next(item for item in twice if item["id"] == "black")["score"] == 6.5
