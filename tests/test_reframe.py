from backend.pipeline.reframe import _should_use_fit_blur, fit_blur_filtergraph, full_frame_blur_filtergraph


def test_portrait_video_with_late_face_uses_full_frame_fallback():
    assert _should_use_fit_blur(0.73, 10.0, 6, 16, 1.99) is True


def test_portrait_video_with_sparse_face_detection_uses_full_frame_fallback():
    assert _should_use_fit_blur(0.73, 15.0, 9, 23, 2.6) is True


def test_well_tracked_portrait_video_keeps_dynamic_crop():
    assert _should_use_fit_blur(0.73, 15.0, 20, 23, 0.0) is False


def test_landscape_gameplay_without_faces_does_not_force_blurred_fit():
    assert _should_use_fit_blur(1.78, 60.0, 0, 72, None) is False


def test_fit_blur_filtergraph_preserves_full_active_frame():
    report = {"active_bounds": {"left": 0, "top": 246, "right": 1080, "bottom": 1732}}
    graph = fit_blur_filtergraph(report, (1080, 1920))
    assert "crop=1080:1486:0:246" in graph
    assert "boxblur=24:2" in graph
    assert "force_original_aspect_ratio=decrease" in graph
    assert "[vbase]" in graph


def test_visual_black_gap_sampler_detects_black_screens_with_caption_rows():
    from backend.pipeline.visual import _black_gaps_from_flags

    seconds = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    flags = [False, True, True, True, False, True, False]
    gaps = _black_gaps_from_flags(seconds, flags, 3.2, 2.0)
    assert len(gaps) == 2
    assert gaps[0]["duration"] == 1.5
    assert gaps[1]["duration"] == 0.5


def test_full_frame_portrait_graph_preserves_entire_source_image():
    report = {"active_bounds": {"left": 0, "top": 0, "right": 1920, "bottom": 1080}}
    graph = full_frame_blur_filtergraph(report, (1080, 1920))
    assert "force_original_aspect_ratio=decrease" in graph
    assert "force_original_aspect_ratio=increase" in graph
    assert "boxblur=28:4" in graph
    assert "overlay=x=(W-w)/2:y=(H-h)/2" in graph
    assert "[vbase]" in graph
