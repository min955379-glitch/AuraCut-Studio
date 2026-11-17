import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
from app.brain_ai import gemini_generate, gemini_timeout, ollama_url, reason
from app.common import DEFAULT_MODEL, AppError, logger, workspace
from app.storage import drive_folder, folder_test


def entry(label, status, detail, fix=''):
    if status != 'PASS':
        logger.warning('%s | Cause: %s | Fix: %s', label, detail, fix)
    else:
        fix = ''
    return {'name': label, 'status': status, 'detail': detail, 'fix': fix}


def timeout_check(config):
    _, warning = gemini_timeout(config)
    if warning:
        return entry('Gemini timeout', 'WARN', warning,
                     'Set GEMINI_TIMEOUT_SECONDS to a number of at least 10 in .env.')
    return None


def gemini_test_context(config):
    # A result is valid only for the exact configuration it tested. Do not expose the key.
    values = [config.get(name, '') for name in ('GEMINI_API_KEY', 'GEMINI_MODEL', 'GEMINI_TIMEOUT_SECONDS')]
    return hashlib.sha256(json.dumps(values, ensure_ascii=True).encode('utf-8')).hexdigest()


async def test_gemini(config):
    if not config.get('GEMINI_API_KEY', '').strip() or not config.get('GEMINI_MODEL', '').strip():
        return entry('Gemini', 'FAIL', 'GEMINI_API_KEY and GEMINI_MODEL are both required for the test.',
                     'Set the Gemini key and model in .env, then test again.')
    try:
        reply = await gemini_generate('Reply with the word OK.', config)
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError('Gemini returned an empty response')
        logger.info('Gemini test passed for model %s.', config['GEMINI_MODEL'])
        return entry('Gemini', 'PASS', 'Real Gemini generation test passed for model {}.'.format(config['GEMINI_MODEL']))
    except Exception as exc:
        return entry('Gemini', 'FAIL', 'Real Gemini generation test failed: {}'.format(reason(exc, config)),
                     'Check the Gemini key, model, internet connection and quota, then test again.')


def local_checks(config):
    results = [entry('Python', 'PASS' if sys.version_info >= (3, 11) else 'FAIL',
                     'Python {}.{}.{}'.format(*sys.version_info[:3]), 'Install Python 3.11 or newer.')]
    for program in ('ffmpeg', 'ffprobe'):
        path = shutil.which(program)
        if not path:
            results.append(entry(program, 'FAIL', '{} was not found on PATH.'.format(program), 'Install FFmpeg and add its bin folder to PATH; restart AuraCut.'))
            continue
        try:
            result = subprocess.run([path, '-version'], capture_output=True, text=True, errors='replace', timeout=8)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout).strip() or 'exit code {}'.format(result.returncode))
            results.append(entry(program, 'PASS', 'Available: {}'.format(path)))
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            results.append(entry(program, 'FAIL', str(exc), 'Repair FFmpeg and reopen AuraCut.'))
    try:
        root = workspace(config)
        for name in ('Input_Media', 'Output_Renders'):
            try:
                folder_test(root / name)
                results.append(entry(name, 'PASS', 'Writable: {}'.format(root / name)))
            except OSError as exc:
                results.append(entry(name, 'FAIL', str(exc), 'Check folder permissions and free space on C:.'))
    except Exception as exc:
        fix = exc.fix if isinstance(exc, AppError) else 'Check the local workspace path and folder permissions.'
        results.append(entry('Workspace', 'FAIL', str(exc), fix))
    try:
        folder = drive_folder(config)
        folder_test(folder)
        results.append(entry('Drive', 'PASS', 'Folder writable: {}. Cloud sync is not tested.'.format(folder)))
    except (AppError, OSError) as exc:
        results.append(entry('Drive', 'WARN', str(exc), exc.fix if isinstance(exc, AppError) else 'Start Google Drive and check folder permissions and free space.'))
    return results


async def ollama_health(config):
    try:
        async with httpx.AsyncClient(timeout=4, trust_env=False) as client:
            response = await client.get(ollama_url(config) + '/api/tags')
            response.raise_for_status()
            data = response.json()
        models = data.get('models') if isinstance(data, dict) else None
        if not isinstance(models, list) or any(not isinstance(m, dict) for m in models):
            raise ValueError('Invalid Ollama model list')
        model = config.get('OLLAMA_MODEL', '').strip() or DEFAULT_MODEL
        names = [m.get('name', m.get('model', '')) for m in models]
        if model not in names:
            return entry('Ollama', 'FAIL', 'Ollama is reachable but model {} is missing.'.format(model), 'In PowerShell, run: ollama pull {}'.format(model))
        return entry('Ollama', 'PASS', 'Reachable; model {} is installed.'.format(model))
    except Exception as exc:
        return entry('Ollama', 'FAIL', reason(exc, config), 'Start Ollama and check OLLAMA_URL in .env.')


