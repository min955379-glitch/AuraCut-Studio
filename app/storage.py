import os
import re
import shutil
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


def backup_file(config, folder, name):
    source = safe_file(config, folder, name)
    dest_dir = drive_folder(config)
    if dest_dir.resolve() == source.parent.resolve():
        raise AppError('Drive backup', 'The backup folder is the source workspace folder.',
                       'Choose a separate Google Drive backup folder in .env.')
    target = dest_dir / name
    try:
        # Exclusive creation prevents overwriting a different file and avoids symlink targets.
        with source.open('rb') as src, target.open('xb') as dest:
            try:
                shutil.copyfileobj(src, dest, length=1024 * 1024)
                dest.flush()
            except Exception:
                dest.close()
                target.unlink(missing_ok=True)
                raise
        source_size = source.stat().st_size
        dest_size = target.stat().st_size
        if source_size != dest_size:
            target.unlink(missing_ok=True)
            raise AppError('Drive backup', 'Size mismatch: source {} bytes; copy {} bytes.'.format(source_size, dest_size),
                           'Check free space and Drive access, then try the backup again.')
        return {'ok': True, 'message': 'Copied and verified size: {}'.format(target)}
    except FileExistsError as exc:
        raise AppError('Drive backup', 'A file named {} already exists in the backup folder; it was not overwritten.'.format(name),
                       'Rename or move the existing Drive file, then try again.') from exc
    except OSError as exc:
        raise AppError('Drive backup', 'Could not copy {}: {}'.format(source, exc),
                       'Check Drive is running, writable, and has enough free space.') from exc


def list_files(config):
    root = workspace(config)
    try:
        drive = drive_folder(config)
    except AppError:
        drive = None
    files = []
    for folder in ('Input_Media', 'Output_Renders'):
        for path in sorted((root / folder).iterdir(), key=lambda p: p.name.lower()):
            if path.is_symlink() or not path.is_file():
                continue
            size = path.stat().st_size
            backed_up = False
            if drive:
                candidate = drive / path.name
                try:
                    backed_up = not candidate.is_symlink() and candidate.is_file() and candidate.stat().st_size == size
                except OSError as exc:
                    raise AppError('File list', 'Cannot check backup {}: {}'.format(candidate, exc),
                                   'Check Google Drive folder access, then refresh the list.') from exc
            files.append({'name': path.name, 'size': size, 'folder': folder, 'backed_up': backed_up})
    return files


def folder_test(path):
    with tempfile.NamedTemporaryFile(prefix='auracut_check_', dir=str(path)) as f:
        f.write(b'write test')
        f.flush()
