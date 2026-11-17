import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from app import brain_ai, health, storage
from app.common import AppError
from app.main import app


def response(length=10):
    return json.dumps({'scenes': [{'title': 'Scene', 'video_prompt': 'x' * length}]})


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_not_configured_uses_ollama(self):
        with patch.object(brain_ai, 'gemini_generate', new_callable=AsyncMock) as gemini, patch.object(brain_ai, 'ollama_generate', new=AsyncMock(return_value=response())) as ollama:
            result = await brain_ai.generate_script('topic', 1, {})
            gemini.assert_not_awaited()
            ollama.assert_awaited_once()
            self.assertIn('Ollama', result['answered_by'])
            self.assertIn('not configured', result['answered_by'])

    async def test_429_fallback_then_next_request_tries_gemini_again(self):
        request = httpx.Request('POST', 'https://example.com')
        resp = httpx.Response(429, text='rate limited', request=request)
        failure = httpx.HTTPStatusError('429 rate limited', request=request, response=resp)
        config = {'GEMINI_API_KEY': 'key', 'GEMINI_MODEL': 'configured-model'}
        with patch.object(brain_ai, 'gemini_generate', new=AsyncMock(side_effect=[failure, response()])) as gemini, patch.object(brain_ai, 'ollama_generate', new=AsyncMock(return_value=response())) as ollama:
            first = await brain_ai.generate_script('topic', 1, config)
            second = await brain_ai.generate_script('topic', 1, config)
            self.assertIn('429', first['answered_by'])
            self.assertEqual('Gemini', second['answered_by'])
            self.assertEqual(2, gemini.await_count)
            self.assertEqual(1, ollama.await_count)

    async def test_both_fail_report_both_reasons_and_redact_key(self):
        config = {'GEMINI_API_KEY': 'private-key', 'GEMINI_MODEL': 'test'}
        with patch.object(brain_ai, 'gemini_generate', new=AsyncMock(side_effect=ValueError('429 private-key'))), patch.object(brain_ai, 'ollama_generate', new=AsyncMock(side_effect=ConnectionError('not reachable'))):
            with self.assertRaises(AppError) as context:
                await brain_ai.generate_script('topic', 1, config)
        self.assertIn('Gemini:', context.exception.real_cause)
        self.assertIn('429', context.exception.real_cause)
        self.assertIn('Ollama:', context.exception.real_cause)
        self.assertIn('not reachable', context.exception.real_cause)
        self.assertNotIn('private-key', context.exception.real_cause)

    async def test_invalid_gemini_uses_ollama(self):
        config = {'GEMINI_API_KEY': 'key', 'GEMINI_MODEL': 'test'}
        with patch.object(brain_ai, 'gemini_generate', new=AsyncMock(return_value='not JSON')), patch.object(brain_ai, 'ollama_generate', new=AsyncMock(return_value=response())):
            result = await brain_ai.generate_script('topic', 1, config)
            self.assertIn('not valid JSON', result['answered_by'])

    async def test_length_boundary_and_shorten_retry(self):
        self.assertEqual([], brain_ai.overlong(brain_ai.validate_scenes(response(880), 1)))
        self.assertEqual([(1, 881)], brain_ai.overlong(brain_ai.validate_scenes(response(881), 1)))
        with patch.object(brain_ai, 'ollama_generate', new=AsyncMock(side_effect=[response(881), response(880)])) as ollama:
            result = await brain_ai.generate_script('topic', 1, {})
            self.assertEqual(880, len(result['scenes'][0]['video_prompt']))
            self.assertEqual(2, ollama.await_count)
            self.assertIn('previous response had overlong prompts', ollama.await_args.args[0])
        with patch.object(brain_ai, 'ollama_generate', new=AsyncMock(return_value=response(881))) as ollama:
            with self.assertRaises(AppError) as context:
                await brain_ai.generate_script('topic', 1, {})
            self.assertIn('two shortening retries', context.exception.real_cause)
            self.assertEqual(3, ollama.await_count)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = {'AURACUT_WORKSPACE': str(self.root / 'work'), 'AURACUT_DRIVE_BACKUP': str(self.root / 'drive')}
        for target in ('app.storage.workspace', 'app.main.workspace'):
            patcher = patch(target, return_value=self.root / 'work')
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.root / 'work' / 'Input_Media').mkdir(parents=True)
        (self.root / 'work' / 'Output_Renders').mkdir()
        (self.root / 'drive').mkdir()
        (self.root / 'work' / 'Input_Media' / 'test.mp4').write_bytes(b'video data')

    def test_backup_success_and_no_overwrite(self):
        result = storage.backup_file(self.config, 'Input_Media', 'test.mp4')
        self.assertTrue(result['ok'])
        self.assertEqual(b'video data', (self.root / 'drive' / 'test.mp4').read_bytes())
        with self.assertRaises(AppError) as context:
            storage.backup_file(self.config, 'Input_Media', 'test.mp4')
        self.assertIn('not overwritten', context.exception.real_cause)

    def test_size_mismatch_removes_incomplete_copy(self):
        def partial(src, dst, length):
            dst.write(b'x')
        with patch.object(storage.shutil, 'copyfileobj', side_effect=partial):
            with self.assertRaises(AppError) as context:
                storage.backup_file(self.config, 'Input_Media', 'test.mp4')
        self.assertIn('Size mismatch', context.exception.real_cause)
        self.assertFalse((self.root / 'drive' / 'test.mp4').exists())

    def test_missing_drive_is_recoverable(self):
        self.config['AURACUT_DRIVE_BACKUP'] = str(self.root / 'missing')
        with self.assertRaises(AppError) as context:
            storage.backup_file(self.config, 'Input_Media', 'test.mp4')
        self.assertIn('missing', context.exception.real_cause)
        self.assertEqual(1, len(storage.list_files(self.config)))
        self.assertFalse(storage.list_files(self.config)[0]['backed_up'])

    def test_gpu_test_checks_duration_before_saving(self):
        from subprocess import CompletedProcess
        def fake_run(args, **kwargs):
            if args[0] == 'ffmpeg':
                Path(args[-1]).write_bytes(b'fake encoded video')
                return CompletedProcess(args, 0, '', '')
            return CompletedProcess(args, 0, '{"format":{"duration":"2.0"}}', '')
        with patch('app.health.workspace', return_value=self.root / 'work'), patch('app.health.shutil.which', side_effect=lambda name: name), patch('app.health.subprocess.run', side_effect=fake_run):
            result = health.gpu_test(self.config)
        self.assertEqual('PASS', result['status'])
        self.assertTrue((self.root / 'work' / 'Output_Renders' / '_amf_test.mp4').exists())

    def test_health_survives_bad_configuration(self):
        with patch('app.main.settings', side_effect=ValueError('bad line')):
            with TestClient(app) as client:
                result = client.get('/api/health')
                self.assertEqual('AuraCut Studio', result.json()['app'])
                self.assertEqual('FAIL', result.json()['checks'][0]['status'])
                self.assertIn('bad line', result.json()['checks'][0]['detail'])

    def test_api_workflow_and_errors(self):
        with patch('app.main.settings', return_value=self.config), patch('app.main.brain_ai.ollama_generate', new=AsyncMock(return_value=response())):
            with TestClient(app) as client:
                self.assertEqual(200, client.get('/').status_code)
                self.assertEqual(200, client.get('/static/alpine.min.js').status_code)
                denied = client.post('/api/script', headers={'Origin': 'https://example.com'}, json={'topic': 'test', 'scene_count': 1})
                self.assertEqual(403, denied.status_code)
                self.assertEqual(1, len(client.get('/api/files').json()['files']))
                result = client.post('/api/script', json={'topic': 'test', 'scene_count': 1})
                self.assertEqual(200, result.status_code)
                self.assertIn('Ollama', result.json()['answered_by'])
                invalid = client.post('/api/script', json={'topic': 'test', 'scene_count': 9})
                self.assertEqual(422, invalid.status_code)
                self.assertTrue(all(k in invalid.json() for k in ('what_failed', 'real_cause', 'fix')))
                backup = client.post('/api/backup', json={'name': 'test.mp4', 'folder': 'Input_Media'})
                self.assertTrue(backup.json()['ok'])
                self.assertTrue(client.get('/api/files').json()['files'][0]['backed_up'])
                self.assertTrue(client.get('/api/logs').json()['lines'])

    def test_truncated_upload_is_cleaned_up(self):
        with patch('app.main.settings', return_value=self.config):
            with TestClient(app) as client:
                body = b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="truncated.mp4"\r\n\r\npartial'
                result = client.post('/api/upload', content=body, headers={'Content-Type': 'multipart/form-data; boundary=boundary'})
                self.assertEqual(400, result.status_code)
                self.assertFalse((self.root / 'work' / 'Input_Media' / 'truncated.mp4').exists())

    def test_upload_rejects_path_tricks(self):
        with patch('app.main.settings', return_value=self.config):
            with TestClient(app) as client:
                for name in ('../bad.mp4', '..\\bad.mp4', 'C:\\secret.mp4', 'CON.mp4', 'bad:stream.mp4'):
                    result = client.post('/api/upload', files={'file': (name, b'data', 'video/mp4')})
                    self.assertEqual(400, result.status_code, name)
                    self.assertFalse(result.json()['ok'])
                result = client.post('/api/upload', files={'file': ('test.mp4', b'new', 'video/mp4')})
                self.assertEqual(200, result.status_code)
                self.assertNotEqual('test.mp4', result.json()['name'])
                self.assertEqual(b'video data', (self.root / 'work' / 'Input_Media' / 'test.mp4').read_bytes())


if __name__ == '__main__':
    unittest.main()
