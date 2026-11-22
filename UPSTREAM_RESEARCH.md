# Upstream GitHub research and design decisions

Research reviewed on 11 November 2026. These repositories informed architecture choices; this project does not wholesale merge or vendor their source code.

## `vxnuaj/reframe`

Repository: <https://github.com/vxnuaj/reframe>

Useful ideas:

- Separate subject detection from crop-path planning and final rendering.
- Make crop paths inspectable JSON so analysis can be cached and rendering repeated at different output qualities.
- Use scene boundaries, subject selection with lock/hold/switch hysteresis, and a damped virtual camera with a dead zone.
- Reassociate tracker IDs when the same subject is briefly re-numbered.
- Keep an interface that can accept boxes from different detectors, including a cloud detector, without changing the camera logic.

Universal Clipper adaptation: OpenRouter supplies sparse normalized subject boxes; OpenCV optical flow bridges them locally; the crop path is smoothed and passed to one FFmpeg renderer. This release does not implement the upstream repository's optional local YOLO or active-speaker model backends.

## `GrepCut/OpenClipper`

Repository: <https://github.com/GrepCut/OpenClipper>

Useful ideas from its published reframe design:

- Detect scene cuts separately from subject detection.
- Track face/person/pose identities across frames rather than centering whichever face is largest in one frame.
- Build a camera path from subject importance and scene context, reset trackers at shot boundaries, and use separate composition for multi-person shots.
- Preserve a two-stage analysis/rendering architecture.

The upstream implementation describes local WinML/DirectML vision models, ByteTrack and an AutoFlip-style renderer. Universal Clipper does not include those local ML models because the product requirement is API-first/no local generative models and a lightweight Windows install. This release uses cloud visual anchors plus local optical flow as a lower-dependency compromise; it is not claimed to match the upstream detector accuracy.

## `Prekzursil/Reframe`

Repository: <https://github.com/Prekzursil/Reframe>

Useful ideas:

- Distinguish single-speaker, two-person split and multi-person composite layouts.
- Use explicit scene-cut resets and stable subject tracking.
- Keep a storyboard/edit plan separate from the renderer.
- Make caption positioning and style editable and export-safe.

The upstream project's published materials include optional local model stacks and note that some GPU validation is environment-dependent. Universal Clipper uses these ideas as design references, not bundled code or models.

## Original four references

- ClipMint: <https://github.com/kleZ799/clipmint> — content-specific ranking, chunked transcript analysis, visual judging and creator workflow.
- Chopify: <https://github.com/mehbul/chopify> — independent audio/visual evidence, cached signal timelines, candidate scoring and caption rendering.
- AutoClip: <https://github.com/artbyjazi/autoclip> — resumable processing stages, provider abstraction, word-index-based highlight boundaries and modular reframing.
- Auto-Clipper: <https://github.com/bendawg2010/Auto-clipper> — gaming profiles, event signals, YOLO/OpenCV hybrid detection and trigger-driven clips.

## Design choice

Universal Clipper uses API-based language/vision/speech reasoning and local deterministic media operations. The AI can rank and interpret; it cannot silently rewrite the user's in/out points. Computer vision tracks only a subject selected by the cloud model and falls back when confidence is low. The renderer is a single canonical implementation so bulk export and manual edit do not use different crop algorithms.

No upstream repository's code or model weights are redistributed in this package. If code is reused in a future release, its exact commit, licence, attribution and model/data terms must be reviewed first.
