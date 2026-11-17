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
- The user has not reported the Drive render backup or short-video error checklist results.
- AI scoring, URL ingest, music, publishing and other later-phase features are not built.

### Verified by user
- User reports analysis and all three framing modes work on both vertical and wide videos on their PC; renders took under one minute.

## Phase 3 — captions

### Done
- Optional CPU int8 faster-whisper engine with background install and health status; local small/base model cache and selected-clip transcription with word timestamps and VAD.
- PeakCut options, staged progress, ASS word highlighting with an OFL font, same-encode burn, paired SRT output and individual Drive backups. No speech still renders.
- Added mocked builder, transcription, install, render, path-safety and backup tests, plus a real synthetic five-second FFmpeg ASS burn when available. Existing Phase 1/2 tests remain in the suite.

### Not done
- First model download and actual speech transcription have not been run in this sandbox; network access to the model host is unavailable here.
- Mocked tests and a Linux FFmpeg burn do NOT prove Windows operation, AMD AMF, Urdu/Hindi shaping, real speech accuracy or Drive behavior on the user's PC. Complete the checklist.

### Verified by user
- Not yet.

### Ideas for later
- Romanized Urdu/Hindi captions, caption rewriting and emoji, translation beyond English, captions outside PeakCut, AI Auto-Editor, music/SFX, speed ramps, face tracking, URL ingest, thumbnails, story tabs, Director Chat, Asset Hunter, scheduling, publishing/OAuth, executable packaging and database. None are implemented in Phase 3.
