# Phase status

## Phase 0 — system check

### Done
- Standard-library system check, example settings, ignore rules, and Windows instructions.

### Not done
- None reported for Phase 0.

### Verified by user
- User reported PASS 9 / WARN 2 / FAIL 0 on their PC: Python 3.12.10, ffmpeg and h264_amf listed, Ollama and `qwen2.5:7b-instruct-q4_K_M` present.

## Phase 1 — core server

### Done
- Local server, page, upload, manual Drive backup, health checks, GPU encode test, logs, and Gemini-first/Ollama script generation.
- Gemini timeout and health diagnostics fixes.

### Not done
- The user has not specifically reported the both-providers-down error checklist result.

### Verified by user
- User reports Phase 1 done and verified on their PC: upload, Drive backup, GPU encode test, health, Ollama scripts, and Gemini using `gemini-3.5-flash-lite`. Larger flash models returned 503 overload.

## Phase 2 — PeakCut

### Done
- Local audio-loudness analysis, non-overlapping ranked windows, and evenly spaced fallback for videos with no audio.
- Background analysis/render jobs, progress reporting, three vertical framing modes, AMF-to-CPU fallback, output verification, and manual Drive backup button.
- Added mocked tests and a real generated-video CPU test when FFmpeg and ffprobe are available.
- The old reference files were absent from this checkout; the implementation uses a new design rather than importing them.

### Not done
- Phase 2 has not yet been verified on the user's Windows PC.
- Mocked tests do NOT prove the tool works on the user's PC; only the user's checklist can verify that.
- Captions, transcription, AI scoring, URL ingest, music, publishing and other later-phase features are not built.

### Verified by user
- Not yet.

### Ideas for later
- None.
