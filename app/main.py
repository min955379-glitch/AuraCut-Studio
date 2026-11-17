import asyncio
import os
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from python_multipart.multipart import MultipartParser, parse_options_header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.concurrency import run_in_threadpool

from app import brain_ai, health, storage
from app.common import AppError, LOG_LINES, ROOT, error_data, logger, settings, workspace

app = FastAPI(title='AuraCut Studio', docs_url=None, redoc_url=None, openapi_url=None)
app.mount('/static', StaticFiles(directory=str(ROOT / 'app' / 'static')), name='static')


@app.middleware('http')
async def local_origin_only(request, call_next):
    # Browsers send Origin on cross-site writes; deny remote pages access to
    # local files even though this Phase 1 app has no login system.
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin not in ('http://127.0.0.1:8000', 'http://localhost:8000'):
        return JSONResponse(status_code=403, content=error_data('Request origin', 'A write was attempted from an untrusted origin: {}'.format(origin),
                            'Open AuraCut directly at http://127.0.0.1:8000 and retry.'))
    return await call_next(request)


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    try:
        config = settings()
    except Exception:
        config = {}
    return JSONResponse(status_code=500, content=error_data('Unexpected server error', '{}: {}'.format(type(exc).__name__, exc),
                        'Check the log drawer, correct the reported cause, and retry.', config))


@app.exception_handler(AppError)
async def known_error(request, exc):
    try:
        config = settings()
    except Exception:
        config = {}
    return JSONResponse(status_code=exc.status, content=error_data(exc.what_failed, exc.real_cause, exc.fix, config))


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    errors = '; '.join('{}: {}'.format('.'.join(str(part) for part in e['loc']), e['msg']) for e in exc.errors())
    return JSONResponse(status_code=422, content=error_data('Invalid request', errors, 'Check the input fields and try again.'))


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    cause = str(exc.detail)
    if exc.status_code == 404:
        cause = '404 {} {}: {}'.format(request.method, request.url.path, exc.detail)
    return JSONResponse(status_code=exc.status_code, content=error_data('HTTP request', cause, 'Check the address and refresh the page.'))


@app.api_route('/favicon.ico', methods=['GET', 'HEAD'])
def favicon():
    return Response(status_code=204)


@app.get('/')
def index():
    return FileResponse(ROOT / 'app' / 'static' / 'index.html')


@app.get('/api/health')
async def get_health():
    try:
        config = settings()
    except (OSError, UnicodeError, ValueError) as exc:
        check = health.entry('Configuration', 'FAIL', '{}: {}'.format(type(exc).__name__, exc),
                             'Check the .env file is readable UTF-8 and each setting uses NAME=value.')
        return {'ok': True, 'app': 'AuraCut Studio', 'checks': [check]}
    async def limited_gemini():
        try:
            return await asyncio.wait_for(health.gemini_health(config), timeout=8)
        except asyncio.TimeoutError:
            return health.entry('Gemini', 'WARN', 'Listing Gemini models timed out after 8 seconds.', 'Check internet access and retry health.')
    local, ollama, gemini = await asyncio.gather(run_in_threadpool(health.local_checks, config), health.ollama_health(config), limited_gemini())
    checks = local + [ollama, gemini, health.entry('AMF encode test', 'WARN', 'Not run by this health check.', 'Click Run GPU encode test to verify the hardware encoder.')]
    timeout_warning = health.timeout_check(config)
    if timeout_warning:
        checks.append(timeout_warning)
    return {'ok': True, 'app': 'AuraCut Studio', 'checks': checks,
            'gemini_test_context': health.gemini_test_context(config)}


@app.post('/api/gemini-test')
async def gemini_test():
    config = settings()
    check = await health.test_gemini(config)
    return {'ok': True, 'check': check, 'timeout_warning': health.timeout_check(config),
            'gemini_test_context': health.gemini_test_context(config)}


@app.post('/api/gpu-test')
async def test_gpu():
    result = await run_in_threadpool(health.gpu_test, settings())
    return {'ok': True, 'check': result}