async def gemini_health(config):
    key, model = config.get('GEMINI_API_KEY', '').strip(), config.get('GEMINI_MODEL', '').strip()
    if not key:
        return entry('Gemini', 'WARN', 'GEMINI_API_KEY is not set (optional).', 'Optionally add a Gemini key and model to .env.')
    try:
        models = []
        token = None
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            for _ in range(20):
                params = {'pageSize': 100}
                if token:
                    params['pageToken'] = token
                response = await client.get('https://generativelanguage.googleapis.com/v1beta/models', params=params, headers={'x-goog-api-key': key})
                response.raise_for_status()
                data = response.json()
                models.extend(m.get('name', '').removeprefix('models/') for m in data.get('models', []) if 'generateContent' in m.get('supportedGenerationMethods', []))
                token = data.get('nextPageToken')
                if not token:
                    break
            if token:
                raise ValueError('Gemini model list exceeded 20 pages')
        if not model or model.removeprefix('models/') not in models:
            available = ', '.join(models) if models else '(no generateContent models returned)'
            return entry('Gemini', 'WARN', 'GEMINI_MODEL is empty or unavailable. Available models: {}'.format(available),
                         'Set GEMINI_MODEL in .env to one of the listed names, then refresh health.')
        return entry('Gemini', 'WARN', 'Key and model look valid (not tested). Model: {}.'.format(model),
                     'Click Test Gemini now to check a real generation request.')
    except Exception as exc:
        return entry('Gemini', 'WARN', 'Could not validate the key/model or list available models: {}'.format(reason(exc, config)),
                     'Check the key, internet connection, and Gemini API access; retry health.')


def gpu_test(config):
    root = workspace(config)
    folder = root / 'Output_Renders'
    target = folder / '_amf_test.mp4'
    if target.is_symlink():
        raise AppError('GPU encode test', 'The test output is a link: {}'.format(target), 'Remove the link and retry the test.')
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise AppError('GPU encode test', 'ffmpeg or ffprobe is missing from PATH.', 'Install FFmpeg and reopen AuraCut.')
    import tempfile
    import os
    fd, tmpname = tempfile.mkstemp(prefix='_amf_test_', suffix='.mp4', dir=str(folder))
    os.close(fd)
    tmp = Path(tmpname)
    try:
        run = subprocess.run([ffmpeg, '-hide_banner', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30', '-t', '2', '-pix_fmt', 'nv12', '-c:v', 'h264_amf', str(tmp)], capture_output=True, text=True, errors='replace', timeout=90)
        if run.returncode:
            raise AppError('GPU encode test', 'FFmpeg exited with code {}: {}'.format(run.returncode, (run.stderr or run.stdout).strip()), 'Check AMD drivers and install an FFmpeg build with working h264_amf support.')
        probe = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(tmp)], capture_output=True, text=True, errors='replace', timeout=15)
        if probe.returncode:
            raise AppError('GPU encode test', 'ffprobe exited with code {}: {}'.format(probe.returncode, (probe.stderr or probe.stdout).strip()), 'Repair FFmpeg and retry the GPU test.')
        try:
            duration = float(json.loads(probe.stdout)['format']['duration'])
        except (ValueError, KeyError, TypeError) as exc:
            raise AppError('GPU encode test', 'ffprobe did not return a valid duration: {}'.format(exc), 'Repair FFmpeg and retry the GPU test.') from exc
        if not tmp.is_file() or tmp.stat().st_size == 0 or not 1.5 <= duration <= 2.5:
            raise AppError('GPU encode test', 'Test output is empty or has unexpected duration: {} seconds.'.format(duration), 'Check AMD drivers and retry the GPU test.')
        os.replace(tmp, target)
        logger.info('GPU encode test passed: %s; %.2f seconds.', target, duration)
        return entry('AMF encode test', 'PASS', 'Encoded {:.2f} seconds to {}.'.format(duration, target))
    except subprocess.TimeoutExpired as exc:
        raise AppError('GPU encode test', 'The encoder or probe timed out: {}'.format(exc), 'Check AMD drivers and retry the GPU test.') from exc
    except OSError as exc:
        raise AppError('GPU encode test', 'Could not run or save the test: {}'.format(exc), 'Check FFmpeg, workspace permissions, and free space.') from exc
    finally:
        tmp.unlink(missing_ok=True)
