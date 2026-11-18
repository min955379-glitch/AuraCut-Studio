import os
import re
import hashlib
import json
import threading
import time
import uuid
import tempfile
from pathlib import Path

from app.common import AppError, workspace


def valid_name(name):
    if not isinstance(name, str) or not name or name in ('.', '..') or len(name) > 200:
        return False
    if any(c in name for c in '/\\:<>"|?*') or any(ord(c) < 32 for c in name):
        return False
    if name.endswith(('.', ' ')) or name.strip() != name:
        return False
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I):
        return False
    return True


def safe_file(config, folder, name):
    if folder not in ('Input_Media', 'Output_Renders') or not valid_name(name):
        raise AppError('File selection', 'Invalid folder or unsafe filename: {} / {}'.format(folder, name),
                       'Choose a file from the displayed list, without folder paths in its name.')
    if folder == 'Output_Renders' and name.startswith('._peakcut_'):
        raise AppError('File selection', 'This PeakCut render is still a temporary file.',
                       'Wait for the render to finish before choosing or backing it up.')
    base = workspace(config) / folder
    path = base / name
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise AppError('File selection', 'The selected file is a link: {}'.format(path),
                       'Use a regular file stored in the local workspace.')
    if not path.is_file():
        raise AppError('File selection', 'The file does not exist: {}'.format(path),
                       'Refresh the file list and choose an existing workspace file.', 404)
    return path


def drive_folder(config):
    raw = config.get('AURACUT_DRIVE_BACKUP', '').strip()
    if not raw:
        raise AppError('Drive backup', 'AURACUT_DRIVE_BACKUP is not set in .env.',
                       'Set it to the full path of an existing Google Drive folder in .env.')
    path = Path(raw)
    if not path.is_absolute() or not path.is_dir():
        raise AppError('Drive backup', 'Drive folder is missing or not an absolute folder: {}'.format(raw),
                       'Start Google Drive and set AURACUT_DRIVE_BACKUP to an existing full folder path.')
    raw_workspace = config.get('AURACUT_WORKSPACE', '').strip() or r'C:\AuraCut_Workspace'
    work = Path(raw_workspace)
    if work.is_absolute():
        resolved_work, resolved_drive = work.resolve(), path.resolve()
        if resolved_work == resolved_drive or resolved_work in resolved_drive.parents or resolved_drive in resolved_work.parents:
            raise AppError('Drive backup', 'The Drive backup folder overlaps the local workspace: {}'.format(path),
                           'Set AURACUT_DRIVE_BACKUP to a separate Google Drive folder.')
    return path


_BACKUP_LOCK = threading.Lock()
POLL_SECONDS = 2
VERIFY_TIMEOUT = 90


def backup_file(config, folder, name, progress=lambda *_: None):
    # Serializing backup attempts keeps an app-owned partial from being overwritten
    # by concurrent requests. Files not created by this app are never overwritten.
    with _BACKUP_LOCK:
        return _backup_file(config, folder, name, progress)


