from backend.pipeline.profiles import classify_content


def test_profile_classifier_suggests_gaming_for_gameplay_transcript():
    transcript = {"segments": [{"text": "That was an insane headshot, the enemy squad is down and we got the win."}]}
    result = classify_content(transcript)
    assert result["kind"] == "gaming"
    assert result["confidence"] > 0


def test_profile_classifier_uses_general_without_transcript():
    result = classify_content(None)
    assert result["kind"] == "general"
    assert result["confidence"] == 0
