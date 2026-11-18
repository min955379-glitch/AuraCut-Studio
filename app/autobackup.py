"""Explicitly queued background Drive backups, persisted locally for later retry."""
import json
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app import storage
from app.common import AppError, clean, error_data, logger, workspace

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='drive-backup')
_lock = threading.RLock()
_states = {}


def flag(config, name, default=False):
    raw = config.get(name, '').strip().lower()
    if not raw:
        return default
    if raw in ('true', '1', 'yes', 'on'):
        return True
    if raw in ('false', '0', 'no', 'off'):
        return False
    raise AppError('Auto backup configuration', '{} is not true or false: {}'.format(name, raw),
                   'Set {} to true or false in .env.'.format(name))


def enabled(config):
    return flag(config, 'AURACUT_AUTO_BACKUP', bool(config.get('AURACUT_DRIVE_BACKUP', '').strip()))


def inputs_enabled(config):
    return flag(config, 'AURACUT_AUTO_BACKUP_INPUTS', False)


def _state(config):
    root = workspace(config)
    path = root / 'auto_backup_state.json'
    if path.is_symlink():
        raise AppError('Auto backup state', 'State file is a link: {}'.format(path), 'Use a regular local state file.')
    key = str(path)
    if key not in _states:
        try:
            data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'records': {}, 'last': None}
            if not isinstance(data.get('records'), dict):
                raise ValueError('records must be an object')
            # Jobs cannot survive an app restart; require a deliberate retry.
            for record in data['records'].values():
                if record.get('status') in ('queued', 'backing_up'):
                    record.update(status='pending', message='Backup interrupted; click Back up all pending files.')
            _states[key] = data
        except (OSError, ValueError, TypeError) as exc:
            raise AppError('Auto backup state', 'Could not read local backup state: {}'.format(exc),
                           'Check local workspace permissions and state file, then retry.') from exc
    return path, _states[key]


def _save(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def status(config):
    with _lock:
        _, data = _state(config)
        return json.loads(json.dumps(data))


def _key(folder, name):
    return folder + '/' + name


def enqueue(config, folder, names):
    # Persist pending work before queueing it. This never touches Drive or waits for the copy.
    with _lock:
        path, data = _state(config)
        queued = []
        for name in names:
            if not name:
                continue
            file = storage.safe_file(config, folder, name)
            if file.suffix.lower() == '.srt' and file.stat().st_size == 0:
                continue
            key = _key(folder, name)
            record = data['records'].get(key, {})
            if record.get('status') in ('queued', 'backing_up'):
                continue
            data['records'][key] = {'folder': folder, 'name': name, 'status': 'queued', 'message': 'Waiting to back up'}
            queued.append(key)
        try:
            _save(path, data)
        except OSError as exc:
            for key in queued:
                data['records'][key].update(status='pending', message='Could not save backup state: {}. Fix: Check local workspace permissions.'.format(exc))
            raise AppError('Auto backup state', 'Could not save backup state: {}'.format(exc),
                           'Check local workspace permissions, then click Back up all pending files.') from exc
        if queued:
            try:
                _executor.submit(_run, dict(config), queued)
            except Exception as exc:
                for key in queued:
                    data['records'][key].update(status='pending', message='Could not start backup: {}'.format(exc))
                _save(path, data)
                raise AppError('Auto backup', 'Could not start backup: {}'.format(exc), 'Click Back up all pending files to retry.') from exc
        return len(queued)


def _run(config, keys):
    try:
        _run_inner(config, keys)
    except Exception as exc:
        error = error_data('Auto backup', '{}: {}'.format(type(exc).__name__, exc),
                           'Check local workspace permissions and click Back up all pending files.', config)
        logger.exception('Auto backup worker failed')
        try:
            with _lock:
                path, data = _state(config)
                for key in keys:
                    record = data['records'].get(key)
                    if record and record.get('status') in ('queued', 'backing_up'):
                        record.update(status='pending', message=error['real_cause'] + '. Fix: ' + error['fix'], error=error)
                _save(path, data)
        except Exception as state_exc:
            logger.error('Auto backup state failed: %s | Fix: Check local workspace permissions.', state_exc)


def _run_inner(config, keys):
    for key in keys:
        with _lock:
            path, data = _state(config)
            record = data['records'][key]
            record.update(status='backing_up', message='Backing up to Drive')
            _save(path, data)
            folder, name = record['folder'], record['name']
        def progress(percent, stage):
            with _lock:
                record['message'] = ('Backing up to Drive ({} percent)'.format(percent) if percent is not None and stage.startswith('Copying') else stage)
        try:
            result = storage.backup_file(config, folder, name, progress)
            message = 'Backed up to Drive: {}'.format(result['message'])
            error = None
            state = 'backed_up'
            logger.info('%s', message)
        except Exception as exc:
            if isinstance(exc, AppError):
                error = error_data(exc.what_failed, exc.real_cause, exc.fix, config)
            else:
                error = error_data('Auto backup', '{}: {}'.format(type(exc).__name__, exc),
                                   'Check Drive and click Back up all pending files.', config)
            error['technical_details'] = clean(traceback.format_exc(), config)
            logger.error('Auto backup technical details:\n%s', error['technical_details'])
            message = 'Backup pending: {}. Fix: {}'.format(error['real_cause'], error['fix'])
            state = 'pending'
        with _lock:
            record.update(status=state, message=message, error=error)
            data['last'] = {'time': datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds'), 'message': message}
            try:
                _save(path, data)
            except OSError as exc:
                error = error_data('Auto backup state', 'Could not save backup status: {}'.format(exc),
                                   'Check local workspace permissions, then retry pending backups.', config)
                error['technical_details'] = clean(traceback.format_exc(), config)
                record.update(status='pending', message=error['real_cause'] + '. Fix: ' + error['fix'], error=error)
                data['last'] = {'time': datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds'), 'message': record['message']}
                logger.error('Auto backup state details:\n%s', error['technical_details'])


def retry_pending(config):
    with _lock:
        _, data = _state(config)
        records = [dict(r) for r in data['records'].values() if r.get('status') == 'pending']
    count = 0
    failures = []
    for record in records:
        try:
            count += enqueue(config, record['folder'], [record['name']])
        except Exception as exc:
            detail = clean('{}: {}'.format(type(exc).__name__, exc), config)
            failures.append('{}: {}'.format(record['name'], detail))
            logger.error('Pending backup %s | Cause: %s | Fix: Check the local file and retry.', record['name'], detail)
    if failures:
        raise AppError('Back up pending files', 'Queued {} file(s); could not queue: {}'.format(count, '; '.join(failures)),
                       'Check the listed local files and workspace permissions, then retry.')
    return count


def manual_backup(config, folder, name, progress=lambda *_: None):
    result = storage.backup_file(config, folder, name, progress)
    with _lock:
        path, data = _state(config)
        record = data['records'].get(_key(folder, name))
        if record and record.get('status') not in ('queued', 'backing_up'):
            message = 'Backed up to Drive: {}'.format(result['message'])
            record.update(status='backed_up', message=message, error=None)
            data['last'] = {'time': datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds'), 'message': message}
            _save(path, data)
    return result
