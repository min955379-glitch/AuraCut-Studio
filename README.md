# AuraCut Studio — Phase 3C: Ollama auto-start

AuraCut runs locally on your Windows 10 or 11 PC. This phase adds PeakCut: find loud moments in a local video and render a vertical short. The Phase 1 tools remain available.

## Get Phase 3C

1. Open PowerShell. Go to your AuraCut folder. If you saved it in a different location, use that folder instead:

   ```powershell
   cd C:\AuraCut-Studio
   ```

2. Download the branch information:

   ```powershell
   git fetch origin
   ```

3. Switch to the Phase 3C work branch. If Git reports uncommitted changes, do not discard them; ask for help first.

   ```powershell
   git switch arena/d3ad0999-auracut-studio
   ```

4. Pull updates to that branch:

   ```powershell
   git pull
   ```

## Start the app

1. In File Explorer, open `C:\AuraCut-Studio` (or wherever you saved the repository).
2. Double-click `Start AuraCut.bat`. Alternatively, in PowerShell in that folder run:

   ```powershell
   py -3 run.py
   ```

3. The first run creates `.venv` and installs Python packages. This requires internet; it can take a few minutes. Keep the window open. Later runs skip installation unless the requirements change.
4. Your browser should open `http://127.0.0.1:8000`. If it does not, enter that address in the browser yourself. The server listens only on your own PC.
5. Keep the PowerShell or batch window open while using AuraCut. Press Ctrl+C in that window to stop the server.

If port 8000 belongs to another program, the launcher reports the process when Windows can identify it. Close that program yourself and retry. AuraCut never stops it for you. If AuraCut is already running, the launcher opens the existing page.

## Ollama auto-start

AuraCut checks `http://127.0.0.1:11434` before starting. If Ollama is already running, the black window says **Ollama is already running**. Otherwise it tries to start Ollama and waits up to 20 seconds. A successful start says **Starting Ollama...** and then **Ollama is ready**. AuraCut does not stop Ollama when it closes.

This is enabled by default, including when your existing `.env` has no new setting. To turn it off, open PowerShell in your AuraCut folder and run:

```powershell
notepad .env
```

Add `AURACUT_START_OLLAMA=false`, or change the existing line if it is already there. Save the file and restart AuraCut. Use `true` to turn auto-start back on. The app still checks an already-running Ollama when auto-start is off.

If Ollama is not installed or does not start, the window explains the cause and fix; AuraCut continues starting and can still use Gemini. Install Ollama from `https://ollama.com` if necessary. The local startup log is `C:\AuraCut_Workspace\logs\ollama-start.log` when available. Do not paste secrets from logs.

If the window says the default model is missing, install it yourself in PowerShell; AuraCut does not download it automatically:

```powershell
ollama pull qwen2.5:7b-instruct-q4_K_M
```

### Check on your PC

1. Close the old AuraCut black window. Right-click Ollama's tray icon near the Windows clock and choose Quit.
2. In File Explorer, double-click `Start AuraCut.bat`. The black window should say **Starting Ollama...** then **Ollama is ready**. In the browser's **System health**, check **Ollama: PASS**.
3. Close the AuraCut black window, but leave Ollama running. Double-click `Start AuraCut.bat` again. The window should say **Ollama is already running**.
4. Send your teacher the result of each step and the exact warning text, if any. Do not send your API key or `.env` contents.

Mocked tests do not prove auto-start works on your Windows PC. The Ollama PASS line also requires the configured model to be installed.

## Configure optional Gemini and Drive settings

1. In PowerShell, open the repository folder. If `.env` does not already exist, make it from the example:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Open it in Notepad:

   ```powershell
   notepad .env
   ```

3. For Drive backups, set `AURACUT_DRIVE_BACKUP` to an **existing** Google Drive folder, for example `G:\My Drive\AuraCut_Backup`. Start Google Drive for desktop first. Leave the workspace set to `C:\AuraCut_Workspace`.
4. Gemini is optional. To enable it, enter both `GEMINI_API_KEY` and `GEMINI_MODEL`. If the model is blank or invalid, the System health card lists available model names when the key can be checked. Pick one and save `.env`.
5. Gemini's request timeout defaults to 12 seconds, even if your existing `.env` has no timeout setting. To change it, add `GEMINI_TIMEOUT_SECONDS=12` to `.env` and change the number. Values below 10 are raised to 10 seconds and shown as a warning. Invalid values use 12 seconds and show a warning.
6. Ollama defaults to `qwen2.5:7b-instruct-q4_K_M` at `http://127.0.0.1:11434`. Keep Ollama running. Save `.env`, then refresh health on the page.
7. Never share or commit `.env` or your API key.

## Try Phase 3B

