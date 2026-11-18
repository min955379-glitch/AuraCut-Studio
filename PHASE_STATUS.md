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
- Drive render backups failed with a size mismatch on the user's PC; the fix below needs verification.
- AI scoring, URL ingest, music, publishing and other later-phase features are not built.

### Verified by user
- User reports analysis and all three framing modes work on both vertical and wide videos on their PC; renders took under one minute. The two-second video displayed the expected short-video error.

## Phase 3 — captions

### Done
- Optional CPU int8 faster-whisper engine with background install and health status; local small/base model cache and selected-clip transcription with word timestamps and VAD.
- PeakCut options, staged progress, ASS word highlighting with an OFL font, same-encode burn, paired SRT output and individual Drive backups. No speech still renders.
- Added mocked builder, transcription, install, render, path-safety and backup tests, plus a real synthetic five-second FFmpeg ASS burn when available. Existing Phase 1/2 tests remain in the suite.

### Not done
- Actual speech transcription has not been run in this sandbox because the model host is unavailable here. The earlier Windows `metadata_errors` failure was subsequently fixed and the user reports captions now run.
- Mocked tests and a Linux FFmpeg burn do NOT prove Windows operation, AMD AMF, Urdu/Hindi shaping, real speech accuracy or Drive behavior on the user's PC. Complete the checklist.

### Verified by user
- The user reports the captions engine install succeeded, saved-model loading and transcription now work, and a 30-second clip takes about 50 seconds to transcribe. Hindi/Urdu as-spoken captions appeared in Devanagari.

## Phase 3 — targeted fixes

### Done
- Source investigation: faster-whisper 1.2.1 `audio.decode_audio` calls `av.open(input_file, mode="r", metadata_errors="ignore")` during transcription of a file path. Its model constructor does not decode audio. A source search found no other speech-engine use of that keyword. Thus the reported failure points to the audio-opening path, not the successful model constructor. Tested PyAV 12.3.0, 13.1.0, 14.2.0, 15.1.0, 16.1.0, 17.0.0 and 18.1.0 accepted this keyword in the Linux sandbox. The user's PyAV version and original traceback are unknown, so a specific version conflict or the exact Windows callee is NOT proven. No dependency pins were changed.
- Avoid the problematic audio-opening path by reading PeakCut's already-extracted mono 16 kHz PCM WAV into a NumPy array; faster-whisper 1.2.1 accepts arrays and skips `decode_audio`. Load models with the same `WhisperModel(size, device='cpu', compute_type='int8', download_root=...)` shape as the user's successful test; force local-only when a complete cached model exists. Full worker tracebacks are recorded in the local workspace `logs/captions.log` and the last 10 lines appear in technical details. Model download and transcription have separate progress/error labels. Added the generated-silence engine test.
- Drive copies now stream, flush and fsync; verification polls every two seconds for up to 90 seconds. Incomplete copies are kept and marked; retries do not overwrite unrelated existing files. Matching name and size is reported as already backed up. The UI displays copy progress.
- PeakCut clears old analysis results at the start and on failure and shows the failure cause beside its status.
- Added mocked coverage for cache reuse, separate failures, tracebacks, Drive retries/polling and failed-analysis UI. The 62-test suite passed in Linux (one real-transcription test skipped because the small model is not cached). A generated two-second FFmpeg PCM waveform loaded into the same samples as PyAV. The real two-second silence transcription test skips if the model is not locally cached. No models were downloaded in this sandbox.

### Not done
- Google Drive size polling and the engine test were not specifically reported as verified. Mocked tests do NOT prove Windows operation; the original PyAV failure was not reproducible here without the user's original traceback and PyAV version.

### Verified by user
- User confirms saved-model loading and caption transcription now work on Windows; the metadata_errors failure is fixed.

## Phase 3B — Roman captions and automatic backup

### Done
- Four caption language modes: English translation, Roman Hindi, Roman Urdu and original script. Gemini-first/Ollama Roman conversion uses bounded chunks, validates word alignment and English preservation, retries then falls back to timed lines or original text with a warning. Converted clips are cached locally across styles.
- No-speech renders no longer create an empty SRT. Render output is published before Drive copying; background automatic backup tracks pending files and supports explicit retry. Source uploads are not auto-backed-up by default.
- The existing merged OFL font covers tested Devanagari and Arabic/Urdu sample glyphs and has `deva`, `dev2`, and `arab` shaping tables; no additional font was added. Real display on Windows still needs checking.
- Added mocked Roman, mode, cache, Drive, pending retry, and file-label tests. Linux regression run: 79 tests, 77 passed and 2 skipped (optional speech engine/real transcription unavailable); real generated-video FFmpeg render and subtitle burn passed. A separate FFmpeg burn selected the bundled font for Hindi/Urdu sample glyphs.

### Not done
- Roman quality depends on the AI model and must be judged by the user. Mocked tests do NOT prove this works on the user's PC; real AI conversion, script display, Windows AMF, Drive and backup recovery require the checklist. Network access to actual Gemini/Ollama and model hosting was not available in this sandbox.

### Verified by user
- Not yet for Phase 3B.

### Ideas for later
- Caption rewriting and emoji, translation beyond English, captions outside PeakCut, AI Auto-Editor, music/SFX, speed ramps, face tracking, URL ingest, thumbnails, story tabs, Director Chat, Asset Hunter, scheduling, publishing/OAuth, executable packaging and database. None are implemented in Phase 3.
