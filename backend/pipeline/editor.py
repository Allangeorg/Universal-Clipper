from __future__ import annotations

import json
import logging
import math
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from backend.config import FFMPEG
from backend.pipeline.media import MediaError, probe
from backend.pipeline.render import OUTPUTS
from backend.pipeline.subject_tracking import refine_subject_keyframes
from backend.pipeline.reframe import (
    analyse_source_bounds, crop_filter, fit_blur_filtergraph,
    full_frame_blur_filtergraph,
)

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
CAPTION_STYLES = {"bold", "karaoke", "clean", "boxed", "minimal", "pop", "glow"}
log = logging.getLogger(__name__)

_DETECTED_ENCODER: dict[str, Any] | None = None


def get_optimized_video_encoder(requested_preset: str = "fast", requested_crf: int = 18) -> list[str]:
    """Return optimal FFmpeg video encoder flags with hardware acceleration detection."""
    global _DETECTED_ENCODER
    if _DETECTED_ENCODER is None:
        _DETECTED_ENCODER = {"codec": "libx264"}
        try:
            test_proc = subprocess.run(
                [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                 "-i", "testsrc=duration=0.1:size=256x256:rate=10",
                 "-c:v", "h264_nvenc", "-preset", "p4", "-f", "null", "-"],
                capture_output=True, timeout=5, check=False,
            )
            if test_proc.returncode == 0:
                _DETECTED_ENCODER = {"codec": "h264_nvenc"}
                log.info("Hardware acceleration active: using h264_nvenc for fast exports")
        except Exception:
            pass

    codec = _DETECTED_ENCODER["codec"]
    crf_val = max(14, min(30, int(requested_crf)))
    if codec == "h264_nvenc":
        nvenc_preset = "p4" if requested_preset in {"slow", "medium", "fast"} else "p2"
        return ["-c:v", "h264_nvenc", "-preset", nvenc_preset, "-cq", str(crf_val), "-profile:v", "high", "-pix_fmt", "yuv420p"]
    else:
        x264_preset = "fast" if requested_preset in {"slow", "medium"} else requested_preset
        return ["-c:v", "libx264", "-preset", x264_preset, "-crf", str(crf_val), "-profile:v", "high", "-pix_fmt", "yuv420p"]




