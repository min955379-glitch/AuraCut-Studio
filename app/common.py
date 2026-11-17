import logging
import os
import re
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = 'qwen2.5:7b-instruct-q4_K_M'
LOG_LINES = deque(maxlen=300)
logger = logging.getLogger('auracut')
logger.setLevel(logging.INFO)

class MemoryHandler(logging.Handler):
    def emit(self, record):
        LOG_LINES.append(self.format(record))

if not logger.handlers:
    fmt = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
    for handler in (logging.StreamHandler(), MemoryHandler()):
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    logger.propagate = False


def settings():
    values = {}
    path = ROOT / '.env'
    if path.exists():
        with path.open(encoding='utf-8-sig') as f:
            for number, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' not in line:
                    raise ValueError('.env line {} must have NAME=value'.format(number))
                key, value = line.split('=', 1)
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key.strip()] = value
    return values


def clean(text, config=None):
    text = str(text)
    key = (config or {}).get('GEMINI_API_KEY', '')
    if key:
        text = text.replace(key, '[redacted]')
    text = re.sub(r'(key=)[^&\s]+', r'\1[redacted]', text, flags=re.I)
    return text


class AppError(Exception):
    def __init__(self, what_failed, real_cause, fix, status=400):
        super().__init__(real_cause)
        self.what_failed, self.real_cause, self.fix, self.status = what_failed, real_cause, fix, status


def error_data(what, cause, fix, config=None):
    result = {'ok': False, 'what_failed': str(what), 'real_cause': clean(cause, config), 'fix': str(fix)}
    logger.error('%s | Cause: %s | Fix: %s', result['what_failed'], result['real_cause'], result['fix'])
    return result


def workspace(config):
    raw = config.get('AURACUT_WORKSPACE', '').strip() or r'C:\AuraCut_Workspace'
    path = Path(raw)
    if not path.is_absolute():
        raise AppError('Workspace setup', 'AURACUT_WORKSPACE must be an absolute local path: {}'.format(raw),
                       'Set AURACUT_WORKSPACE=C:\\AuraCut_Workspace in .env.')
    if os.name == 'nt' and os.path.normcase(os.path.normpath(raw)) != os.path.normcase(r'C:\AuraCut_Workspace'):
        raise AppError('Workspace setup', 'Processing is restricted to C:\\AuraCut_Workspace, not {}.'.format(raw),
                       'Set AURACUT_WORKSPACE=C:\\AuraCut_Workspace in .env.')
    if os.name != 'nt' and raw == r'C:\AuraCut_Workspace':
        raise AppError('Workspace setup', 'The Windows workspace is unavailable on this operating system.',
                       'Run AuraCut on Windows, or configure a local absolute workspace for development.')
    for candidate in (path, path / 'Input_Media', path / 'Output_Renders'):
        if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
            raise AppError('Workspace setup', '{} is a link or junction.'.format(candidate),
                           'Use real local folders, not Google Drive links or junctions.')
        candidate.mkdir(parents=True, exist_ok=True)
    return path
