"""Phase 0: check prerequisites without installing any Python packages."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "qwen2.5:7b-instruct-q4_K_M"
OLLAMA_URL = "http://127.0.0.1:11434/api/tags"
ENV_FILE = Path(__file__).resolve().parent / ".env"
WORKSPACE = Path(r"C:\AuraCut_Workspace")
TIMEOUT = 8
counts = {"PASS": 0, "WARN": 0, "FAIL": 0}


def report(level, item, detail, fix=None):
    counts[level] += 1
    print("{} | {} | {}".format(level, item, detail))
    if level != "PASS":
        print("       Fix: {}".format(fix))


def check_python():
    version = "{}.{}.{}".format(*sys.version_info[:3])
    if sys.version_info >= (3, 11):
        report("PASS", "Python", "Version {} (3.11 or newer required).".format(version))
    else:
        report("FAIL", "Python", "Version {} is older than 3.11.".format(version),
               "Install Python 3.11 or newer, then run this check again.")


def check_program(name):
    path = shutil.which(name)
    if not path:
        report("FAIL", name, "Not found on PATH.",
               "Install FFmpeg and add its bin folder to your Windows PATH; reopen PowerShell.")
        return None
    try:
        result = subprocess.run([path, "-version"], capture_output=True, text=True,
                                errors="replace", timeout=TIMEOUT, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip() or "No error details returned"
            report("FAIL", name, "Could not run {}: {}".format(path, detail),
                   "Repair or reinstall FFmpeg, then reopen PowerShell.")
            return None
        report("PASS", name, "Found and ran {}.".format(path))
        return path
    except (OSError, subprocess.TimeoutExpired) as exc:
        report("FAIL", name, "Could not run {}: {}".format(path, exc),
               "Repair or reinstall FFmpeg, then reopen PowerShell.")
        return None


def check_amf(ffmpeg):
    if not ffmpeg:
        report("WARN", "h264_amf", "Cannot check the encoder because ffmpeg is unavailable.",
               "Install FFmpeg with AMD AMF support and run this check again.")
        return
    try:
        result = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True,
                                text=True, errors="replace", timeout=TIMEOUT, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip() or "No error details returned"
            report("WARN", "h264_amf", "Could not list encoders: {}".format(detail),
                   "Repair or reinstall an FFmpeg build with AMD AMF support.")
        elif any("h264_amf" in line.split() for line in result.stdout.splitlines()):
            report("PASS", "h264_amf", "Encoder listed by FFmpeg; actual GPU encoding was not tested.")
        else:
            report("WARN", "h264_amf", "Encoder is not listed by this FFmpeg build.",
                   "Install an FFmpeg build with AMD AMF support.")
    except (OSError, subprocess.TimeoutExpired) as exc:
        report("WARN", "h264_amf", "Could not list encoders: {}".format(exc),
               "Repair or reinstall an FFmpeg build with AMD AMF support.")


def check_ollama():
    try:
        # Ignore system proxy settings: this is a local service, not a network proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(OLLAMA_URL, timeout=TIMEOUT) as response:
            payload = json.load(response)
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list) or any(not isinstance(model, dict) for model in models):
            raise ValueError("Ollama returned an invalid model list")
        report("PASS", "Ollama", "Reachable at http://127.0.0.1:11434.")
        names = [model.get("name", model.get("model", "")) for model in models]
        if MODEL in names:
            report("PASS", "Ollama model", "{} is installed.".format(MODEL))
        else:
            report("FAIL", "Ollama model", "{} is not in the local model list.".format(MODEL),
                   "In PowerShell, run: ollama pull {}".format(MODEL))
    except (urllib.error.URLError, OSError, ValueError, UnicodeError) as exc:
        report("FAIL", "Ollama", "Cannot check {}: {}".format(OLLAMA_URL, exc),
               "Install and start Ollama, then run this check again.")
        report("WARN", "Ollama model", "Cannot check the model while Ollama is unavailable.",
               "Start Ollama, then in PowerShell run: ollama pull {}".format(MODEL))


def read_env():
    if not ENV_FILE.exists():
        return {}, "{} does not exist".format(ENV_FILE)
    values = {}
    try:
        with ENV_FILE.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    return {}, "Invalid .env line {} (expected NAME=value)".format(line_number)
                key, value = line.split("=", 1)
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                values[key.strip()] = value
        return values, None
    except (OSError, UnicodeError) as exc:
        return {}, "Cannot read {}: {}".format(ENV_FILE, exc)


def check_gemini(env, env_error):
    if env_error:
        report("WARN", "Gemini API key", env_error + ".",
               "Copy .env.example to .env and optionally add GEMINI_API_KEY.")
    elif env.get("GEMINI_API_KEY", "").strip():
        report("PASS", "Gemini API key", "A value is set in .env (not validated).")
    else:
        report("WARN", "Gemini API key", "GEMINI_API_KEY is empty or missing in .env (optional).",
               "Optionally add your Gemini API key to .env; do not share the key.")


def check_port():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(("0.0.0.0", 8000))
        report("PASS", "Port 8000", "Available to bind on IPv4 interfaces right now.")
    except OSError as exc:
        report("FAIL", "Port 8000", "Cannot bind port 8000: {}".format(exc),
               "In PowerShell, run: netstat -ano | findstr :8000; stop the process using the port.")


def check_writable_folder(label, folder, create, optional=False):
    level = "WARN" if optional else "FAIL"
    fix = ("Set AURACUT_DRIVE_BACKUP in .env to an existing writable Drive folder."
           if optional else "Create the folder shown above and give your Windows account write permission.")
    try:
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        if not folder.is_dir():
            report(level, label, "Folder does not exist or is not a directory: {}.".format(folder), fix)
            return
        # NamedTemporaryFile deletes the small write probe when closed, even on Windows.
        with tempfile.NamedTemporaryFile(prefix="auracut_check_", dir=str(folder)) as probe:
            probe.write(b"AuraCut write check")
            probe.flush()
        report("PASS", label, "Exists and is writable: {}.".format(folder))
    except (OSError, ValueError) as exc:
        report(level, label, "Cannot create or write to {}: {}".format(folder, exc), fix)


def check_workspace():
    if os.name != "nt":
        for name in ("Input_Media", "Output_Renders"):
            report("FAIL", name, "Windows workspace cannot be checked on this operating system.",
                   "Run this script on your Windows 10 or 11 PC.")
        return
    for name in ("Input_Media", "Output_Renders"):
        check_writable_folder(name, WORKSPACE / name, create=True)


def check_drive(env, env_error):
    if env_error:
        report("WARN", "Google Drive backup", env_error + ".",
               "Copy .env.example to .env and set AURACUT_DRIVE_BACKUP to your Drive folder.")
        return
    value = env.get("AURACUT_DRIVE_BACKUP", "").strip()
    if not value:
        report("WARN", "Google Drive backup", "AURACUT_DRIVE_BACKUP is not set in .env.",
               "Set AURACUT_DRIVE_BACKUP in .env to an existing writable Drive folder.")
        return
    folder = Path(value)
    if not folder.is_absolute():
        report("WARN", "Google Drive backup", "The backup path is not absolute: {}.".format(value),
               "Set AURACUT_DRIVE_BACKUP to the full path of an existing Drive folder.")
        return
    check_writable_folder("Google Drive backup", folder, create=False, optional=True)


def main():
    print("AuraCut Studio - Phase 0 system check")
    print("This check does not test GPU encoding or upload files.\n")
    check_python()
    ffmpeg = check_program("ffmpeg")
    check_program("ffprobe")
    check_amf(ffmpeg)
    check_ollama()
    env, env_error = read_env()
    check_gemini(env, env_error)
    check_port()
    check_workspace()
    check_drive(env, env_error)
    print("\nPASS {} / WARN {} / FAIL {}".format(counts["PASS"], counts["WARN"], counts["FAIL"]))
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
