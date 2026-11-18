"""First-run setup and local-only launcher; uses only Python's standard library."""
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
import webbrowser

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8000'
VENV = ROOT / '.venv'
PYTHON = VENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
MARKER = VENV / '.auracut-requirements-sha256'
OLLAMA_URL = 'http://127.0.0.1:11434'
OLLAMA_MODEL = 'qwen2.5:7b-instruct-q4_K_M'
IS_WINDOWS = sys.platform == 'win32'


def error(what, cause, fix):
    print('FAIL | {} | {}'.format(what, cause), flush=True)
    print('       Fix: {}'.format(fix), flush=True)


def ollama_autostart_enabled():
    """Read the one launcher setting without requiring the app's dependencies."""
    try:
        text = (ROOT / '.env').read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        return True
    except (OSError, UnicodeError) as exc:
        print('WARN | Ollama auto-start | Cannot read .env: {} | Fix: Check .env permissions and UTF-8 encoding.'.format(exc), flush=True)
        return False
    value = ''
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, candidate = line.split('=', 1)
        if key.strip() == 'AURACUT_START_OLLAMA':
            value = candidate.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
    value = value.lower()
    if not value or value in ('true', 'yes', '1', 'on'):
        return True
    if value in ('false', 'no', '0', 'off'):
        return False
    print('WARN | Ollama auto-start | AURACUT_START_OLLAMA is not true or false | Fix: Set AURACUT_START_OLLAMA=true or false in .env.', flush=True)
    return False


def ollama_probe(timeout=0.8):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(OLLAMA_URL + '/', timeout=timeout) as response:
            if not 200 <= response.status < 300:
                return False, 'HTTP {}'.format(response.status)
        return True, ''
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return False, '{}: {}'.format(type(exc).__name__, exc)


def check_ollama_model():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(OLLAMA_URL + '/api/tags', timeout=4) as response:
            data = json.load(response)
        models = data.get('models') if isinstance(data, dict) else None
        if not isinstance(models, list) or any(not isinstance(model, dict) for model in models):
            raise ValueError('Ollama returned an invalid model list')
        if OLLAMA_MODEL not in [model.get('name', model.get('model', '')) for model in models]:
            print('WARN | Ollama model | {} is missing | Fix: In PowerShell run: ollama pull {}'.format(OLLAMA_MODEL, OLLAMA_MODEL), flush=True)
    except (OSError, urllib.error.URLError, ValueError, UnicodeError) as exc:
        print('WARN | Ollama model | Cannot check installed models: {}: {} | Fix: Check Ollama and refresh System health.'.format(type(exc).__name__, exc), flush=True)


def find_ollama():
    """Check PATH and Windows install folders, preferring the tray application."""
    cli = shutil.which('ollama')
    app = shutil.which('ollama app.exe') if IS_WINDOWS else None
    directories = []
    if cli:
        directories.append(Path(cli).parent)
    if IS_WINDOWS:
        for env_name, suffix in (('LOCALAPPDATA', ('Programs', 'Ollama')), ('LOCALAPPDATA', ('Ollama',)),
                                 ('ProgramFiles', ('Ollama',)), ('ProgramFiles(x86)', ('Ollama',))):
            root = os.environ.get(env_name)
            if root:
                directories.append(Path(root).joinpath(*suffix))
    app_candidates = ([Path(app)] if app else []) + [directory / 'ollama app.exe' for directory in directories]
    if IS_WINDOWS:
        for candidate in app_candidates:
            if candidate.is_file():
                return str(candidate), True
    if cli:
        return cli, False
    if IS_WINDOWS:
        for directory in directories:
            candidate = directory / 'ollama.exe'
            if candidate.is_file():
                return str(candidate), False
    return None, False


def ollama_log_file():
    """Keep detached serve diagnostics in the local workspace, never in Drive."""
    if not IS_WINDOWS:
        return None, None
    root = Path(r'C:\AuraCut_Workspace')
    directory = root / 'logs'
    for folder in (root, directory):
        if folder.is_symlink() or (hasattr(folder, 'is_junction') and folder.is_junction()):
            raise OSError('{} is a link or junction'.format(folder))
        folder.mkdir(parents=True, exist_ok=True)
    path = directory / 'ollama-start.log'
    if path.is_symlink():
        raise OSError('{} is a link'.format(path))
    return path.open('a', encoding='utf-8'), path


def ollama_log_tail(path, offset=0):
    if path is None:
        return 'No launcher log is available.'
    try:
        with path.open('rb') as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(offset, log.tell() - 4096))
            lines = log.read().decode('utf-8', errors='replace').splitlines()
        # Never print lines that might contain authentication values.
        lines = [line for line in lines if line.strip() and not re.search(r'(?i)token|password|api[_-]?key|authorization|secret', line)]
        return ' | '.join(lines[-3:])[-900:] or 'No diagnostic output was recorded.'
    except OSError as exc:
        return 'Could not read {}: {}'.format(path, exc)


