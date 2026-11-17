import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app import brain_ai, health
from app.common import LOG_LINES
from app.main import app


class TimeoutTests(unittest.IsolatedAsyncioTestCase):
    def test_default_minimum_and_invalid_values(self):
        self.assertEqual((12, None), brain_ai.gemini_timeout({}))
        self.assertEqual((15.5, None), brain_ai.gemini_timeout({'GEMINI_TIMEOUT_SECONDS': '15.5'}))
        timeout, warning = brain_ai.gemini_timeout({'GEMINI_TIMEOUT_SECONDS': '4'})
        self.assertEqual(10, timeout)
        self.assertIn('below the 10-second minimum', warning)
        for value in ('not-a-number', 'nan', 'inf'):
            timeout, warning = brain_ai.gemini_timeout({'GEMINI_TIMEOUT_SECONDS': value})
            self.assertEqual(12, timeout)
            self.assertIn('not a finite number', warning)

    async def test_sdk_timeout_one_attempt_and_disabled_function_calls(self):
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(return_value=SimpleNamespace(text='OK'))
        client.aio.aclose = AsyncMock()
        config = {'GEMINI_API_KEY': 'secret', 'GEMINI_MODEL': 'some-model', 'GEMINI_TIMEOUT_SECONDS': '4'}
        with patch('app.brain_ai.genai.Client', return_value=client) as constructor, patch('app.brain_ai.asyncio.wait_for', wraps=asyncio.wait_for) as wait:
            with self.assertLogs('auracut', level='WARNING') as logs:
                result = await brain_ai.gemini_generate('test prompt', config)
        self.assertEqual('OK', result)
        options = constructor.call_args.kwargs['http_options']
        self.assertEqual(10000, options.timeout)
        self.assertEqual(1, options.retry_options.attempts)
        self.assertEqual(10, wait.call_args.kwargs['timeout'])
        client.aio.models.generate_content.assert_awaited_once()
        generate_config = client.aio.models.generate_content.call_args.kwargs['config']
        self.assertTrue(generate_config.automatic_function_calling.disable)
        self.assertEqual(1, generate_config.automatic_function_calling.maximum_remote_calls)
        client.aio.aclose.assert_awaited_once()
        self.assertIn('below the 10-second minimum', '\n'.join(logs.output))


class GeminiHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_listed_model_is_unverified(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        response = MagicMock()
        response.json.return_value = {'models': [{'name': 'models/test-model', 'supportedGenerationMethods': ['generateContent']}]}
        client.get = AsyncMock(return_value=response)
        with patch('app.health.httpx.AsyncClient', return_value=client):
            result = await health.gemini_health({'GEMINI_API_KEY': 'key', 'GEMINI_MODEL': 'test-model'})
        self.assertEqual('WARN', result['status'])
        self.assertIn('Key and model look valid (not tested)', result['detail'])
        self.assertIn('Test Gemini now', result['fix'])


class ApiFixTests(unittest.TestCase):
    def setUp(self):
        self.config = {'GEMINI_API_KEY': 'secret-key', 'GEMINI_MODEL': 'test-model', 'GEMINI_TIMEOUT_SECONDS': '4'}

    def test_gemini_test_success_makes_one_real_provider_call_when_mocked(self):
        mock_call = AsyncMock(return_value='OK')
        with patch('app.main.settings', return_value=self.config), patch('app.health.gemini_generate', new=mock_call):
            with TestClient(app) as client:
                result = client.post('/api/gemini-test').json()
        mock_call.assert_awaited_once_with('Reply with the word OK.', self.config)
        self.assertEqual('PASS', result['check']['status'])
        self.assertEqual('', result['check']['fix'])
        self.assertEqual('WARN', result['timeout_warning']['status'])
        self.assertNotIn('secret-key', json_text(result))

    def test_gemini_test_failure_includes_cause_fix_and_redacts_key(self):
        with patch('app.main.settings', return_value=self.config), patch('app.health.gemini_generate', new=AsyncMock(side_effect=RuntimeError('429 secret-key'))):
            with TestClient(app) as client:
                result = client.post('/api/gemini-test').json()
        self.assertEqual('FAIL', result['check']['status'])
        self.assertIn('429', result['check']['detail'])
        self.assertNotIn('secret-key', json_text(result))
        self.assertTrue(result['check']['fix'])
        self.assertIn('429', '\n'.join(LOG_LINES))

    def test_empty_or_missing_test_response_fails(self):
        with patch('app.main.settings', return_value=self.config), patch('app.health.gemini_generate', new=AsyncMock(return_value=' ')):
            with TestClient(app) as client:
                result = client.post('/api/gemini-test').json()
        self.assertEqual('FAIL', result['check']['status'])
        self.assertIn('empty response', result['check']['detail'])
        with patch('app.main.settings', return_value={'GEMINI_MODEL': 'test'}), patch('app.health.gemini_generate', new_callable=AsyncMock) as generate:
            with TestClient(app) as client:
                result = client.post('/api/gemini-test').json()
        self.assertEqual('FAIL', result['check']['status'])
        generate.assert_not_awaited()

    def test_timeout_warning_in_health_and_empty_fix_on_pass(self):
        with patch('app.main.settings', return_value=self.config), patch('app.health.local_checks', return_value=[health.entry('Python', 'PASS', 'Python 3.12', 'must not appear')]), patch('app.health.ollama_health', new=AsyncMock(return_value=health.entry('Ollama', 'PASS', 'OK'))), patch('app.health.gemini_health', new=AsyncMock(return_value=health.entry('Gemini', 'WARN', 'Key and model look valid (not tested).', 'Click Test Gemini now.'))):
            with TestClient(app) as client:
                checks = client.get('/api/health').json()['checks']
        python = next(check for check in checks if check['name'] == 'Python')
        self.assertEqual('', python['fix'])
        warning = next(check for check in checks if check['name'] == 'Gemini timeout')
        self.assertEqual('WARN', warning['status'])
        self.assertIn('Using 10 seconds', warning['detail'])
        page = (Path(__file__).resolve().parents[1] / 'app' / 'static' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('x-if="check.status !== \'PASS\' ? check.fix : false"', page)

    def test_favicon_and_404_log_method_and_path(self):
        with TestClient(app) as client:
            before = len(LOG_LINES)
            self.assertEqual(204, client.get('/favicon.ico').status_code)
            self.assertEqual(before, len(LOG_LINES))
            self.assertEqual(404, client.get('/no-such-page').status_code)
            self.assertIn('404 GET /no-such-page', '\n'.join(LOG_LINES))
            self.assertEqual(404, client.post('/no-such-api').status_code)
            self.assertIn('404 POST /no-such-api', '\n'.join(LOG_LINES))
            self.assertEqual(404, client.get('/static/not-found.js').status_code)
            self.assertIn('404 GET /static/not-found.js', '\n'.join(LOG_LINES))


def json_text(value):
    import json
    return json.dumps(value)


if __name__ == '__main__':
    unittest.main()