def _even(value: float) -> int:
    return max(2, int(round(value)) // 2 * 2)


class OneEuroFilter:
    """Adaptive 1€ low-pass filter for smooth camera tracking without jitter or lag.

    Adapts cutoff frequency based on movement speed: low cutoff at low velocity
    eliminates detector jitter; high cutoff during fast pans avoids sluggish lag.
    """
    def __init__(self, t0: float, x0: float, dx0: float = 0.0,
                 min_cutoff: float = 0.8, beta: float = 0.007, d_cutoff: float = 1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.x_prev = float(x0)
        self.dx_prev = float(dx0)
        self.t_prev = float(t0)

    @staticmethod
    def _alpha(t_e: float, cutoff: float) -> float:
        r = 2.0 * math.pi * cutoff * t_e
        return r / (r + 1.0)

    def filter(self, t: float, x: float) -> float:
        t_e = t - self.t_prev
        if t_e <= 0.0:
            return self.x_prev
        a_d = self._alpha(t_e, self.d_cutoff)
        dx = (x - self.x_prev) / t_e
        dx_hat = a_d * dx + (1.0 - a_d) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(t_e, cutoff)
        x_hat = a * x + (1.0 - a) * self.x_prev
        self.x_prev = x_hat
        self.dx_prev = dx_hat
        self.t_prev = t
        return x_hat


def _smooth_camera(points: list[tuple[float, float, float, bool]], max_x: float, max_y: float) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Adaptive One Euro filter camera path with deadzone and hard resets at scene cuts."""
    xs: list[tuple[float, float]] = []
    ys: list[tuple[float, float]] = []
    fx: OneEuroFilter | None = None
    fy: OneEuroFilter | None = None
    last_x: float | None = None
    last_y: float | None = None

    for t, x, y, cut in sorted(points, key=lambda p: p[0]):
        x = max(0.0, min(max_x, x))
        y = max(0.0, min(max_y, y))
        if fx is None or cut or last_x is None or last_y is None:
            fx = OneEuroFilter(t, x, min_cutoff=0.8, beta=0.007)
            fy = OneEuroFilter(t, y, min_cutoff=0.8, beta=0.007)
            last_x, last_y = x, y
        else:
            dx = abs(x - last_x)
            dy = abs(y - last_y)
            target_x = x if dx > max_x * 0.015 else last_x
            target_y = y if dy > max_y * 0.015 else last_y
            last_x = fx.filter(t, target_x)
            last_y = fy.filter(t, target_y)

        xs.append((max(0.0, t), last_x))
        ys.append((max(0.0, t), last_y))
    return xs, ys


def _ffmpeg_piecewise(points: list[tuple[float, float]], fallback: float) -> str:
    points = sorted(points, key=lambda p: p[0])
    if not points:
        return f"{fallback:.3f}"
    compact: list[tuple[float, float]] = []
    for point in points:
        if compact and abs(compact[-1][0] - point[0]) < 0.001:
            compact[-1] = point
        else:
            compact.append(point)
    if len(compact) == 1:
        return f"{compact[0][1]:.3f}"
    expression = f"{compact[-1][1]:.3f}"
    for index in range(len(compact) - 2, -1, -1):
        t0, v0 = compact[index]; t1, v1 = compact[index + 1]
        span = max(0.001, t1 - t0)
        segment = f"({v0:.3f}+({v1-v0:.3f})*(t-{t0:.3f})/{span:.3f})"
        expression = f"if(lt(t,{t1:.3f}),{segment},{expression})"
    # Hold the first known camera position before the first detected anchor;
    # otherwise the interpolation expression extrapolates backwards.
    return f"if(lt(t,{compact[0][0]:.3f}),{compact[0][1]:.3f},{expression})"


def _ai_reframe_graph(meta: dict[str, Any], output_size: tuple[int, int], duration: float,
                      plan: dict[str, Any] | None) -> tuple[str | None, str | None, dict[str, Any]]:
    """Compile cloud-detected normalized subject boxes into a smooth crop or split layout.

    Returns (complex_graph, simple_filter, report). Invalid/insufficient AI boxes
    never cause a centre crop: the caller falls back to full-frame preservation.
    """
    fallback = {"method": "full-frame-preserve", "layout": "full_frame", "reason": "No validated AI subject path; full picture preserved."}
    if not isinstance(plan, dict):
        return None, None, fallback
    layout = str(plan.get("layout", "full_frame"))
    if layout not in {"single_subject", "two_panel", "three_panel"}:
        return None, None, fallback
    frames = plan.get("keyframes", [])
    if not isinstance(frames, list):
        return None, None, fallback
    width = int(meta.get("width", 0)); height = int(meta.get("height", 0))
    out_w, out_h = output_size
    if width < 2 or height < 2:
        return None, None, fallback
    tracks: dict[str, list[tuple[float, tuple[float, float, float, float], bool]]] = {}
    frame_subjects: list[tuple[float, list[tuple[str, tuple[float, float, float, float]]], bool]] = []
    for frame in frames[:240]:
        if not isinstance(frame, dict):
            continue
        try:
            t = max(0.0, min(duration, float(frame.get("t", 0))))
        except (TypeError, ValueError):
            continue
        frame_detections: list[tuple[str, tuple[float, float, float, float]]] = []
        for subject in frame.get("subjects", []) if isinstance(frame.get("subjects"), list) else []:
            if not isinstance(subject, dict):
                continue
            box = subject.get("bbox", [])
            if not isinstance(box, list) or len(box) != 4:
                continue
            try:
                x1, y1, x2, y2 = [max(0.0, min(1000.0, float(v))) for v in box]
            except (TypeError, ValueError):
                continue
            if x2 <= x1 or y2 <= y1:
                continue
            sid = str(subject.get("id", ""))[:40]
            if not sid:
                continue
            pixel_box = (x1/1000*width, y1/1000*height, x2/1000*width, y2/1000*height)
            tracks.setdefault(sid, []).append((t, pixel_box, bool(frame.get("shot_cut", False))))
            frame_detections.append((sid, pixel_box))
        frame_subjects.append((t, frame_detections, bool(frame.get("shot_cut", False))))

    primary = str(plan.get("primary_subject_id", "person_1"))
    secondary = str(plan.get("secondary_subject_id", "person_2"))
    tertiary = str(plan.get("tertiary_subject_id", "person_3"))
    if layout == "single_subject":
        visible_frames = [item for item in frame_subjects if item[1]]
        if len(visible_frames) < 3:
            return None, None, {**fallback, "reason": "Fewer than three reliable subject detections; full picture preserved."}
        # Portrait sources already fill the canvas. Do not crop them again.
        ratio = out_w / out_h
        if width / height <= ratio * 1.03:
            return None, None, {"method": "source-portrait-preserved", "layout": "full_frame", "subjects_tracked": 1}
        crop_h = _even(height)
        crop_w = min(_even(height * ratio), _even(width))
        max_x = max(0, width - crop_w); max_y = max(0, height - crop_h)
        points = []
        locked_id: str | None = primary if primary in tracks else None
        previous_id: str | None = None
        previous_center: tuple[float, float] | None = None
        for t, detections, shot_cut in visible_frames:
            by_id = {sid: box for sid, box in detections}
            chosen_id: str | None = None
            if not shot_cut and locked_id in by_id:
                chosen_id = locked_id
            elif primary in by_id and (shot_cut or previous_id is None):
                chosen_id = primary
            elif previous_center is not None and not shot_cut:
                # If the vision model re-numbers a visible subject, re-associate
                # with the closest box before choosing a new identity.
                chosen_id = min(detections, key=lambda item: math.hypot(
                    ((item[1][0]+item[1][2])/2)-previous_center[0],
                    ((item[1][1]+item[1][3])/2)-previous_center[1]))[0]
            else:
                # At a cut, reacquire the most prominent visible subject.
                chosen_id = max(detections, key=lambda item: (item[1][2]-item[1][0])*(item[1][3]-item[1][1]))[0]
            box = by_id[chosen_id]
            x1,y1,x2,y2 = box; cx=(x1+x2)/2; cy=(y1+y2)/2
            # Give the person headroom and preserve shoulders; leave extra room
            # for subtitles at the bottom.
            x = cx - crop_w * 0.50
            y = cy - crop_h * 0.36
            identity_changed = previous_id is not None and chosen_id != previous_id
            large_reassociation_jump = previous_center is not None and math.hypot(cx-previous_center[0], cy-previous_center[1]) > crop_w * 0.42
            points.append((t, x, y, bool(shot_cut or (identity_changed and large_reassociation_jump))))
            previous_id = chosen_id
            locked_id = chosen_id
            previous_center = (cx, cy)
        xs, ys = _smooth_camera(points, max_x, max_y)
        xexpr = _ffmpeg_piecewise(xs, max_x/2)
        yexpr = _ffmpeg_piecewise(ys, max_y/2)
        filt = (f"crop={crop_w}:{crop_h}:x='max(0,min({max_x},{xexpr}))':y='max(0,min({max_y},{yexpr}))',"
                f"scale={out_w}:{out_h}:flags=bicubic,setsar=1")
        return None, filt, {"method": "openrouter-vision-subject-path", "layout": "single_subject",
                            "subject_id": primary, "keyframes": len(points), "subject_switches": sum(1 for p in points if p[3]),
                            "camera_smoothing": "one-euro-filter"}

    if layout == "three_panel":
        first = tracks.get(primary, [])
        second = tracks.get(secondary, [])
        third = tracks.get(tertiary, [])
        if len(first) < 3 or len(second) < 3 or len(third) < 3:
            if len(first) >= 3 and len(second) >= 3:
                layout = "two_panel"
            else:
                return None, None, {**fallback, "reason": "Three stable speakers were not all visible; full picture preserved."}
        else:
            panel_h = out_h // 2
            top_w = out_w
            bot_w = out_w // 2
            top_ratio = top_w / panel_h
            bot_ratio = bot_w / panel_h

            if width / height > top_ratio:
                top_crop_h = _even(height); top_crop_w = min(_even(height * top_ratio), _even(width))
            else:
                top_crop_w = _even(width); top_crop_h = min(_even(width / top_ratio), _even(height))
            max_x_top = max(0, width - top_crop_w); max_y_top = max(0, height - top_crop_h)

            if width / height > bot_ratio:
                bot_crop_h = _even(height); bot_crop_w = min(_even(height * bot_ratio), _even(width))
            else:
                bot_crop_w = _even(width); bot_crop_h = min(_even(width / bot_ratio), _even(height))
            max_x_bot = max(0, width - bot_crop_w); max_y_bot = max(0, height - bot_crop_h)

            def panel_pts(samples, cw, ch, mx, my):
                pts = [(t, (b[0]+b[2])/2 - cw/2, (b[1]+b[3])/2 - ch/2, c) for t, b, c in samples]
                pxs, pys = _smooth_camera(pts, mx, my)
                return _ffmpeg_piecewise(pxs, mx/2), _ffmpeg_piecewise(pys, my/2)

            x1, y1 = panel_pts(first, top_crop_w, top_crop_h, max_x_top, max_y_top)
            x2, y2 = panel_pts(second, bot_crop_w, bot_crop_h, max_x_bot, max_y_bot)
            x3, y3 = panel_pts(third, bot_crop_w, bot_crop_h, max_x_bot, max_y_bot)

            graph = (
                f"[0:v]setpts=PTS-STARTPTS,split=3[panel_a][panel_b][panel_c];"
                f"[panel_a]crop={top_crop_w}:{top_crop_h}:x='max(0,min({max_x_top},{x1}))':y='max(0,min({max_y_top},{y1}))',"
                f"scale={out_w}:{panel_h}:flags=bicubic,setsar=1[top];"
                f"[panel_b]crop={bot_crop_w}:{bot_crop_h}:x='max(0,min({max_x_bot},{x2}))':y='max(0,min({max_y_bot},{y2}))',"
                f"scale={bot_w}:{panel_h}:flags=bicubic,setsar=1[bot_left];"
                f"[panel_c]crop={bot_crop_w}:{bot_crop_h}:x='max(0,min({max_x_bot},{x3}))':y='max(0,min({max_y_bot},{y3}))',"
                f"scale={bot_w}:{panel_h}:flags=bicubic,setsar=1[bot_right];"
                f"[bot_left][bot_right]hstack=inputs=2,setsar=1[bottom];"
                f"[top][bottom]vstack=inputs=2,setsar=1[vbase]"
            )
            return graph, None, {
                "method": "openrouter-vision-three-panel", "layout": "three_panel",
                "speaker_ids": [primary, secondary, tertiary],
                "keyframes": min(len(first), len(second), len(third)),
                "camera_smoothing": "one-euro-filter",
            }

    first = tracks.get(primary, []); second = tracks.get(secondary, [])
    if len(first) < 3 or len(second) < 3:
        return None, None, {**fallback, "reason": "Two stable speakers were not both visible; full picture preserved."}
    panel_h = out_h // 2
    panel_ratio = out_w / panel_h
    if width / height > panel_ratio:
        crop_h = _even(height); crop_w = min(_even(height * panel_ratio), _even(width))
    else:
        crop_w = _even(width); crop_h = min(_even(width / panel_ratio), _even(height))
    max_x = max(0, width - crop_w); max_y = max(0, height - crop_h)
    def panel_expr(samples):
        pts=[]
        for t,box,cut in samples:
            x1,y1,x2,y2=box; cx=(x1+x2)/2; cy=(y1+y2)/2
            pts.append((t,cx-crop_w/2,cy-crop_h/2,cut))
        xs,ys=_smooth_camera(pts,max_x,max_y)
        return _ffmpeg_piecewise(xs,max_x/2),_ffmpeg_piecewise(ys,max_y/2)
    x1,y1=panel_expr(first); x2,y2=panel_expr(second)
    graph=(f"[0:v]setpts=PTS-STARTPTS,split=2[panel_a][panel_b];"
           f"[panel_a]crop={crop_w}:{crop_h}:x='max(0,min({max_x},{x1}))':y='max(0,min({max_y},{y1}))',"
           f"scale={out_w}:{panel_h}:flags=bicubic,setsar=1[top];"
           f"[panel_b]crop={crop_w}:{crop_h}:x='max(0,min({max_x},{x2}))':y='max(0,min({max_y},{y2}))',"
           f"scale={out_w}:{panel_h}:flags=bicubic,setsar=1[bottom];"
           f"[top][bottom]vstack=inputs=2,setsar=1[vbase]")
    return graph, None, {"method": "openrouter-vision-two-panel", "layout": "two_panel",
                         "speaker_ids": [primary, secondary], "keyframes": min(len(first),len(second)),
                         "camera_smoothing": "one-euro-filter"}


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        whole += 1
        centis = 0
    return f"{hours}:{minutes:02d}:{whole:02d}.{centis:02d}"


def _ass_colour(hex_colour: str) -> str:
    if not HEX_RE.match(hex_colour):
        hex_colour = "#FFFFFF"
    r, g, b = hex_colour[1:3], hex_colour[3:5], hex_colour[5:7]
    return f"&H00{b.upper()}{g.upper()}{r.upper()}"


def _safe_ass_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ")


def _caption_words(transcript: dict[str, Any] | None, start: float, end: float) -> list[dict[str, Any]]:
    if not transcript:
        return []
    result: list[dict[str, Any]] = []
    for word in transcript.get("words", []) or []:
        try:
            ws, we = float(word.get("start", 0)), float(word.get("end", 0))
        except (TypeError, ValueError):
            continue
        text = str(word.get("text", word.get("word", ""))).strip()
        if text and we > start and ws < end:
            result.append({"start": max(start, ws), "end": min(end, we), "text": text})
    if result:
        return sorted(result, key=lambda item: item["start"])
    # Segment-level providers do not always return word timestamps. Approximate
    # word timings within each measured segment; never claim these are exact.
    for segment in transcript.get("segments", []) or []:
        try:
            ss, se = float(segment.get("start", 0)), float(segment.get("end", 0))
        except (TypeError, ValueError):
            continue
        if se <= start or ss >= end:
            continue
        words = str(segment.get("text", "")).split()
        if not words:
            continue
        left, right = max(start, ss), min(end, se)
        span = max(0.05, right - left)
        for index, word in enumerate(words):
            ws = left + span * index / len(words)
            we = left + span * (index + 1) / len(words)
            result.append({"start": ws, "end": we, "text": word})
    return sorted(result, key=lambda item: item["start"])


def _write_ass(path: Path, transcript: dict[str, Any] | None, start: float, end: float,
               speed: float, style: str, font_size: int, position: str,
               text_colour: str, outline_colour: str, title: str = "", manual_caption: str = "",
               emphasis_words: list[str] | None = None) -> bool:
    words = _caption_words(transcript, start, end)
    if not words and manual_caption.strip():
        tokens = manual_caption.strip().split()
        span = max(0.2, end - start)
        words = [{"start": start + span * i / len(tokens),
                  "end": start + span * (i + 1) / len(tokens), "text": token}
                 for i, token in enumerate(tokens)]
    if not words and not title:
        return False
    style = style if style in CAPTION_STYLES else "bold"
    align = {"top": 8, "middle": 5, "bottom": 2}.get(position, 2)
    bold = -1 if style in {"bold", "karaoke", "boxed", "pop", "glow"} else 0
    outline = 0 if style == "minimal" else (4 if style == "glow" else (2 if style == "clean" else 3))
    border_style = 3 if style == "boxed" else 1
    back_colour = "&H99000000" if style == "boxed" else ("&H8000FF00" if style == "glow" else "&H80000000")
    margin_v = 250 if position == "bottom" else (220 if position == "top" else 100)
    primary = _ass_colour(text_colour)
    outline_col = _ass_colour(outline_colour)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},{primary},&H0000FFFF,{outline_col},{back_colour},{bold},0,0,0,100,100,0,0,{border_style},{outline},1,{align},64,64,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows: list[str] = []
    emphasis = {re.sub(r"[^\w'-]", "", str(word)).casefold() for word in (emphasis_words or [])}
    if title.strip():
        rows.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(min(4.0, (end-start)/speed))},Default,,0,0,0,,{{\\an8\\fs{int(font_size*1.25)}\\b1}}{_safe_ass_text(title.strip())}")
    # Short, two-line-safe caption cues. Long four-word strings are hard to read
    # on phones, so split at punctuation, 3 words, or roughly 26 characters.
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for item in words:
        token_len = len(str(item["text"]))
        punctuation_end = str(item["text"]).endswith((".", "!", "?", ":", ";"))
        if current and (len(current) >= 3 or current_chars + token_len + 1 > 26):
            groups.append(current)
            current, current_chars = [], 0
        current.append(item)
        current_chars += token_len + (1 if len(current) > 1 else 0)
        if punctuation_end:
            groups.append(current)
            current, current_chars = [], 0
    if current:
        groups.append(current)

    for group in groups:
        cue_start = max(0.0, (group[0]["start"] - start) / speed)
        cue_end = max(cue_start + 0.12, (group[-1]["end"] - start) / speed)
        cue_end = min(cue_end, max(0.2, (end - start) / speed))
        pieces = []
        for item in group:
            duration_cs = max(1, int(round((item["end"] - item["start"]) / speed * 100)))
            raw_token = str(item["text"])
            token = _safe_ass_text(raw_token)
            is_emphasis = re.sub(r"[^\w'-]", "", raw_token).casefold() in emphasis
            if style == "karaoke":
                prefix = f"{{\\k{duration_cs}}}"
                if is_emphasis:
                    prefix = f"{{\\1c&H0000D7FF&\\b1\\fscx112\\fscy112\\t(0,100,\\fscx100\\fscy100)\\k{duration_cs}}}"
                    token += "{\\rDefault}"
                pieces.append(prefix + token)
            elif style == "pop":
                # Modern pop-in scale animation for word entrance
                pop_tag = f"{{\\fscx116\\fscy116\\t(0,110,\\fscx100\\fscy100)}}"
                if is_emphasis:
                    pop_tag = f"{{\\1c&H0000D7FF&\\b1\\fscx122\\fscy122\\t(0,120,\\fscx100\\fscy100)}}"
                    token += "{\\rDefault}"
                pieces.append(pop_tag + token)
            elif is_emphasis:
                pieces.append(f"{{\\1c&H0000D7FF&\\b1}}{token}{{\\rDefault}}")
            else:
                pieces.append(token)
        joined_text = " ".join(pieces)
        rows.append(f"Dialogue: 0,{_ass_time(cue_start)},{_ass_time(cue_end)},Default,,0,0,0,,{{\\fad(50,50)}}{joined_text}")
    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8-sig")
    return bool(rows)


def render_edited_clip(source: Path, target: Path, start: float, end: float,
                       settings: dict[str, Any], transcript: dict[str, Any] | None = None) -> dict[str, Any]:
    if not source.exists():
        raise MediaError("Original source file is missing.")
    meta = probe(source)
    duration = float(meta["duration"])
    start = max(0.0, min(float(start), duration - 0.1))
    end = max(start + 0.1, min(float(end), duration))
    aspect = str(settings.get("aspect_ratio", "9:16"))
    if aspect not in OUTPUTS:
        raise ValueError("Choose a supported aspect ratio.")
    speed = min(2.0, max(0.5, float(settings.get("speed", 1.0))))
    punch_in = bool(settings.get("punch_in", False))
    zoom = min(2.5, max(1.0, float(settings.get("zoom", 1.0)) * (1.15 if punch_in else 1.0)))
    volume = min(2.0, max(0.0, float(settings.get("volume", 1.0))))
    brightness = min(0.25, max(-0.25, float(settings.get("brightness", 0.0))))
    contrast = min(2.0, max(0.5, float(settings.get("contrast", 1.0))))
    saturation = min(2.0, max(0.0, float(settings.get("saturation", 1.0))))
    fade_in = min(3.0, max(0.0, float(settings.get("fade_in", 0.0))))
    fade_out = min(3.0, max(0.0, float(settings.get("fade_out", 0.0))))
    focus_x = min(1.0, max(0.0, float(settings.get("focus_x", 0.5))))
    focus_y = min(1.0, max(0.0, float(settings.get("focus_y", 0.5))))
    size = OUTPUTS[aspect]
    # Framing is explicit. Full-frame is the safe default: a 16:9 source cannot
    # fill a 9:16 canvas without losing information, so keep the whole picture
    # sharp in the centre and use a blurred duplicate as the background.
    filters: list[str] = ["setpts=PTS-STARTPTS"]
    reframe_report: dict[str, Any] = {"method": "original-frame"}
    fit_blur_graph: str | None = None
    framing_mode = str(settings.get("framing_mode") or ("adaptive" if settings.get("smart_reframe") is True else "full_frame"))
    if framing_mode not in {"adaptive", "full_frame", "smart_crop", "manual_crop"}:
        framing_mode = "full_frame"
    if size:
        out_w, out_h = size
        if framing_mode == "adaptive":
            framing_plan = settings.get("framing_plan")
            tracking_report: dict[str, Any] = {"tracking": "not-requested"}
            if isinstance(framing_plan, dict) and framing_plan.get("layout") in {"single_subject", "two_panel", "three_panel"}:
                try:
                    framing_plan, tracking_report = refine_subject_keyframes(
                        source, start, end, framing_plan, sample_fps=5.0, output_fps=2.0
                    )
                except Exception as exc:
                    # Optical-flow tracking is an enhancement. The validated cloud
                    # anchors remain usable, but we report the degraded path clearly.
                    tracking_report = {"tracking": "anchor-only", "reason": str(exc)[:220]}
                    log.warning("Optical-flow subject refinement skipped: %s", exc)
            complex_graph, simple_filter, ai_report = _ai_reframe_graph(meta, size, end-start, framing_plan)
            if tracking_report.get("tracking") not in {"not-requested", "refined"}:
                ai_report["tracking_report"] = tracking_report
            elif tracking_report.get("tracking") == "refined":
                ai_report["tracking_report"] = tracking_report
            if complex_graph:
                fit_blur_graph = complex_graph
                filters = []
                reframe_report = ai_report
            elif simple_filter:
                filters.append(simple_filter)
                reframe_report = ai_report
            else:
                reframe_report = analyse_source_bounds(source, start, end)
                fit_blur_graph = full_frame_blur_filtergraph(reframe_report, size)
                if ai_report.get("method") == "source-portrait-preserved":
                    reframe_report.update({"method": "source-portrait-preserved", "layout": "full_frame",
                                           "reason": "Source is already portrait; no second crop was applied."})
                elif isinstance(settings.get("framing_plan"), dict) and settings["framing_plan"].get("layout") == "full_frame":
                    reframe_report.update({"method": "full-frame-preserve", "layout": "full_frame", "ai_decision": ai_report.get("reason")})
                else:
                    reframe_report.update({"method": "full-frame-fallback", "layout": "full_frame", "ai_fallback": ai_report.get("reason")})
                if tracking_report.get("tracking") not in {"not-requested", "not-needed"}:
                    reframe_report["tracking_report"] = tracking_report
                filters = []
        elif framing_mode == "full_frame":
            reframe_report = analyse_source_bounds(source, start, end)
            fit_blur_graph = full_frame_blur_filtergraph(reframe_report, size)
            filters = []
        elif framing_mode == "smart_crop":
            crop_expression, reframe_report = crop_filter(
                source, start, end, size, focus_x=focus_x, focus_y=focus_y, zoom=zoom
            )
            if reframe_report.get("mode") == "fit-blur":
                fit_blur_graph = fit_blur_filtergraph(reframe_report, size)
                filters = []
            else:
                filters.append(crop_expression)
        else:
            ratio = out_w / out_h
            if meta["width"] / max(1, meta["height"]) > ratio:
                base_h = meta["height"]
                base_w = base_h * ratio
            else:
                base_w = meta["width"]
                base_h = base_w / ratio
            crop_w = max(2, int(base_w / zoom) // 2 * 2)
            crop_h = max(2, int(base_h / zoom) // 2 * 2)
            crop_x = max(0, min(meta["width"] - crop_w, int((meta["width"] - crop_w) * focus_x)))
            crop_y = max(0, min(meta["height"] - crop_h, int((meta["height"] - crop_h) * focus_y)))
            filters.append(f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={out_w}:{out_h}:flags=bicubic,setsar=1")
    if abs(brightness) > 0.001 or abs(contrast - 1.0) > 0.001 or abs(saturation - 1.0) > 0.001:
        filters.append(f"eq=brightness={brightness:.4f}:contrast={contrast:.4f}:saturation={saturation:.4f}")
    output_duration = (end - start) / speed
    black_gaps: list[dict[str, float]] = []
    # Use precomputed black gaps when available, avoiding redundant decode pass during export
    if "black_gaps" in settings and isinstance(settings["black_gaps"], list):
        black_gaps = settings["black_gaps"]
    elif not settings.get("skip_black_qa", True):
        try:
            from backend.pipeline.quality import detect_black_gaps
            black_gaps = detect_black_gaps(source, start, end)
        except Exception as exc:
            log.warning("Black-frame QA skipped: %s", exc)
    quality_warnings: list[str] = []
    source_w = int(meta.get("width", 0) or 0)
    source_h = int(meta.get("height", 0) or 0)
    source_bitrate = int(meta.get("video_bitrate", 0) or 0)
    if source_w and source_h and min(source_w, source_h) < 720:
        quality_warnings.append(f"Source is only {source_w}×{source_h}; export cannot restore detail that is not present in the source.")
    if source_bitrate and source_bitrate < 1_200_000:
        quality_warnings.append("Source video bitrate is low; the output preserves the available detail but may still look soft or blocky.")
    if black_gaps:
        total_black = sum(item["duration"] for item in black_gaps)
        quality_warnings.append(
            f"Source contains {len(black_gaps)} full-black section(s) totaling {total_black:.1f}s. "
            "They were left intact because source audio continues; review these moments before publishing."
        )
    source_duration = end - start
    # Video fades run before setpts, so scale their source-time durations by
    # playback speed.  This keeps the visible fade length consistent with the
    # audio fade and the final rendered timeline.
    if fade_in > 0:
        video_fade_in = min(fade_in * speed, source_duration / 2)
        filters.append(f"fade=t=in:st=0:d={video_fade_in:.3f}")
    if fade_out > 0:
        video_fade_out = min(fade_out * speed, source_duration / 2)
        filters.append(f"fade=t=out:st={max(0, source_duration-video_fade_out):.3f}:d={video_fade_out:.3f}")
    if abs(speed - 1.0) > 0.001:
        filters.append(f"setpts=PTS/{speed:.4f}")
    target.parent.mkdir(parents=True, exist_ok=True)
    ass_path = target.with_suffix(".ass")
    captions_enabled = bool(settings.get("captions_enabled", False))
    if captions_enabled:
        _write_ass(ass_path, transcript, start, end, speed,
                   str(settings.get("caption_style", "bold")),
                   int(min(110, max(24, int(settings.get("font_size", 64))))),
                   str(settings.get("caption_position", "bottom")),
                   str(settings.get("text_colour", "#FFFFFF")),
                   str(settings.get("outline_colour", "#000000")),
                   str(settings.get("title_overlay", "")), str(settings.get("caption_text", "")),
                   list(settings.get("emphasis_words", []) or []))
        if ass_path.exists():
            filters.append("subtitles=filename='" + ass_path.name + "'")
    elif str(settings.get("title_overlay", "")).strip():
        _write_ass(ass_path, None, start, end, speed, "clean", 54, "top", "#FFFFFF", "#000000", str(settings.get("title_overlay", "")), "", [])
        filters.append("subtitles=filename='" + ass_path.name + "'")

    logo_path_str = str(settings.get("logo_path", "") or "").strip()
    logo_file = Path(logo_path_str) if logo_path_str else None
    has_logo = bool(logo_file and logo_file.is_file())

    # Apply the input trim before filters. Placing -t after -i would cap the
    # already speed-adjusted output and could undo the requested duration.
    command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{end-start:.3f}", "-i", str(source)]
    if has_logo:
        command += ["-i", str(logo_file)]

    logo_graph = ""
    overlay_filter = ""
    if has_logo:
        logo_scale = min(0.45, max(0.08, float(settings.get("logo_scale", 0.20))))
        logo_opacity = min(1.0, max(0.2, float(settings.get("logo_opacity", 0.90))))
        logo_pos = str(settings.get("logo_position", "top-right")).lower()
        target_w = out_w if size else 1080
        logo_w = max(2, int(round(target_w * logo_scale)) // 2 * 2)
        pos_expr = {
            "top-right": ("W-w-36", "36"),
            "top-left": ("36", "36"),
            "bottom-right": ("W-w-36", "H-h-160"),
            "bottom-left": ("36", "H-h-160"),
            "top-center": ("(W-w)/2", "36"),
        }.get(logo_pos, ("W-w-36", "36"))
        alpha_filter = f"format=rgba,colorchannelmixer=aa={logo_opacity:.2f}" if logo_opacity < 0.999 else "format=rgba"
        logo_graph = f";[1:v]scale={logo_w}:-1,{alpha_filter}[logo_ready]"
        overlay_filter = f";[vprelogo][logo_ready]overlay=x={pos_expr[0]}:y={pos_expr[1]}:shortest=1[vout]"

    if fit_blur_graph is not None:
        # The fit/blur composition is already a complex filtergraph. Attach all
        # downstream colour, fade, speed and subtitle filters to its [vbase]
        # output; do not also pass -vf (FFmpeg cannot combine -vf with this map).
        post_filters = ",".join(filters) if filters else "null"
        if has_logo:
            graph = fit_blur_graph + f";[vbase]{post_filters}[vprelogo]" + logo_graph + overlay_filter
            command += ["-filter_complex", graph, "-map", "[vout]"]
        else:
            graph = fit_blur_graph + f";[vbase]{post_filters}[vout]"
            command += ["-filter_complex", graph, "-map", "[vout]"]
    else:
        if has_logo:
            post_filters = ",".join(filters) if filters else "null"
            graph = f"[0:v]{post_filters}[vprelogo]" + logo_graph + overlay_filter
            command += ["-filter_complex", graph, "-map", "[vout]"]
        else:
            if filters:
                command += ["-vf", ",".join(filters)]
            command += ["-map", "0:v:0"]
    encoder_args = get_optimized_video_encoder(
        requested_preset=str(settings.get("preset", "fast")),
        requested_crf=int(settings.get("crf", 18)),
    )
    command += ["-map", "0:a?"] + encoder_args
    preserve_audio = bool(settings.get("preserve_audio", True)) and meta["has_audio"] and volume > 0
    if preserve_audio:
        audio_filters = []
        if abs(speed - 1.0) > 0.001:
            audio_filters.append(f"atempo={speed:.4f}")
        audio_filters.append(f"volume={volume:.4f}")
        if bool(settings.get("normalize_audio", True)):
            # Platform-friendly integrated loudness and true-peak ceiling.
            audio_filters.append("loudnorm=I=-14:TP=-1.0:LRA=11")
        if fade_in > 0:
            audio_filters.append(f"afade=t=in:st=0:d={min(fade_in, output_duration/2):.3f}")
        if fade_out > 0:
            fade_duration = min(fade_out, output_duration/2)
            audio_filters.append(f"afade=t=out:st={max(0, output_duration-fade_duration):.3f}:d={fade_duration:.3f}")
        audio_filters.append("aresample=48000")
        command += ["-af", ",".join(audio_filters), "-c:a", "aac", "-b:a", "256k"]
    else:
        command += ["-an"]
    command += ["-movflags", "+faststart", str(target)]
    try:
        result = subprocess.run(command, cwd=target.parent, capture_output=True, text=True, timeout=2400, check=False)
    except FileNotFoundError as exc:
        raise MediaError("FFmpeg was not found. Install FFmpeg and restart the application.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("Render timed out. Try a shorter clip or faster preset.") from exc
    finally:
        if ass_path.exists():
            ass_path.unlink(missing_ok=True)
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise MediaError((result.stderr or "FFmpeg failed to render this edit")[-1800:])
    from backend.pipeline.quality import validate_rendered_output, QualityGateError
    expected_duration = (end - start) / speed
    try:
        qa_result = validate_rendered_output(
            target, expected_duration, expected_aspect=aspect, require_audio=preserve_audio
        )
    except QualityGateError as exc:
        target.unlink(missing_ok=True)
        raise MediaError(f"{exc}. The output was deleted rather than returned as an invalid clip.") from exc
    rendered_meta = probe(target)
    actual_duration = qa_result["duration"]
    return {"path": str(target), "duration": actual_duration,
            "expected_duration": expected_duration, "aspect_ratio": aspect, "bytes": target.stat().st_size,
            "width": rendered_meta.get("width"), "height": rendered_meta.get("height"),
            "video_bitrate": rendered_meta.get("video_bitrate"),
            "settings": {**settings, "start": start, "end": end, "framing_mode": framing_mode},
            "reframe": reframe_report, "black_gaps": black_gaps,
            "warnings": quality_warnings}
