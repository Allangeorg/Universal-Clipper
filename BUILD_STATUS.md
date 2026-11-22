# Build status — Universal Clipper v0.6.0

## Summary

This update fixes the identified duration, rendering-path and tracking defects and adds a cloud-anchored optical-flow subject tracking stage. It is not a claim of perfect automatic editing or OpusClip parity.

## Changes in this build

### Duration and discovery

- Exact duration remains the default (60 seconds) and can be set from 5 to 600 seconds.
- Candidate windows are constructed to the selected exact length or within the selected range. The former 75%-of-minimum loophole has been removed.
- A source that is too short is rejected rather than silently producing a short substitute.
- Discovery cache version was incremented, preventing use of stale candidate data after the algorithm change.
- Bulk export reconciles older candidate records against the saved project duration contract when the source has enough footage.

### Reframing

- Removed the duplicate legacy renderer's blind centre crop. It now delegates to the canonical editor renderer.
- AI framing uses 12–24 time-labelled sample frames and validated subject boxes from a vision-capable OpenRouter model.
- Added a local OpenCV Lucas–Kanade optical-flow stage that follows cloud-identified subjects between anchors at approximately 5 FPS.
- Scene-cut detection clears tracking state; cloud anchors reacquire the subject. The emitted crop path is smoothed and capped at 240 keyframes.
- Camera expressions hold the first valid crop before the first subject anchor instead of extrapolating backwards.
- Subject-ID churn can be reassociated by nearest subject position without forcing a camera cut unless the position jump is large.
- Low-confidence plans, sparse tracks and failed optical flow fall back safely to full-frame preservation.
- Single-subject and two-panel layouts are compiled into the same FFmpeg renderer.
- The old Haar cascade crop remains labelled experimental; it is not the default AI tracking path.

### Editorial plan and render QA

- OpenRouter plan validation now applies confidence thresholds and rejects low-confidence forced crops. A vision-capable model must be explicitly selected in AI Settings; if none is selected, the app uses safe full-frame preservation rather than pretending AI subject tracking is active.
- The editorial plan can set a short title, caption style/position/size, transcript-verified emphasis words, visual treatment and restrained fades.
- Bulk and editor exports share one canonical render implementation.
- CRF 16 / slow preset defaults; AAC 256 kbps and -14 LUFS / -1 dBTP target when normalization is enabled.
- FFprobe validates output duration and dimensions; outputs failing strict duration tolerance are deleted rather than returned as valid clips.
- Reports contain framing/tracking details, source-quality warnings and black-frame warnings.

## Verification performed

- `pytest -q` — **73 passed** (including FFmpeg-backed renders for adaptive single-subject, two-panel composition and optical-flow-refined motion).
- Python compilation: `python -m compileall -q backend tests` — passed.
- Frontend JavaScript syntax: `node --check backend/static/app.js` — passed.
- FFmpeg/FFprobe integration tests — passed in the build environment, including an optical-flow-refined moving-subject render.
- Smoke-rendered 8-second sections from both latest user-supplied v0.5 output videos through the full-frame 9:16 path; both outputs probed at exactly 8.000 seconds, 1080×1920, H.264/AAC.
- Generated moving-subject test footage confirms the cloud-anchor + Lucas–Kanade tracker follows a moving textured subject between anchors.
- Single-subject adaptive render — 1080×1920 and expected duration verified by FFprobe.
- Two-panel adaptive render — 1080×1920 and expected duration verified by FFprobe.
- Exact/range duration, stale legacy candidate reconciliation, low-confidence fallback, subject-ID reassociation and the legacy-renderer delegation are covered by tests.

## Not verified in this environment

- No live OpenRouter request was made; the user's API key is not available to the build environment. The actual quality of cloud-generated subject boxes/edit plans therefore remains provider/model dependent and requires a user-side acceptance run.
- No actual YouTube download was performed in this build. The supplied MP4s were already processed vertical outputs, not the original long-form YouTube sources.
- No Windows PowerShell 5.1 / Python 3.14 end-to-end run was possible here. The package includes the existing Windows launcher and compatible dependency ranges, but must be smoke-tested on the user's machine.
- Optical-flow tests use controlled synthetic footage. The supplied edited reference MP4s do not include the original landscape sources, so missing pixels in those exports cannot be recovered or used to validate source-to-output detail preservation.

## Known limits

- A sparse cloud frame plan plus local optical flow is better than a fixed crop, but it is not dedicated lip-sync active-speaker diarization. A face can still be misidentified or leave the frame between anchors.
- Two-panel layout requires stable co-visible subjects; otherwise the app falls back to one subject or full frame.
- B-roll, multi-track editing, keyframe animation UI, advanced audio restoration and direct social publishing are not implemented.
- Source resolution/bitrate limits remain irreversible. CRF improves encoding quality but cannot restore detail absent from the source.

## User upgrade notes

Overlay the contents of the inner `universal-clipper` folder onto the existing project. Preserve `.venv`, `.env` and `data`. Create a new job after updating so the new discovery and AI plan cache versions are used.
