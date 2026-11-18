"""Small in-memory background job queue for Phase 2."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.common import AppError, error_data, logger

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='peakcut')
_lock = threading.Lock()
_jobs = {}
MAX_ACTIVE_JOBS = 8
MAX_FINISHED_JOBS = 100


def submit(label, func, *args):
    with _lock:
        active = sum(1 for job in _jobs.values() if job['status'] in ('queued', 'running'))
        if active >= MAX_ACTIVE_JOBS:
            raise AppError(label, 'The PeakCut job queue is full.', 'Wait for an existing job to finish, then try again.', 429)
        finished = [key for key, job in _jobs.items() if job['status'] in ('done', 'failed')]
        for key in finished[:-MAX_FINISHED_JOBS + 1]:
            del _jobs[key]
        job_id = uuid.uuid4().hex
        _jobs[job_id] = {'id': job_id, 'status': 'queued', 'percent': None, 'stage': 'Waiting for a worker'}

    def progress(percent, stage):
        with _lock:
            _jobs[job_id]['percent'] = max(0, min(99, int(percent))) if percent is not None else None
            _jobs[job_id]['stage'] = stage

    def worker():
        with _lock:
            _jobs[job_id].update(status='running', stage='Starting')
        try:
            result = func(*args, progress=progress)
            with _lock:
                _jobs[job_id].update(status='done', percent=100, stage='Finished', result=result)
            logger.info('%s finished (job %s).', label, job_id)
        except Exception as exc:
            if isinstance(exc, AppError):
                info = error_data(exc.what_failed, exc.real_cause, exc.fix)
                if getattr(exc, 'technical_details', None):
                    info['technical_details'] = exc.technical_details
            else:
                info = error_data(label, '{}: {}'.format(type(exc).__name__, exc),
                                  'Check the reported cause, FFmpeg, local workspace access and free space, then retry.')
            with _lock:
                _jobs[job_id].update(status='failed', stage='Failed', error=info)

    try:
        _executor.submit(worker)
    except Exception:
        with _lock:
            del _jobs[job_id]
        raise
    return job_id


def get(job_id):
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise AppError('PeakCut job', 'Job {} was not found.'.format(job_id),
                           'Start the analysis or render again; jobs are cleared when the app restarts.', 404)
        return dict(job)
