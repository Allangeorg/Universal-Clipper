# Universal Clipper — confirmed product requirements

## Locked creation defaults

| Requirement | Default | Rule |
|---|---|---|
| Duration | Exact 60 seconds | User may choose exact 5–600 seconds or an explicit min/max range. Never silently output shorter than the exact request. |
| Aspect ratio | 9:16 | New projects use vertical output; alternative formats remain available. |
| Editing intelligence | Balanced | Balance hook, payoff, context, completeness, visual/audio interest and pace. |
| Framing | AI adaptive | Use cloud visual anchors + local optical-flow tracking when evidence is sufficient. Fall back to full-frame blurred fill when uncertain. |
| Output quality | H.264 CRF 16, slow | Preserve source dimensions/detail where possible; do not invent upscaled detail. |
| Audio | Preserve + normalize | Keep source audio by default; normalization target -14 LUFS / -1 dBTP when enabled. |
| AI provider | OpenRouter-first | No local LLM or local Whisper requirement. API keys remain in the OS credential vault. |

## Duration contract

- Exact mode sets clip minimum and maximum to the requested length.
- Range mode generates candidates only inside the requested interval.
- Ranking can reorder candidates but cannot modify source in/out timestamps.
- If the source is shorter than the exact requirement, return no candidate and explain why.
- A changed duration requires a new discovery job for best results. Existing candidate records may be reconciled at export only if the source has sufficient surrounding footage.

## Framing contract

1. Ask a vision-capable OpenRouter model to identify stable visual subjects and return time-labelled normalized bounding boxes from 12–24 sampled frames.
2. Validate subject boxes, layout and confidence. Do not allow uncertain boxes to force a crop.
3. Track subjects between cloud anchors locally with OpenCV Lucas–Kanade optical flow at approximately 5 FPS. Reset at scene cuts and reacquire from the next cloud anchor.
4. Smooth the emitted crop path and cap it at 240 keyframes.
5. Support single-subject crop and two-panel stacked composition when evidence supports them.
6. Fall back to a full-frame foreground with blurred fill if subject evidence or tracking is inadequate.
7. Keep manual crop and full-frame modes available.
8. Do not claim lip-sync active-speaker diarization. The current system tracks visually anchored subjects; it does not guarantee recognition of who is speaking.

## Balanced editorial intelligence

- Prefer a clear opening, enough context, a coherent point and a payoff.
- Avoid overvaluing loudness, random motion or keywords.
- Preserve complete thoughts and speaker turns.
- Do not invent quotes, scenes, reactions or B-roll.
- Warn about black gaps and low source quality; do not silently delete speech over black frames.
- Apply only renderable AI plan fields (title, captions, emphasis, grade and fades) after validation.

## Privacy and API requirements

- No local generative model or local Whisper model is required.
- The selected transcription provider receives audio chunks when transcription is enabled.
- Transcript text and sampled frames have independent privacy toggles.
- Up to 24 compressed still frames per exported clip may be sent to a vision-capable model; the complete source video is not uploaded for reframing.
- OpenRouter is the default provider. Groq/OpenAI can be selected for speech separately.

## Not yet accepted as completed

- Dedicated audio-visual active-speaker diarization.
- Automatic B-roll search/insertion and animated transition packs.
- Full multi-track non-linear editor.
- Keyframe curve editor and arbitrary effects graph.
- Advanced audio repair, EQ, compression, denoise and voice isolation.
- One-click platform publishing and channel performance learning.
- Production-grade event detection profiles for every game/sport.

These are separate roadmap items. No release may describe them as implemented before their own tests and real-footage acceptance checks pass.
