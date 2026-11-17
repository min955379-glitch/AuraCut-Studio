"""First-run setup and local-only launcher; uses only Python's standard library."""
import hashlib
import json
import os
from pathlib import Path
import socket
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


def error(what, cause, fix):
    print('FAIL | {} | {}'.format(what, cause), flush=True)
    print('       Fix: {}'.format(fix), flush=True)


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
