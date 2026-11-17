# AuraCut Studio — Phase 0

This phase checks your Windows PC. It does not edit videos, run a server, or upload files.

## Run the check

1. Open PowerShell on your Windows 10 or 11 PC.
2. Change to the folder containing `check_system.py`. For example, if you saved this repository in `C:\AuraCut-Studio`, enter:

   ```powershell
   cd C:\AuraCut-Studio
   ```

3. Run the check:

   ```powershell
   py -3 check_system.py
   ```

4. Copy the full output and paste it back to your teacher (Claude). Do not paste your `.env` file or API key.

Each check prints PASS, WARN, or FAIL. WARN and FAIL include a suggested fix. The last line shows the totals, such as `PASS 5 / WARN 2 / FAIL 0`. Your totals may differ. A FAIL means something needs attention; the script returns exit code 1. A WARN alone does not make the script fail.

The check creates `C:\AuraCut_Workspace\Input_Media` and `C:\AuraCut_Workspace\Output_Renders` if needed and tests whether they are writable. It only checks whether FFmpeg lists `h264_amf`; it does not test actual GPU encoding.

## Optional settings

1. In PowerShell, first change to the folder containing `check_system.py` as described above.
2. If you do not already have an `.env` file, create one:

   ```powershell
   Copy-Item .env.example .env
   ```

3. Open `.env` in Notepad:

   ```powershell
   notepad .env
   ```

4. Optionally put your Gemini key after `GEMINI_API_KEY=`. The check only detects a nonempty value; it does not validate the key.
5. Optionally put your existing Google Drive backup folder's full path after `AURACUT_DRIVE_BACKUP=`, for example `G:\My Drive\AuraCut_Backup`. Save and close Notepad. The folder must already exist. The check only tests local write access, not cloud sync.
6. Run the check again using the command above. Never share or commit `.env`.

All future media processing must use the local `C:\AuraCut_Workspace` folders, not a Google Drive path. Google Drive is only for backups of finished files. This phase does not copy files to Drive.
