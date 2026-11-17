# AuraCut Studio — Phase 2: PeakCut

AuraCut runs locally on your Windows 10 or 11 PC. This phase adds PeakCut: find loud moments in a local video and render a vertical short. The Phase 1 tools remain available.

## Get Phase 2

1. Open PowerShell. Go to your AuraCut folder. If you saved it in a different location, use that folder instead:

   ```powershell
   cd C:\AuraCut-Studio
   ```

2. Download the branch information:

   ```powershell
   git fetch origin
   ```

3. Switch to the Phase 2 work branch. If Git reports uncommitted changes, do not discard them; ask for help first.

   ```powershell
   git switch arena/23bb1466-auracut-studio
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

## Re-check the fixes on your PC

1. Close the old black AuraCut window. In File Explorer, open the repository folder and double-click `Start AuraCut.bat` again.
2. In **System health**, click **Test Gemini now**. A successful real request shows `PASS · Gemini` and `Real Gemini generation test passed`. Before the test, a valid listed model says `Key and model look valid (not tested)`. If the test fails, copy the displayed cause and fix; do not copy your API key.
3. Enter a topic and click **Generate**. It should say **Answered by: Gemini**. If Gemini fails, it falls back to Ollama and shows the Gemini reason.
4. Check the Python PASS line has no `Fix:` text.
5. Test the previously pending error case: stop Ollama. In Notepad, temporarily empty `GEMINI_MODEL=` in `.env` and save it. Close the black AuraCut window, then double-click `Start AuraCut.bat` again. Click **Generate**. A crimson error should explain both the Gemini and Ollama reasons. Restore your original model in `.env` and restart Ollama afterwards.
6. Paste the result of each step and any failure's cause and fix back to your teacher (Claude). Do not include your API key or `.env` contents.

A successful Gemini test confirms one tiny request worked at that moment; it does not guarantee a later generation will succeed. A refreshed page starts with the untested status again.

All processing uses `C:\AuraCut_Workspace\Input_Media` and `C:\AuraCut_Workspace\Output_Renders`, not a Drive path. The app only copies a file to Drive when you click its backup button. A Drive failure does not delete your local file.

## Tests and Phase 0 check

The following command runs mocked tests in PowerShell from the repository folder after the first app start:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Mocked tests do not prove the app works on your PC. A real FFmpeg test runs when ffmpeg and ffprobe are available; it uses CPU encoding and a generated test video. Complete the PeakCut checklist above on your PC. The original Phase 0 system check still runs from the same PowerShell folder:

```powershell
py -3 check_system.py
```