1. If your existing `.env` already has `AURACUT_DRIVE_BACKUP` set, automatic render backup defaults to ON even without new lines. To make it explicit, in PowerShell in your repository folder run:

   ```powershell
   notepad .env
   ```

   Add these lines at the end, then save and close Notepad. If either line already exists, change it instead of adding a duplicate:

   ```text
   AURACUT_AUTO_BACKUP=true
   AURACUT_AUTO_BACKUP_INPUTS=false
   ```

   The second line keeps source uploads from being automatically copied. Set the first to `false` to turn automatic backup off. Restart AuraCut after editing `.env`.
2. Close the old black window, double-click `Start AuraCut.bat`, and press **Ctrl+F5** in the browser. In PeakCut tick **Add captions**. The four modes appear; **Roman Hindi + English words** is selected by default.
3. Find a Hindi/Urdu speech moment and render in Roman Hindi. Check Latin spelling, English words, timing, and gold highlighting. Render the same moment in Roman Urdu, English only, and Original script. A repeated Roman render with the same source, clip, speech model and language uses the saved conversion, even if the caption style changes. If AI conversion fails, original text is used with a warning; fallback line timing is approximate.
4. Open the matching `.srt` in Notepad and compare its words with the video. A no-speech render creates no SRT.
5. Watch the separate Drive backup message after the video appears. It should say **Backed up to Drive**. Verify file sizes: in PowerShell, run the first line below; for the second, replace the example path with your actual Drive folder.

   ```powershell
   Get-ChildItem -LiteralPath 'C:\AuraCut_Workspace\Output_Renders' | Select-Object Name,Length
   Get-ChildItem -LiteralPath 'G:\My Drive\AuraCut_Backup' | Select-Object Name,Length
   ```

   Compare the numbers in **Length** for the same `.mp4` and `.srt`. Size verification does not guarantee cloud sync has completed.
6. Quit Google Drive for desktop and render another clip. The local render should finish and show a backup-pending warning. Start Drive again and click **Back up all pending files**. Do not remove partial copies; unrelated files with the same name are never overwritten.
7. Render a music-only moment and check there is no new empty `.srt`. Speech detection can mistake music for speech.
8. Paste each result, times, red errors and technical details to teacher Claude, plus 10 lines of Roman captions for spelling review. Do not paste your API key or `.env`.

## Try PeakCut on your PC

1. Close the old black AuraCut window. In File Explorer, open your AuraCut repository folder and double-click `Start AuraCut.bat`.
2. In the browser, find **04 / PeakCut**. Check the video dropdown lists files already in `C:\AuraCut_Workspace\Input_Media`. If needed, use **Drop your video** first and click **Refresh videos**.
3. Pick your 221 MB phone video. Set **Clip seconds** to 30 and **Highlights** to 3. Click **Find best moments**. Note how long it takes. Results show rank, time range and loudness score. If there is no audio, evenly spaced moments are used instead. Fewer results may appear if the video is too short to fit all requested clips without overlap.
4. Choose **Whole frame + blur**. Click **Render this** for rank 1. Wait for the progress to reach 100%. Note how long it takes. In File Explorer, open `C:\AuraCut_Workspace\Output_Renders` and play the finished file. Check that it is vertical and has sound.
5. Render the same moment using **Zoom + blur**, then **Full screen crop**. Play each result and check that it looks right. If the GPU fails, a warning says CPU was used instead; CPU rendering is slower.
6. Click **Back up to Drive** below a finished render. Check the file appears in your configured Drive folder. The copy is verified by size, not by cloud-sync status.
7. To try the short-video error, copy `_amf_test.mp4` from `C:\AuraCut_Workspace\Output_Renders` into `C:\AuraCut_Workspace\Input_Media` using File Explorer. In PeakCut click **Refresh videos**, choose the copy, then click **Find best moments**. The two-second video should show a clear red error explaining that at least 15 seconds are required.
8. Paste the result of every step, the analysis and render times, and any red error text exactly to your teacher (Claude). Do not include your API key or `.env` contents.

PeakCut uses audio loudness, not AI, to rank moments. Clips are 1080 × 1920 at 30 frames per second. If a video is at least 15 seconds but shorter than the requested length, PeakCut uses the whole video. Finished files are never overwritten. Jobs are cleared when the app stops.

## Check the Phase 3 fixes

1. Close the old black AuraCut window, double-click `Start AuraCut.bat`, and press **Ctrl+F5** in the browser. You do **not** need to reinstall the captions engine for this fix; no dependency pins changed.
2. In **System health**, click **Test captions engine**. It loads the small model and transcribes two seconds of generated silence locally. Wait for **PASS**; the first use may download about 500 MB. A saved complete model is reused. If it fails, expand **Show technical details** in the red error and copy the text.
3. Find a clear speech moment, tick **Add captions**, choose **Clean Ivory** and **Original script**, then render. Check the words and gold highlight are in sync.
4. Back up the video and the matching `.srt` separately. Watch the copying progress. Verification can take up to 90 seconds; if incomplete, the local file and partial Drive copy remain. Wait and click backup again. Check both Drive copies have the same size as the originals. A same-sized existing file is reported as **Already backed up**; unrelated files with different sizes are never overwritten.
5. Find moments successfully, then try a two-second video. The failed analysis should not show old results, and the cause should be beside **Failed**.
6. Tell teacher Claude the result of each step, times, and any red error and technical details. Do not share API keys. Full speech tracebacks are also saved locally in `C:\AuraCut_Workspace\logs\captions.log`.

