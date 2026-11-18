"""Launcher tests use mocked network and processes; no actual Ollama is started."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import run


class FakeClock:
    def __init__(self):
        self.now = 0
        self.waits = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class SettingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = patch.object(run, 'ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_true_and_explicit_false_or_true(self):
        self.assertTrue(run.ollama_autostart_enabled())
        (self.root / '.env').write_text('AURACUT_START_OLLAMA=false\n', encoding='utf-8')
        self.assertFalse(run.ollama_autostart_enabled())
        (self.root / '.env').write_text('AURACUT_START_OLLAMA=false\nAURACUT_START_OLLAMA="true"\n', encoding='utf-8')
        self.assertTrue(run.ollama_autostart_enabled())

    def test_invalid_setting_warns_and_skips_auto_start(self):
        (self.root / '.env').write_text('AURACUT_START_OLLAMA=maybe\n', encoding='utf-8')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertFalse(run.ollama_autostart_enabled())
        self.assertIn('not true or false', out.getvalue())
        self.assertIn('Fix:', out.getvalue())

    def test_unreadable_settings_warn_and_do_not_start(self):
        with patch.object(Path, 'read_text', side_effect=PermissionError('denied')):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertFalse(run.ollama_autostart_enabled())
        self.assertIn('denied', out.getvalue())
        self.assertIn('Fix:', out.getvalue())


class PrepareTests(unittest.TestCase):
    def capture(self, *patches):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for item in patches:
            stack.enter_context(item)
        output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        return output

    def test_already_running_checks_model_does_not_start(self):
        with patch.object(run, 'ollama_autostart_enabled', return_value=True), patch.object(run, 'ollama_probe', return_value=(True, '')), patch.object(run, 'check_ollama_model') as model, patch.object(run, 'find_ollama') as find, patch.object(run.subprocess, 'Popen') as popen, contextlib.redirect_stdout(io.StringIO()) as out:
            run.prepare_ollama()
        self.assertIn('Ollama is already running', out.getvalue())
        model.assert_called_once()
        find.assert_not_called()
        popen.assert_not_called()

    def test_disabled_checks_existing_service_but_never_launches(self):
        with patch.object(run, 'ollama_autostart_enabled', return_value=False), patch.object(run, 'ollama_probe', return_value=(False, 'refused')), patch.object(run, 'find_ollama') as find, patch.object(run.subprocess, 'Popen') as popen, contextlib.redirect_stdout(io.StringIO()) as out:
            run.prepare_ollama()
        self.assertIn('auto-start is disabled', out.getvalue())
        find.assert_not_called()
        popen.assert_not_called()

    def test_disabled_already_running_still_checks_model(self):
        with patch.object(run, 'ollama_autostart_enabled', return_value=False), patch.object(run, 'ollama_probe', return_value=(True, '')), patch.object(run, 'check_ollama_model') as check, patch.object(run, 'find_ollama') as find:
            run.prepare_ollama()
        check.assert_called_once()
        find.assert_not_called()

    def test_start_cli_hidden_and_poll_until_ready(self):
        clock = FakeClock()
        proc = MagicMock()
        proc.poll.return_value = None
        with patch.object(run, 'IS_WINDOWS', True), patch.object(run, 'ollama_autostart_enabled', return_value=True), patch.object(run, 'ollama_probe', side_effect=[(False, 'refused'), (False, 'refused'), (True, '')]), patch.object(run, 'find_ollama', return_value=('ollama.exe', False)), patch.object(run, 'ollama_log_file', return_value=(None, None)), patch.object(run.subprocess, 'Popen', return_value=proc) as popen, patch.object(run.time, 'monotonic', side_effect=clock.monotonic), patch.object(run.time, 'sleep', side_effect=clock.sleep), patch.object(run, 'check_ollama_model') as model, contextlib.redirect_stdout(io.StringIO()) as out:
            run.prepare_ollama()
        self.assertIn('Starting Ollama...', out.getvalue())
        self.assertIn('Ollama is ready', out.getvalue())
        self.assertEqual([1, 1], clock.waits)
        self.assertEqual(['ollama.exe', 'serve'], popen.call_args.args[0])
        flags = popen.call_args.kwargs['creationflags']
        self.assertTrue(flags & 0x08000000)
        self.assertTrue(flags & 0x00000200)
        model.assert_called_once()
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()
        proc.wait.assert_not_called()

    def test_gui_app_is_launched_without_serve_even_if_app_exits(self):
        clock = FakeClock()
        proc = MagicMock()
        proc.poll.return_value = 0
        with patch.object(run, 'IS_WINDOWS', True), patch.object(run, 'ollama_autostart_enabled', return_value=True), patch.object(run, 'ollama_probe', side_effect=[(False, 'refused'), (False, 'starting'), (True, '')]), patch.object(run, 'find_ollama', return_value=('ollama app.exe', True)), patch.object(run, 'ollama_log_file', return_value=(None, None)), patch.object(run.subprocess, 'Popen', return_value=proc) as popen, patch.object(run.time, 'monotonic', side_effect=clock.monotonic), patch.object(run.time, 'sleep', side_effect=clock.sleep), patch.object(run, 'check_ollama_model') as model:
            run.prepare_ollama()
        self.assertEqual(['ollama app.exe'], popen.call_args.args[0])
        model.assert_called_once()
        proc.terminate.assert_not_called()

    def test_start_timeout_shows_cause_and_does_not_stop_process(self):
        clock = FakeClock()
        proc = MagicMock()
        proc.poll.return_value = None
        with patch.object(run, 'ollama_autostart_enabled', return_value=True), patch.object(run, 'ollama_probe', return_value=(False, 'Connection refused')), patch.object(run, 'find_ollama', return_value=('ollama', False)), patch.object(run, 'ollama_log_file', return_value=(None, None)), patch.object(run.subprocess, 'Popen', return_value=proc), patch.object(run.time, 'monotonic', side_effect=clock.monotonic), patch.object(run.time, 'sleep', side_effect=clock.sleep), contextlib.redirect_stdout(io.StringIO()) as out:
            run.prepare_ollama()
        self.assertEqual(20, clock.now)
        self.assertEqual([1] * 20, clock.waits)
        self.assertIn('Not reachable after 20 seconds: Connection refused', out.getvalue())
        self.assertIn('Fix:', out.getvalue())
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()

    def test_gui_timeout_reports_exit_code(self):
        clock = FakeClock()
        proc = MagicMock()
        proc.poll.return_value = 2
        with patch.object(run, 'ollama_autostart_enabled', return_value=True), patch.object(run, 'ollama_probe', return_value=(False, 'refused')), patch.object(run, 'find_ollama', return_value=('ollama app.exe', True)), patch.object(run, 'ollama_log_file', return_value=(None, None)), patch.object(run.subprocess, 'Popen', return_value=proc), patch.object(run.time, 'monotonic', side_effect=clock.monotonic), patch.object(run.time, 'sleep', side_effect=clock.sleep), contextlib.redirect_stdout(io.StringIO()) as out:
            run.prepare_ollama()
        self.assertIn('App process exited with code 2', out.getvalue())
        proc.terminate.assert_not_called()

    def test_not_installed_is_one_clear_warning_and_no_start(self):
        with patch.object(run, 'ollama_autostart_enabled', return_value=True), patch.object(run, 'ollama_probe', return_value=(False, 'refused')), patch.object(run, 'find_ollama', return_value=(None, False)), patch.object(run.subprocess, 'Popen') as popen, contextlib.redirect_stdout(io.StringIO()) as out:
            run.prepare_ollama()
        self.assertEqual(1, len(out.getvalue().splitlines()))
        self.assertIn('https://ollama.com', out.getvalue())
        self.assertIn('Fix:', out.getvalue())
        popen.assert_not_called()

    def test_failed_process_reports_exit_and_log(self):
        clock = FakeClock()
        proc = MagicMock()
        proc.poll.return_value = 7
        proc.returncode = 7
        with patch.object(run, 'ollama_autostart_enabled', return_value=True), patch.object(run, 'ollama_probe', return_value=(False, 'refused')), patch.object(run, 'find_ollama', return_value=('ollama', False)), patch.object(run, 'ollama_log_file', return_value=(None, None)), patch.object(run, 'ollama_log_tail', return_value='bind failed'), patch.object(run.subprocess, 'Popen', return_value=proc), patch.object(run.time, 'monotonic', side_effect=clock.monotonic), patch.object(run.time, 'sleep', side_effect=clock.sleep), contextlib.redirect_stdout(io.StringIO()) as out:
            run.prepare_ollama()
        self.assertIn('exited with code 7', out.getvalue())
        self.assertIn('bind failed', out.getvalue())
        self.assertIn('Fix:', out.getvalue())

    def test_failed_spawn_reports_real_error(self):
        with patch.object(run, 'ollama_autostart_enabled', return_value=True), patch.object(run, 'ollama_probe', return_value=(False, 'refused')), patch.object(run, 'find_ollama', return_value=('ollama', False)), patch.object(run, 'ollama_log_file', return_value=(None, None)), patch.object(run.subprocess, 'Popen', side_effect=PermissionError('access denied')), contextlib.redirect_stdout(io.StringIO()) as out:
            run.prepare_ollama()
        self.assertIn('PermissionError: access denied', out.getvalue())
        self.assertIn('Fix:', out.getvalue())

    def test_launcher_continues_to_start_server_if_ollama_missing(self):
        server = MagicMock()
        server.poll.return_value = None
        server.wait.return_value = 0
        with patch.object(run, 'ollama_autostart_enabled', return_value=True), patch.object(run, 'ollama_probe', return_value=(False, 'refused')), patch.object(run, 'find_ollama', return_value=(None, False)), patch.object(run, 'port_busy', return_value=False), patch.object(run, 'setup'), patch.object(run, 'is_auracut', return_value=True), patch.object(run, 'browser'), patch.object(run.subprocess, 'Popen', return_value=server) as popen, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(0, run.main())
        self.assertIn('Ollama was not found', out.getvalue())
        self.assertIn('AuraCut is ready', out.getvalue())
        self.assertIn('uvicorn', popen.call_args.args[0])


class DiscoveryTests(unittest.TestCase):
    def test_windows_install_folder_prefers_app_over_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'Programs' / 'Ollama'
            folder.mkdir(parents=True)
            cli, gui = folder / 'ollama.exe', folder / 'ollama app.exe'
            cli.touch()
            gui.touch()
            with patch.object(run, 'IS_WINDOWS', True), patch.object(run.shutil, 'which', return_value=None), patch.dict(run.os.environ, {'LOCALAPPDATA': temp}, clear=True):
                self.assertEqual((str(gui), True), run.find_ollama())
                gui.unlink()
                self.assertEqual((str(cli), False), run.find_ollama())

    def test_path_cli_checks_for_adjacent_app(self):
        with tempfile.TemporaryDirectory() as temp:
            cli, gui = Path(temp) / 'ollama.exe', Path(temp) / 'ollama app.exe'
            cli.touch()
            gui.touch()
            def which(name):
                return str(cli) if name == 'ollama' else None
            with patch.object(run, 'IS_WINDOWS', True), patch.object(run.shutil, 'which', side_effect=which), patch.dict(run.os.environ, {}, clear=True):
                self.assertEqual((str(gui), True), run.find_ollama())


class NetworkTests(unittest.TestCase):
    def test_model_missing_shows_exact_pull_command(self):
        payload = io.BytesIO(json.dumps({'models': [{'name': 'different:model'}]}).encode())
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = payload
        with patch.object(run.urllib.request, 'build_opener', return_value=opener), contextlib.redirect_stdout(io.StringIO()) as out:
            run.check_ollama_model()
        self.assertIn('ollama pull qwen2.5:7b-instruct-q4_K_M', out.getvalue())
        self.assertIn('Fix:', out.getvalue())
        self.assertEqual(run.OLLAMA_URL + '/api/tags', opener.open.call_args.args[0])

    def test_installed_model_does_not_request_download(self):
        payload = io.BytesIO(json.dumps({'models': [{'name': run.OLLAMA_MODEL}]}).encode())
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = payload
        with patch.object(run.urllib.request, 'build_opener', return_value=opener), contextlib.redirect_stdout(io.StringIO()) as out:
            run.check_ollama_model()
        self.assertNotIn('ollama pull', out.getvalue())
        self.assertEqual(1, opener.open.call_count)

    def test_probe_uses_local_http_and_reports_network_failure(self):
        opener = MagicMock()
        opener.open.side_effect = OSError('connection refused')
        with patch.object(run.urllib.request, 'build_opener', return_value=opener), patch.object(run.urllib.request, 'ProxyHandler') as proxy:
            ready, cause = run.ollama_probe()
        self.assertFalse(ready)
        self.assertIn('connection refused', cause)
        proxy.assert_called_once_with({})
        self.assertEqual(run.OLLAMA_URL + '/', opener.open.call_args.args[0])

    def test_model_list_failure_reports_actual_cause(self):
        opener = MagicMock()
        opener.open.side_effect = OSError('connection reset')
        with patch.object(run.urllib.request, 'build_opener', return_value=opener), contextlib.redirect_stdout(io.StringIO()) as out:
            run.check_ollama_model()
        self.assertIn('connection reset', out.getvalue())
        self.assertIn('Fix:', out.getvalue())

    def test_log_tail_ignores_old_entries_and_sensitive_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'ollama-start.log'
            path.write_text('old error\n', encoding='utf-8')
            offset = path.stat().st_size
            with path.open('a', encoding='utf-8') as f:
                f.write('API_KEY=private\nnew failure\n')
            text = run.ollama_log_tail(path, offset)
            self.assertIn('new failure', text)
            self.assertNotIn('old error', text)
            self.assertNotIn('private', text)


if __name__ == '__main__':
    unittest.main()
