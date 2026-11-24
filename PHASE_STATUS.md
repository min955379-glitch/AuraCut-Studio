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
- Earlier manual Drive render backups failed with a size mismatch; the user subsequently reported that Phase 3B automatic Drive backup works.
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
- Roman spelling quality depends on the AI model. Mocked tests do NOT prove every Windows or Drive recovery case; no real Gemini/Ollama conversion was tested in this sandbox.

### Verified by user
- User reports English, Roman Hindi, Roman Urdu, and Original captions and automatic Drive backup work on their PC. The captions are accurate but appear too small and plain to them.

## Phase 3C — Ollama auto-start only

### Done
- The Windows launcher checks local Ollama, starts its tray app or hidden server when needed, waits up to 20 seconds, and reports the result without preventing AuraCut startup if Ollama is unavailable.
- Auto-start defaults to enabled and can be disabled in `.env`; a missing default model is reported with the exact pull command, never downloaded automatically. Ollama is not stopped when AuraCut closes.
- Added mocked launcher tests for the running, starting, timeout, missing, disabled, and model-check cases. Linux regression run: 100 tests, 97 passed and 3 skipped (optional speech engine, NumPy, or font inspection unavailable). Mocked tests do NOT prove this works on the user's PC.

### Not done
- User verification of Phase 3C auto-start on Windows is still needed. At the time of this auto-start task, professional caption styling and preview were deferred by the user; see the separate caption-styling update below.

### Verified by user
- Not yet for Phase 3C auto-start.

### Ideas for later
- Caption rewriting and emoji, translation beyond English, captions outside PeakCut, AI Auto-Editor, music/SFX, speed ramps, face tracking, URL ingest, thumbnails, story tabs, Director Chat, Asset Hunter, scheduling, publishing/OAuth, executable packaging and database. None are implemented in Phase 3.


## Phase 3C — professional caption styling and preview

### Done
- Added Bold Gold Glow, Clean Ivory Pro and Steel Blue Glow, with 72/88/104 base sizes, ALL CAPS display control, 90 px side margins and 420 px bottom margin. Caption ASS events use at most four words and two lines; long words are scaled rather than cut. SRT preserves original casing.
- Added a 1080x1920 PNG preview endpoint and PeakCut controls. Preview and video use the same ASS builder, relative ASS filter and bundled font. Preview temporary files stay in the local workspace, and preview does not need the optional speech engine. Legacy style names remain accepted for existing API clients.
- Linux regression: 106 tests run, 101 passed and 5 skipped. Real FFmpeg PNG previews for all presets and a short synthetic PeakCut-filter video burn passed. Nine preset/size previews took 0.050–0.067 seconds each in this sandbox; Windows speed is unverified. Two older real-media tests skipped because ffprobe was unavailable; optional faster-whisper, NumPy and fonttools checks also skipped. Mocked tests do not prove Windows rendering.

### Not done / not verified
- User has not verified the presets, preview speed, Unicode shaping, clipping or matching rendered captions on Windows. The under-two-second target depends on PC and FFmpeg; it is not guaranteed for every system. The sizing heuristic for unusually wide or complex-script text needs real-world checking.
- This task does not add Auto-Editor, publishing, OAuth, story tabs, music or other later features. Ollama auto-start remains separately unverified by the user.

### Verified by user
- Not yet for professional caption styling and preview.


## Phase 3C-Fix — caption size and line spacing

### Done
- The user reported a 0.13-second Windows preview and good placement, but text was too small. Before this fix, the Linux FFmpeg Large Bold Gold Glow preview measured 314 px wide on its first line; solid ivory capitals were 27 px tall and the popped gold capitals were 31 px tall. The second line measured 227 px wide; visible fill-to-fill line gap was 57 px.
- The ASS canvas already matched 1080x1920. The bundled font has a 714-unit cap height in a 2112-unit ascender/descender box, so libass produced capitals much shorter than the ASS font-size number. The previous character-count width heuristic also reduced the Large sample from 88 to 80 ASS units. The font's large line box caused loose line spacing.
- Calibrated ASS sizes for visible capital height and placed each line separately with tighter spacing. Glyph-advance-based grouping keeps words intact and shrinks only an oversized group; unsupported glyphs use a conservative estimate. Preview and PeakCut still share the same ASS builder, bundled font and filter. SRT and Roman/Drive behavior were not changed. No dependencies changed.
- On this Linux sandbox, after the fix, the Large Bold Gold Glow first line measured 772 px wide with 66 px ivory and 76 px popped gold capitals; the second line was 558 px wide and 66 px tall, with a 23 px fill-to-fill gap. Sizes and glyphs can render differently on other FFmpeg/libass/font setups.
- Real FFmpeg pixel tests covered 3 presets by 3 sizes, 1080x1920 PeakCut-filter synthetic video at all sizes, and long Latin, Hindi and Urdu samples including every spoken-word highlight. All passed locally. A full suite run passed 106 of 110 tests; 4 skipped because ffprobe, optional faster-whisper or NumPy were not available. No Windows speech or AMD test was performed.

### User PC result
- Awaiting the user's Windows preview and real speech-render results, including screenshots and any clipping or shaping problems.


## Phase 3C-Fix 2 — active-word line overlap

### Done
- The user reports caption size and placement look good on their Windows PC, but a gold word on line 2 touched the line above in a real render.
- Reproduced in Linux FFmpeg with Large Bold Gold Glow, ALL CAPS: “AND THE LITTLE” above active “PUT”. Before the change, solid ivory ended at y=1383 and gold began at y=1379: a 5 px fill overlap. When measured separately, outline/glow bounds overlapped by 40 px.
- The 115% lower-line vertical pop grew upward while line spacing used only the normal-size estimate. Spacing now uses the actual font glyph bounds, active-word scaling, outline and glow allowance. The lower line stays anchored; the top line may move slightly as the active word changes. The original 115% pop and font sizes remain. Preview and render share the same builder; the preview now shows gold on line 2.
- After the change, the same sandbox sample has 55 px between solid fills, or 20 px clear between the full styled bounds. Real FFmpeg tests measure both active-line positions for all 3 presets and 3 sizes, all preview variants, a synthetic PeakCut video, and additional lowercase, Hindi and Urdu examples. Existing size and margin pixel checks were rerun. Linux full suite: 114 tests, 109 passed and 5 skipped (two need ffprobe; others need faster-whisper, NumPy or fonttools). No speech or Windows AMD hardware test was performed here.
- Existing spacing assertion was updated because its previous fill-gap maximum excluded outlines/glow and conflicted with a clear styled gap. The existing pixel-size, width, margin and safe-zone checks remain in place. No dependencies, Roman conversion, Drive or Ollama code were changed.

### User PC result
- Awaiting the user's new Windows real-render frame and any reported errors or unwanted line movement.
