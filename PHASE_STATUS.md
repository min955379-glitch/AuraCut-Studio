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
- Added a localhost launcher, single offline-asset page, health checks, real GPU encode test, uploads, explicit Drive backup and recent logs.
- Added stateless Gemini-first/Ollama fallback, scene validation and up to two shortening retries.
- Added mocked unittest coverage for router, prompt limits, backup and upload safety.

### Not done
- The user's Windows checklist has not yet been completed for Phase 1.
- Mocked tests do NOT prove the app works on the user's PC; only the user's checklist can verify that.
- Video editing, clipping, captions, voices, publishing and other later-phase features are not built.

### Verified by user
- Not yet.

### Ideas for later
- None.