@app.post('/api/upload')
async def upload(request: Request):
    # Parse incrementally: Starlette's default form parser spools large files to
    # the OS temp directory, which violates the local-workspace-only rule.
    content_type, options = parse_options_header(request.headers.get('content-type', ''))
    boundary = options.get(b'boundary')
    if content_type != b'multipart/form-data' or not boundary:
        raise AppError('Upload', 'The request has no valid multipart boundary.', 'Choose a file and upload it again.')
    root = workspace(settings()) / 'Input_Media'
    state = {'path': None, 'output': None, 'headers': {}, 'field': b'', 'value': b'', 'count': 0, 'finished': False}

    def part_begin():
        state.update(headers={}, field=b'', value=b'')
    def header_field(data, start, end):
        state['field'] += data[start:end]
    def header_value(data, start, end):
        state['value'] += data[start:end]
    def header_end():
        state['headers'][state['field'].lower()] = state['value']
        state['field'], state['value'] = b'', b''
    def headers_finished():
        state['count'] += 1
        raw = state['headers'].get(b'content-disposition', b'')
        match = re.search(rb'filename\s*=\s*(?:"((?:\\.|[^"\\])*)"|([^;]*))', raw, re.I)
        if state['count'] != 1 or not match:
            raise AppError('Upload', 'The request must contain exactly one named file.', 'Choose one file and upload it again.')
        raw_name = match.group(1) if match.group(1) is not None else match.group(2).strip()
        if any(c in raw_name for c in (b'/', b'\\', b':')):
            raise AppError('Upload', 'The filename contains a folder path or drive prefix.', 'Rename the file without folder paths, then retry.')
        _, disposition = parse_options_header(raw)
        if disposition.get(b'name') != b'file' or b'filename' not in disposition:
            raise AppError('Upload', 'The file field or filename is missing.', 'Choose a file and upload it again.')
        try:
            name = disposition[b'filename'].decode('utf-8')
        except UnicodeDecodeError as exc:
            raise AppError('Upload', 'Filename is not valid UTF-8: {}'.format(exc), 'Rename the file and upload it again.') from exc
        if not storage.valid_name(name):
            raise AppError('Upload', 'Unsafe or invalid filename: {}'.format(name), 'Rename the file without folder paths or Windows-forbidden characters, then retry.')
        stem, suffix = Path(name).stem, Path(name).suffix
        path = root / name
        for _ in range(5):
            try:
                state['output'] = path.open('xb')
                state['path'] = path
                break
            except FileExistsError:
                path = root / '{}_{}{}'.format(stem[:170], uuid.uuid4().hex[:10], suffix)
        if state['output'] is None:
            raise AppError('Upload', 'Could not choose a unique filename.', 'Try the upload again.')
    def part_data(data, start, end):
        if state['output'] is None:
            raise AppError('Upload', 'File data arrived before a valid filename.', 'Choose a file and upload it again.')
        state['output'].write(data[start:end])
    def part_end():
        if state['output']:
            state['output'].close()
    def finish():
        state['finished'] = True
    callbacks = {'on_part_begin': part_begin, 'on_header_field': header_field, 'on_header_value': header_value,
                 'on_header_end': header_end, 'on_headers_finished': headers_finished,
                 'on_part_data': part_data, 'on_part_end': part_end, 'on_end': finish}
    parser = MultipartParser(boundary, callbacks)
    try:
        async for chunk in request.stream():
            parser.write(chunk)
        parser.finalize()
        if not state['finished'] or not state['path']:
            raise AppError('Upload', 'The upload ended before the file was complete.', 'Check the connection and upload the file again.')
        path = state['path']
        logger.info('Uploaded to local workspace: %s', path)
        return {'ok': True, 'name': path.name, 'size': path.stat().st_size}
    except Exception:
        if state['output'] and not state['output'].closed:
            state['output'].close()
        if state['path']:
            state['path'].unlink(missing_ok=True)
        raise
    finally:
        parser.close()


@app.get('/api/files')
def files():
    return {'ok': True, 'files': storage.list_files(settings())}


class FileChoice(BaseModel):
    folder: str
    name: str


@app.post('/api/backup')
def backup(choice: FileChoice):
    result = storage.backup_file(settings(), choice.folder, choice.name)
    logger.info(result['message'])
    return result


class ScriptRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=4000)
    scene_count: int = Field(ge=1, le=8)


@app.post('/api/script')
async def script(data: ScriptRequest):
    if not data.topic.strip():
        raise AppError('Script generation', 'The topic is empty.', 'Type a topic before clicking Generate.')
    config = settings()
    result = await brain_ai.generate_script(data.topic.strip(), data.scene_count, config)
    result['timeout_warning'] = health.timeout_check(config)
    logger.info('Script generated by %s.', result['answered_by'])
    return result


@app.get('/api/logs')
def logs():
    return {'ok': True, 'lines': list(LOG_LINES)}

class ClientError(BaseModel):
    what_failed: str = Field(max_length=200)
    real_cause: str = Field(max_length=2000)
    fix: str = Field(max_length=1000)


@app.post('/api/client-error')
def client_error(data: ClientError):
    error_data(data.what_failed, data.real_cause, data.fix, settings())
    return {'ok': True}