## Try captions on your PC

1. Close the old black AuraCut window. Open your repository folder in File Explorer, double-click `Start AuraCut.bat`, and press **Ctrl+F5** in the browser tab. The first app startup may install core packages.
2. In **System health**, look for **Captions engine: WARN** (not installed). In **04 / PeakCut**, the disabled checkbox explains that installation is needed.
3. Click **Install captions engine**. Leave the window open. It may take several minutes depending on internet speed. Wait for **Captions engine: PASS**. If it fails, copy the complete red error and its fix.
4. Select a video with clear speech and click **Find best moments**. Tick **Add captions**, select **Bold Gold**, and render a moment. The first use of the default **small** model downloads about 500 MB into `C:\AuraCut_Workspace\models` and may take several minutes. Watch the stage message and wait; later uses reuse it. The optional **base** model is smaller and faster but can be less accurate. The first use of base also downloads it.
5. Open `C:\AuraCut_Workspace\Output_Renders` in File Explorer and play the new video. Check words follow the voice, the active word is gold, and captions leave the lower 380 pixels clear. Note transcription and render times.
6. Render the same moment with **Clean Ivory**. Play and compare it.
7. Open the matching `.srt` in Notepad: it should contain numbered captions with clip-relative timestamps. A no-speech clip has no SRT.
8. Click the video and SRT **Back up to Drive** buttons separately; check both appear in your configured Drive folder. AuraCut checks existence and size, not cloud-sync completion.
9. With captions enabled, render a moment from a music-only video. The video should still be delivered and say **No speech found, rendered without captions**. Speech detection may occasionally mistake music for speech.
10. Send your teacher (Claude) the result of each step, install time, first model-download time, transcription time, render time, and every red error's exact text. Do not send your API key or `.env` contents.

Captions run locally on the CPU, without PyTorch. Speech inference works from the cached model without internet. Roman conversion uses Gemini when configured (caption text is sent to Google), then local Ollama if Gemini fails. Choose Auto, English, Urdu or Hindi for the spoken language and one of the four caption language modes. Accuracy and script shaping still need checking on your own PC. Models and temporary audio stay in the local workspace; models are never sent to Drive. If the optional engine is missing, a requested render continues without captions and gives a warning. If downloading or transcription fails, the job reports the cause and fix instead of silently continuing.

## Re-check the fixes on your PC

1. Close the old black AuraCut window. In File Explorer, open the repository folder and double-click `Start AuraCut.bat` again.
2. In **System health**, click **Test Gemini now**. A successful real request shows `PASS · Gemini` and `Real Gemini generation test passed`. Before the test, a valid listed model says `Key and model look valid (not tested)`. If the test fails, copy the displayed cause and fix; do not copy your API key.
3. Enter a topic and click **Generate**. It should say **Answered by: Gemini**. If Gemini fails, it falls back to Ollama and shows the Gemini reason.
4. Check the Python PASS line has no `Fix:` text.
5. Test the previously pending error case: stop Ollama. In Notepad, temporarily empty `GEMINI_MODEL=` in `.env` and save it. Close the black AuraCut window, then double-click `Start AuraCut.bat` again. Click **Generate**. A crimson error should explain both the Gemini and Ollama reasons. Restore your original model in `.env` and restart Ollama afterwards.
6. Paste the result of each step and any failure's cause and fix back to your teacher (Claude). Do not include your API key or `.env` contents.

A successful Gemini test confirms one tiny request worked at that moment; it does not guarantee a later generation will succeed. A refreshed page starts with the untested status again.

All processing uses `C:\AuraCut_Workspace\Input_Media` and `C:\AuraCut_Workspace\Output_Renders`, not a Drive path. If automatic backup is enabled, finished PeakCut files are also copied in the background. Manual backup buttons remain available. A Drive failure does not delete your local file.

## Tests and Phase 0 check

The following command runs mocked tests in PowerShell from the repository folder after the first app start:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Mocked tests do not prove the app works on your PC. A real FFmpeg test runs when ffmpeg and ffprobe are available; it uses CPU encoding and a generated test video. Complete the PeakCut checklist above on your PC. The original Phase 0 system check still runs from the same PowerShell folder:

```powershell
py -3 check_system.py
```
