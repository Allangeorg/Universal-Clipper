# Reference output audit and fixes — 11 November 2026

## References inspected

Latest reference exports supplied:

- `01_893f2b05015b_9x16.mp4`
- `02_86e53033c76b_9x16.mp4`

Earlier exports and frame sheets from v0.2–v0.5 were also considered. The original long-form source videos were not supplied, so this audit can assess exported composition and codec metadata but cannot establish the original source's resolution, crop or detail loss conclusively.

## Latest export measurements

| Property | Video 01 | Video 02 |
|---|---:|---:|
| Duration | 30.000 s | 30.000 s |
| Frame size | 1080 × 1920 | 1080 × 1920 |
| Frame rate | 25 FPS | 25 FPS |
| Video codec | H.264 | H.264 |
| Video bitrate | approx. 6.86 Mbps | approx. 3.15 Mbps |
| Audio | AAC, 48 kHz, stereo | AAC, 48 kHz, stereo |

These are portrait containers, but composition is not automatically portrait-correct because the encoded dimensions are 1080×1920.

## Visible defects

1. **Framing and context loss:** the camera crop is too tight for several scenes. The outputs lose edge text, secondary people, microphones, show branding and contextual objects.
2. **Inconsistent shot treatment:** the selected focal point does not reliably change with scene cuts or camera changes.
3. **Duration surprise:** both latest samples are 30 seconds. Earlier range selection allowed the shortest valid length; users expected the chosen exact duration.
4. **No real editorial plan:** previous versions mostly scored candidates and rendered a fixed crop. They did not produce a timecoded subject path or renderable editing choices.
5. **Low-quality source risk:** output bitrate/resolution cannot establish source detail. Re-encoding cannot restore lost detail.

## Source-level causes fixed

- Removed the old 75%-of-minimum discovery loophole and added strict duration construction.
- Incremented discovery and AI cache versions so old 30-second candidates/editorial results are not reused for fresh jobs.
- Bulk export now reconciles stale candidates against the project's saved exact/range duration contract if the original source contains enough footage.
- Removed the duplicate legacy blind-centre-crop renderer; it delegates to the same canonical editor used by manual edits.
- Fixed a missing `math` import in subject-ID reassociation and added a regression test for ID churn.
- Fixed crop interpolation before the first detected anchor; it now holds the first known crop rather than extrapolating backwards.
- Removed the original 24-frame limit from crop-path compilation; refined paths are kept up to 240 keyframes and held at the first known position before the first subject anchor.
- Added optical-flow refinement between cloud visual anchors, scene-cut resets, bounded path output and confidence gates.
- If a cloud plan is low-confidence or tracking is insufficient, adaptive rendering falls back to full-frame preservation rather than a blind centre crop.
- Added strict FFprobe duration validation and retained output diagnostics.

## Current adaptive pipeline

1. OpenRouter vision reviews 12–24 time-labelled still frames per selected clip, plus transcript text only when sharing is permitted.
2. It returns a constrained layout (`single_subject`, `two_panel` or `full_frame`) and subject boxes normalized to the complete frame.
3. The application validates box size, subject persistence, co-visibility and model confidence.
4. OpenCV optical flow follows those cloud-selected boxes locally at approximately 5 FPS. It bridges the gaps between semantic anchors; it does not run a local generative model or object-detection network.
5. Scene cuts reset tracking. A later cloud anchor reacquires the subject.
6. The crop path is smoothed and emitted at up to 2 keyframes/second, capped at 240 keyframes to bound FFmpeg expression size.
7. The renderer produces a single-subject crop or two-panel stacked composition. If evidence is not sufficient, it preserves the whole source frame over a blurred fill.

## Important limitations

- This is **visual subject tracking**, not dedicated lip-sync active-speaker diarization. It cannot guarantee that the cropped person is the person speaking.
- Optical flow depends on visual texture and can lose a subject during occlusion, heavy motion blur, fast camera moves or abrupt edits. It holds the last reliable position and relies on cloud anchors to reacquire.
- Two-panel mode requires two stable subjects visible together in several sample frames; otherwise it falls back.
- The original source is required to recover missing field of view. The supplied already-cropped MP4s cannot reveal pixels discarded before they were uploaded.
- B-roll, animated transitions, keyframe curve editing and multi-track timeline are not implemented in this release.

## Validation in this build

- 73 automated tests passed, including generated moving-subject optical-flow tracking, subject-ID churn, >24-keyframe path compilation, low-confidence full-frame fallback, single-subject FFmpeg render, two-panel FFmpeg render and strict-duration checks.
- Python compilation and frontend JavaScript syntax checks passed.
- Both supplied reference exports were smoke-rendered through the new full-frame preservation path for 8 seconds; FFprobe confirmed 1080×1920 H.264/AAC and exact 8.000-second duration for each.
- No live OpenRouter API request was made; the user's private API key was not available to the build environment.
- No live YouTube download or Windows PowerShell 5.1 acceptance run was performed in this environment. The uploaded files were processed outputs, not the original source videos, so this smoke render validates export compatibility rather than source-level reframing quality.