def prepare_ollama():
    enabled = ollama_autostart_enabled()
    ready, initial_cause = ollama_probe()
    if ready:
        print('Ollama is already running', flush=True)
        check_ollama_model()
        return
    if not enabled:
        print('Ollama auto-start is disabled; Ollama is not reachable. Start it manually if needed.', flush=True)
        return
    executable, is_app = find_ollama()
    if not executable:
        print('WARN | Ollama | Ollama was not found on PATH or in the usual Windows folders | Fix: Download and install Ollama from https://ollama.com.', flush=True)
        return
    print('Starting Ollama...', flush=True)
    log_handle, log_path, log_offset = None, None, 0
    try:
        try:
            log_handle, log_path = ollama_log_file()
            if log_handle is not None:
                log_offset = os.fstat(log_handle.fileno()).st_size
        except OSError as exc:
            print(r'WARN | Ollama log | Could not open the local startup log: {} | Fix: Check C:\AuraCut_Workspace\logs permissions.'.format(exc), flush=True)
        flags = (getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000) |
                 getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)) if IS_WINDOWS else 0
        command = [executable] if is_app else [executable, 'serve']
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=log_handle if log_handle is not None else subprocess.DEVNULL,
                                   creationflags=flags, close_fds=True)
    except OSError as exc:
        print('WARN | Ollama start | Could not start {}: {}: {} | Fix: Check the Ollama installation and permissions, then start it manually.'.format(executable, type(exc).__name__, exc), flush=True)
        return
    finally:
        if log_handle is not None:
            log_handle.close()
    deadline = time.monotonic() + 20
    next_check = time.monotonic() + 1
    cause = initial_cause
    while time.monotonic() < deadline:
        time.sleep(max(0, min(next_check, deadline) - time.monotonic()))
        remaining = deadline - time.monotonic()
        ready, cause = ollama_probe(timeout=max(0.01, min(0.8, remaining)))
        if ready:
            print('Ollama is ready', flush=True)
            check_ollama_model()
            return
        if not is_app and process.poll() is not None:
            print(r'WARN | Ollama start | Ollama serve exited with code {}. {} | Fix: Check C:\AuraCut_Workspace\logs\ollama-start.log and start Ollama manually.'.format(process.returncode, ollama_log_tail(log_path, log_offset)), flush=True)
            return
        next_check += 1
    app_exit = process.poll() if is_app else None
    exit_detail = ' App process exited with code {}.'.format(app_exit) if app_exit is not None else ''
    print('WARN | Ollama start | Not reachable after 20 seconds: {}.{} {} | Fix: Check Ollama and start it manually; AuraCut can still use Gemini.'.format(cause, exit_detail, ollama_log_tail(log_path, log_offset)), flush=True)


def browser():
    try:
        if not webbrowser.open(URL):
            raise RuntimeError('Windows did not open a browser')
    except Exception as exc:
        error('Open browser', exc, 'Open {} manually in your browser.'.format(URL))


def port_busy():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(('127.0.0.1', 8000))
            return False
        except OSError as exc:
            return exc


def is_auracut():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(URL + '/api/health', timeout=12) as response:
            result = json.load(response)
        return isinstance(result, dict) and result.get('app') == 'AuraCut Studio'
    except (OSError, ValueError, urllib.error.URLError):
        return False


def owner():
    if os.name != 'nt':
        return 'Program identification is available on Windows; check port 8000 with your system tools.'
    try:
        net = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True, text=True, errors='replace', timeout=10, check=True)
        pids = set()
        for line in net.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 5 and fields[1].rsplit(':', 1)[-1] == '8000' and fields[3].upper() == 'LISTENING':
                pids.add(fields[4])
        if not pids:
            return 'No listening process was found by netstat.'
        result = []
        for pid in sorted(pids):
            task = subprocess.run(['tasklist', '/FI', 'PID eq {}'.format(pid), '/FO', 'CSV', '/NH'], capture_output=True, text=True, errors='replace', timeout=10, check=True)
            result.append('PID {}: {}'.format(pid, task.stdout.strip()))
        return '; '.join(result)
    except (OSError, subprocess.SubprocessError) as exc:
        return 'Could not identify the program: {}'.format(exc)


def setup():
    if sys.version_info < (3, 11):
        raise RuntimeError('Python {} is too old; Python 3.11 or newer is required.'.format(sys.version.split()[0]))
    if not PYTHON.exists():
        print('Creating the local Python environment...', flush=True)
        venv.create(str(VENV), with_pip=True)
    digest = hashlib.sha256((ROOT / 'requirements.txt').read_bytes()).hexdigest()
    if not MARKER.exists() or MARKER.read_text(encoding='utf-8').strip() != digest:
        print('Installing required Python packages. Internet is needed for this step...', flush=True)
        subprocess.run([str(PYTHON), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], cwd=str(ROOT), check=True)
        MARKER.write_text(digest, encoding='utf-8')


def main():
    os.chdir(ROOT)
    try:
        prepare_ollama()
    except Exception as exc:
        print('WARN | Ollama startup | {}: {} | Fix: Check Ollama and start it manually; AuraCut will continue.'.format(type(exc).__name__, exc), flush=True)
    busy = port_busy()
    if busy:
        if is_auracut():
            print('AuraCut is already running.', flush=True)
            browser()
            return 0
        error('Port 8000', '{}. {}'.format(busy, owner()), 'Close the listed program or free port 8000; then start AuraCut again.')
        return 1
    try:
        setup()
    except Exception as exc:
        error('Setup', '{}: {}'.format(type(exc).__name__, exc), 'Install Python 3.11+ and check your internet and folder permissions; then retry.')
        return 1
    try:
        print('Starting AuraCut on {}...'.format(URL), flush=True)
        server = subprocess.Popen([str(PYTHON), '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000'], cwd=str(ROOT))
    except OSError as exc:
        error('Server start', exc, 'Check the virtual environment and folder permissions, then retry.')
        return 1
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if server.poll() is not None:
                error('Server start', 'Server exited with code {} (see the messages above).'.format(server.returncode), 'Fix the reported server error, then start AuraCut again.')
                return 1
            if is_auracut():
                print('AuraCut is ready. Keep this window open.', flush=True)
                browser()
                return server.wait()
            time.sleep(0.5)
        error('Server start', 'The health endpoint did not become ready within 40 seconds.', 'Check the server messages above and .env settings, then retry.')
        return 1
    except KeyboardInterrupt:
        print('\nStopping AuraCut...', flush=True)
        return 0
    finally:
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=10)


if __name__ == '__main__':
    sys.exit(main())
