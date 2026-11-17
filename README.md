# AuraCut Studio — Phase 1

AuraCut runs locally on your Windows 10 or 11 PC. This phase uploads files to your local workspace, backs up a selected file, and generates basic scene scripts. It does not edit videos.

## Get the Phase 1 code

1. Open PowerShell. Go to your AuraCut folder. If you saved it in a different location, use that folder instead:

   ```powershell
   cd C:\AuraCut-Studio
   ```

2. Download the branch information:

   ```powershell
   git fetch origin
   ```

3. Switch to the Phase 1 branch. If Git reports uncommitted changes, do not discard them; ask for help first.

   ```powershell
   git switch arena/807355ae-auracut-studio
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
5. Ollama defaults to `qwen2.5:7b-instruct-q4_K_M` at `http://127.0.0.1:11434`. Keep Ollama running. Save `.env`, then refresh health on the page.
6. Never share or commit `.env` or your API key.

## Your checklist

1. Start AuraCut. Confirm the browser page opens.
2. In **System health**, Python, ffmpeg, ffprobe, Input_Media, Output_Renders and Ollama should say PASS. Drive should say PASS after you configure it. Gemini can say WARN if you did not configure it. The AMF test says WARN until you run it.
3. Drag a small video into **Drop your video**. Check it appears in the list and in `C:\AuraCut_Workspace\Input_Media` in File Explorer.
4. Click **Back up to Drive** beside that file. Check the file appears in your configured Drive folder. The app verifies the local copy exists and has the same size; it does not verify cloud sync. Existing backup files are not overwritten.
5. Enter a topic and click **Generate**. Check scenes appear, each counter is at or under 880, and the page says **Answered by**.
6. Click **Run GPU encode test**. A PASS means FFmpeg created and checked a two-second clip at `C:\AuraCut_Workspace\Output_Renders\_amf_test.mp4`. A FAIL shows the actual FFmpeg error and a fix.
7. To test the error display, stop Ollama. If Gemini is configured, temporarily leave `GEMINI_MODEL` blank in `.env` and save it, so Gemini cannot answer instead. Click **Generate** again. A clear error should appear. Restart Ollama and restore the model setting afterwards.
8. Paste a screenshot or text of the health card and the result of every checklist step back to your teacher (Claude). Do not include your API key or `.env` contents.

All processing uses `C:\AuraCut_Workspace\Input_Media` and `C:\AuraCut_Workspace\Output_Renders`, not a Drive path. The app only copies a file to Drive when you click its backup button. A Drive failure does not delete your local file.

## Tests and Phase 0 check

The following command runs mocked tests in PowerShell from the repository folder after the first app start:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Mocked tests do not prove the app works on your PC. Complete the checklist above. The original Phase 0 system check still runs from the same PowerShell folder:

```powershell
py -3 check_system.py
```
