# Universal Clipper v0.6.0

**API-first AI clipping and editing studio for Windows.** Import a local video or a supported YouTube URL, discover candidate moments, review them, and export vertical or landscape clips. Generative AI runs through your configured cloud API. Video decoding, optical-flow tracking, composition, editing and rendering run locally with FFmpeg/OpenCV.

This is a substantial engineering update, not a claim of perfect or OpusClip-equivalent output. The release fixes specific source-code defects and adds a cloud-anchored, locally refined reframing path. Live OpenRouter and Windows acceptance tests still need to be performed on your machine.

## v0.6.0 highlights

- **Duration is enforced.** New jobs default to exactly 60 seconds. Set any exact target from 5–600 seconds or use a minimum/maximum range. The discovery engine does not return a shorter candidate to satisfy an exact request. An insufficient source produces no candidate rather than a silently shortened clip.
- **9:16 is the default.** Output is 1080×1920. Adaptive framing is the default when a vision-capable OpenRouter model and frame-sharing permission are configured. Uncertain visual decisions fall back to full-frame preservation with a blurred fill; they do not force a centre crop.
- **Cloud visual anchors + local tracking.** OpenRouter reviews 12–24 time-labelled sample frames and supplies validated subject boxes. OpenCV Lucas–Kanade optical flow tracks those subjects between anchors at approximately 5 samples/second. Scene cuts reset tracks. The crop path is smoothed and capped at 240 keyframes to keep FFmpeg expressions bounded.
- **Single- and two-panel composition.** Landscape talking-head footage can use a subject-aware portrait crop; a stable two-person composition can use stacked panels. If the evidence is sparse or low-confidence, the editor preserves the whole frame instead.
- **AI export plan.** A vision-capable model can propose an opening title, caption style/position/size, transcript-verified emphasis words, restrained visual treatment and short fades. These settings are validated and applied by the renderer.
- **One canonical renderer.** Bulk export and editor export now use the same FFmpeg engine. The legacy blind centre-crop renderer delegates to the canonical editor rather than retaining a second, inconsistent crop path.
- **Strict render validation.** The renderer probes the exported MP4 and deletes an output whose duration differs materially from the expected duration.
- **Better diagnostics.** Exports report actual duration, output dimensions, bitrate, framing mode, tracking statistics and quality warnings.

## Important framing trade-off

A 16:9 landscape frame cannot fill a 9:16 portrait canvas with sharp pixels and still preserve every source pixel. The application therefore chooses between:

1. **AI adaptive (default):** use a validated subject crop or a two-panel layout where the visual evidence supports it; otherwise fall back to full-frame preservation.
2. **Full frame + blur fill:** show the complete source picture sharply in the centre and use a blurred duplicate behind it. No horizontal source content is discarded.
3. **Manual crop:** use the user's selected focal position and zoom. This intentionally removes some source content.

A face/subject that is already cut off in the source cannot be recovered by reframing. AI subject framing is also not the same as lip-sync-based active-speaker diarization; this release does not claim perfect speaker identity recognition.

## Architecture

```text
Upload / URL
    |
    v
FFprobe + media preparation
    |
    +--> audio features (local)
    +--> visual motion / black-frame analysis (local)
    +--> cloud transcription (OpenRouter by default)
    |
    v
Candidate discovery (strict user duration contract)
    |
    v
OpenRouter semantic ranking / candidate visual review
    |
    v
Review and select
    |
    v
OpenRouter editorial + visual framing plan (12–24 still frames)
    |
    v
OpenCV optical-flow tracking between AI anchors (5 FPS)
    |
    v
Scene-aware crop path / full-frame / two-panel layout
    |
    v
Canonical FFmpeg editor + caption/audio filters
    |
    v
FFprobe duration, dimension and bitrate QA
```

The AI model does not emit arbitrary timestamps for the final cut. Candidate start/end values are kept in the project database and checked against the requested duration contract. The visual model supplies normalized subject boxes; local optical flow tracks between those cloud anchors. Invalid boxes, too few detections, low confidence or tracking failure produce a safe full-frame fallback rather than a forced crop.

### Optical-flow tracking details

- Cloud frames are sampled at evenly spaced points across the selected clip (12–24 frames depending on duration).
- OpenCV reads the source locally and tracks texture points inside each cloud-provided subject box using pyramidal Lucas–Kanade optical flow.
- A histogram scene-cut detector clears the previous tracking state at a detected cut; the next valid cloud anchor reacquires the subject.
- Cloud boxes refresh drifted tracks. Optical flow only bridges movement between anchors; it does not invent people or switch to an unverified subject.
- A smoothed crop path is emitted at up to 2 keyframes/second and capped at 240 points.
- If tracking cannot be maintained, the original sparse cloud anchors are used; if those are insufficient, full-frame composition is used.