def _backup_file(config, folder, name, progress):
    source = safe_file(config, folder, name)
    dest_dir = drive_folder(config)
    target = dest_dir / name
    if dest_dir.resolve() == source.parent.resolve():
        raise AppError('Drive backup', 'The backup folder is the source workspace folder.',
                       'Choose a separate Google Drive backup folder in .env.')
    try:
        source_stat = source.stat()
        source_size = source_stat.st_size
        key = hashlib.sha256(str(target.resolve()).encode('utf-8')).hexdigest()
        state_dir = workspace(config) / 'backup_state'
        if state_dir.is_symlink() or (hasattr(state_dir, 'is_junction') and state_dir.is_junction()):
            raise AppError('Drive backup', 'The local backup state folder is a link.', 'Use a real local backup_state folder.')
        state_dir.mkdir(parents=True, exist_ok=True)
        marker = state_dir / (key + '.json')
        identity = {'source': str(source.resolve()), 'target': str(target.resolve()),
                    'size': source_size, 'mtime_ns': source_stat.st_mtime_ns}
        owned = False
        if marker.is_file() and not marker.is_symlink():
            try:
                owned = json.loads(marker.read_text(encoding='utf-8')) == identity
            except (ValueError, UnicodeError) as exc:
                raise AppError('Drive backup', 'Cannot read backup state: {}'.format(exc),
                               'Ask for help before changing existing Drive files.') from exc
        exists = target.exists() or target.is_symlink()
        if exists:
            if target.is_symlink() or not target.is_file():
                raise AppError('Drive backup', 'The Drive destination is not a regular file: {}'.format(target),
                               'Choose a different name or remove the link yourself.')
            if target.stat().st_size == source_size:
                if owned:
                    marker.unlink(missing_ok=True)
                return {'ok': True, 'message': 'Already backed up: {}'.format(target)}
            if not owned:
                raise AppError('Drive backup', 'A different-sized file named {} already exists; it was not overwritten.'.format(name),
                               'Rename or move the existing Drive file, then try again.')
        else:
            marker.write_text(json.dumps(identity), encoding='utf-8')
        # A retry writes a separate copy first: an existing incomplete file stays
        # untouched until its replacement has been fully copied and verified.
        copy_target = (dest_dir / ('.auracut_{}_{}.partial'.format(uuid.uuid4().hex, name))) if exists else target
        progress(0, 'Copying, 0 percent')
        with source.open('rb') as src, copy_target.open('xb') as dest:
            copied = 0
            while True:
                data = src.read(1024 * 1024)
                if not data:
                    break
                dest.write(data)
                copied += len(data)
                percent = min(99, int(copied * 100 / max(1, source_size)))
                progress(percent, 'Copying, {} percent'.format(percent))
            dest.flush()
            os.fsync(dest.fileno())
        current = source.stat()
        if (current.st_size, current.st_mtime_ns) != (source_size, source_stat.st_mtime_ns):
            raise AppError('Drive backup', 'Source changed while copying; the copy may be incomplete.',
                           'Wait for the source file to stop changing, then back it up again.')
        deadline = time.monotonic() + VERIFY_TIMEOUT
        def verify(path):
            while True:
                size = path.stat().st_size if path.exists() else 0
                if size == source_size:
                    return True, size
                left = deadline - time.monotonic()
                if left <= 0:
                    return False, size
                progress(None, 'Waiting for Drive to finish writing ({} / {} bytes)'.format(size, source_size))
                time.sleep(min(POLL_SECONDS, left))
        ok, size = verify(copy_target)
        if not ok:
            raise AppError('Drive backup incomplete', 'Size mismatch: source {} bytes; copy {} bytes. Partial copy kept: {}'.format(source_size, size, copy_target),
                           'Drive is still writing or sync is slow. Wait and click Back up again.')
        if exists:
            os.replace(copy_target, target)
            ok, size = verify(target)
            if not ok:
                raise AppError('Drive backup incomplete', 'Size mismatch: source {} bytes; copy {} bytes. Partial copy kept: {}'.format(source_size, size, target),
                               'Drive is still writing or sync is slow. Wait and click Back up again.')
        marker.unlink(missing_ok=True)
        return {'ok': True, 'message': 'Copied and verified size: {}'.format(target)}
    except AppError:
        raise
    except OSError as exc:
        raise AppError('Drive backup', 'Could not copy or verify {}: {}. Any partial copy was kept.'.format(source, exc),
                       'Check Drive is running, writable, and has enough free space, then retry.') from exc


def list_files(config):
    root = workspace(config)
    try:
        drive = drive_folder(config)
    except AppError:
        drive = None
    files = []
    for folder in ('Input_Media', 'Output_Renders'):
        for path in sorted((root / folder).iterdir(), key=lambda p: p.name.lower()):
            if folder == 'Output_Renders' and path.name.startswith('._peakcut_'):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            size = path.stat().st_size
            backed_up = False
            backup_incomplete = False
            if drive:
                candidate = drive / path.name
                try:
                    if not candidate.is_symlink() and candidate.is_file():
                        backed_up = candidate.stat().st_size == size
                        backup_incomplete = not backed_up
                except OSError as exc:
                    raise AppError('File list', 'Cannot check backup {}: {}'.format(candidate, exc),
                                   'Check Google Drive folder access, then refresh the list.') from exc
            files.append({'name': path.name, 'size': size, 'folder': folder, 'backed_up': backed_up, 'backup_incomplete': backup_incomplete})
    return files


def folder_test(path):
    with tempfile.NamedTemporaryFile(prefix='auracut_check_', dir=str(path)) as f:
        f.write(b'write test')
        f.flush()
