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
- Fixed Gemini's timeout (default 12 seconds, minimum 10 with warning), added a real Gemini test, removed Fix text from PASS lines, and added 404 method/path logging with a 204 favicon. These fixes await the user's re-check.
- Added mocked unittest coverage for the fixes, router, prompt limits, backup and upload safety.

### Not done
- Checklist step 7 (both providers unavailable, clear error) is pending user testing.
- The Gemini fix and diagnostic changes await the user's re-check.
- Mocked tests do NOT prove the tool works on the user's PC; only the user's checklist can verify that.
- Video editing, clipping, captions, voices, publishing and other later-phase features are not built.

### Verified by user
- The user reported Phase 1 steps 1–6 working on 2026-11-17: page and health card, upload, verified Drive copy, Ollama script with prompts under 880 characters, and a successful 2.00-second h264_amf GPU test. Gemini itself did not work and the fixes have not yet been verified.

### Ideas for later
- None.
