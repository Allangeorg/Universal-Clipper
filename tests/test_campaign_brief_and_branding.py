from pathlib import Path
from unittest.mock import patch
import pytest

from backend.main import _build_job_options
from backend.pipeline.editor import render_edited_clip
from backend.providers.ai import rerank_candidates, create_editorial_plan


def test_build_job_options_with_campaign_brief_and_logo():
    opts = _build_job_options(
        "exact", 60, 30, 60, "balanced", "9:16",
        campaign_brief="Must include #GTA6 and KreekCraft overlay",
        logo_path="/path/to/logo.png",
        logo_position="top-right",
        logo_scale=0.25,
        logo_opacity=0.85
    )
    assert opts["campaign_brief"] == "Must include #GTA6 and KreekCraft overlay"
    assert opts["logo_path"] == "/path/to/logo.png"
    assert opts["logo_position"] == "top-right"
    assert opts["logo_scale"] == 0.25
    assert opts["logo_opacity"] == 0.85


def test_rerank_candidates_with_campaign_brief():
    candidates = [
        {"id": "c1", "start": 10.0, "end": 70.0, "score": 7.0, "reason": "action moment", "title": "Moment 1"},
        {"id": "c2", "start": 80.0, "end": 140.0, "score": 8.0, "reason": "talking moment", "title": "Moment 2"}
    ]
    fake_ai_response = '{"rankings": [' \
        '{"id": "c1", "score": 9.2, "brief_alignment": 9.5, "reason": "Matches GTA6 brief with high energy hook", "social_caption": "GTA 6 is insane! Watch KreekCraft on YouTube and Twitch #GTA6 #KreekCraft"},' \
        '{"id": "c2", "score": 6.0, "brief_alignment": 4.0, "reason": "Low alignment with GTA6 brief", "social_caption": "Talking to chat #stream"}' \
    ']}'
    with patch("backend.providers.ai.chat", return_value=fake_ai_response):
        ranked = rerank_candidates(candidates, "fake-key", campaign_brief="KreekCraft GTA 6 stream highlights. Must include #GTA6 #KreekCraft.")
        assert len(ranked) == 2
        top = ranked[0]
        assert top["id"] == "c1"
        assert top["components"]["brief_alignment"] == 9.5
        assert "#GTA6" in top["social_caption"]


def test_render_with_logo_watermark(tmp_path):
    # Minimal 1x1 valid PNG bytes
    logo_file = tmp_path / "watermark.png"
    logo_file.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
        b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7Cv\x98\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    source = Path("data/samples/sample_test.mp4")
    if not source.exists():
        pytest.skip("Test sample video not found")

    target = tmp_path / "out_watermarked.mp4"
    settings = {
        "aspect_ratio": "9:16",
        "preserve_audio": True,
        "framing_mode": "full_frame",
        "normalize_audio": False,
        "captions_enabled": False,
        "logo_path": str(logo_file),
        "logo_position": "top-right",
        "logo_scale": 0.20,
        "logo_opacity": 0.85,
        "crf": 23,
        "preset": "ultrafast"
    }
    result = render_edited_clip(source, target, 0.0, 3.0, settings)
    assert target.exists()
    assert target.stat().st_size > 0
    assert result["duration"] > 0