This is an improvement over a largest-face Haar cascade, but it is not equivalent to a dedicated active-speaker model. For professional lip-sync-based speaker switching, a separate audio-visual diarization model/service is still required.

## Requirements

- Windows 10/11 x64
- Python 3.11, 3.12, 3.13 or 3.14 (the launcher checks installed versions; pip resolves compatible wheels)
- FFmpeg and FFprobe on PATH (a full Windows build with `libx264`, `libass` recommended)
- Internet for YouTube URL ingestion and selected cloud APIs
- An OpenRouter API key for transcription, semantic ranking and sampled-frame visual reasoning
- Enough disk space for source media, temporary audio, project files and exports

No local generative model, Ollama server or local Whisper model is required. OpenCV is used for signal/optical-flow computation, not for running a local LLM.

## Windows setup

1. Extract the ZIP.
2. Open PowerShell in the inner `universal-clipper` folder.
3. Run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
Unblock-File .\run_windows.ps1
.\run_windows.ps1
```

4. Open `http://127.0.0.1:8765`.
5. Open **AI Settings**, add your OpenRouter key, test the connection, refresh models and select a vision-capable model for the vision field.
6. Keep speech transcription set to OpenRouter if you want the same key to handle transcription.

The launcher creates/reuses `.venv`, installs `requirements.txt`, checks FFmpeg/FFprobe and starts Uvicorn. The app binds to `127.0.0.1` only; it is not configured as a public network server.

## Upgrade without losing your settings

Stop the running app with `Ctrl+C`, extract the new ZIP and copy the contents of the inner `universal-clipper` folder over your existing project folder. Choose **Replace files** for application code.

Keep these user-specific paths intact:

- `.venv/` — Python environment
- `.env` — optional environment-based credentials
- `data/` — SQLite database, source media, work files and exports

The project database is migrated automatically. New discovery/AI cache version numbers force stale candidates and editorial plans to be regenerated under the new logic. Create a **new job** after upgrading to get fresh analysis; old source files and previous exports remain in `data/`.

## OpenRouter configuration and privacy

1. Create a key at <https://openrouter.ai/keys>.
2. Save it in **AI Settings**. Keys are stored through the operating-system credential vault and are not returned to the browser.
3. Select a text model and a **vision-capable** model. A text-only model cannot perform subject-box analysis.
4. Keep transcription on OpenRouter to use the same key, or deliberately select Groq/OpenAI with a separate key.
5. The transcript-sharing and sampled-frame-sharing controls are independent.

When sampled-frame sharing is enabled, up to 24 compressed still frames from each exported clip may be sent to the selected vision model. The complete video file is not uploaded for the framing plan. Speech-to-text sends audio chunks to the transcription provider. Transcript text may be sent for semantic ranking and editing only when the relevant privacy setting permits it. API usage can incur charges; model prices and limits are provider-specific.

Do not paste API keys into chat, source files or issue reports. Do not commit `.env`.

## Duration behaviour

- Exact mode sets the candidate minimum and maximum to the same requested number of seconds.
- Range mode considers candidate lengths only inside the selected minimum and maximum.
- AI ranking may reorder candidates, but it cannot change their in/out timestamps.
- If the source is shorter than the requested duration, no invalid shorter clip is substituted.
- A previous completed job retains its original saved settings. Create a new job after changing duration settings; the exporter can reconcile legacy candidates where the source has enough footage, but a fresh discovery pass is preferred.

## Current editing capabilities

- Clip in/out adjustment and split-at-playhead.
- Adaptive AI subject crop, full-frame blurred fill, and manual crop.
- 9:16, 16:9, 1:1 and original output dimensions.
- Speed, zoom, focus, brightness, contrast, saturation and volume adjustments.
- Optional fades, audio loudness normalization and source-audio preservation.
- Burned-in captions from the saved transcript, caption styles, position, size, colour/outline and emphasis words.
- AI-proposed opening title and caption/visual treatment.
- MP4 render and download, with output metadata and warnings.

## Not yet implemented (do not assume these work)

- Full multi-track nonlinear editing timeline with draggable video/audio tracks.
- Dedicated lip-sync active-speaker diarization and persistent person identity across arbitrary edits.
- Automatic B-roll search/download/insertion, animated transition packs or motion-graphics templates.
- Keyframe curve editor and arbitrary effect graph.
- Advanced audio restoration, EQ, compression, de-noise and voice isolation.
- One-click TikTok/Instagram/YouTube publishing and channel analytics.
- Full game-specific YOLO models for every title.

These are roadmap items and need independent implementation and real-footage acceptance tests. v0.6.0 should not be described as a complete OpusClip or CapCut replacement.

## Tests

From the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q backend tests
node --check .\backend\static\app.js
```

FFmpeg integration tests are skipped if FFmpeg/FFprobe are not available. The build report documents the tests executed in the build environment and what still needs a Windows/OpenRouter live check.
