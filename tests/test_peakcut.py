import math
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import jobs, peakcut, storage
from app.common import AppError
from app.main import app


class WindowTests(unittest.TestCase):
    def test_top_windows_are_ranked_and_non_overlapping(self):
        powers = [0.01] * 15 + [0.8] * 15 + [0.1] * 15
        windows = peakcut.pick_windows(45, 15, 3, powers)
        self.assertEqual(3, len(windows))
        self.assertEqual([1, 2, 3], [item['rank'] for item in windows])
        self.assertEqual(15, windows[0]['start'])
        self.assertGreater(windows[0]['score'], windows[1]['score'])
        ordered = sorted(windows, key=lambda item: item['start'])
        self.assertTrue(all(ordered[i]['end'] <= ordered[i + 1]['start'] for i in range(len(ordered) - 1)))

    def test_edge_lengths_and_too_many_requested(self):
        result = peakcut.pick_windows(15, 15, 5, [0.1] * 15)
        self.assertEqual(1, len(result))
        self.assertEqual((0, 15), (result[0]['start'], result[0]['end']))
        with self.assertRaises(AppError) as exc:
            peakcut.pick_windows(2, 15, 1)
        self.assertIn('at least 15 seconds', exc.exception.real_cause)
        result = peakcut.pick_windows(20, 30, 3)
        self.assertEqual([(0, 20)], [(w['start'], w['end']) for w in result])
        self.assertIsNone(result[0]['score'])
        result = peakcut.pick_windows(70, 20, 5)
        self.assertEqual(3, len(result))
        self.assertEqual([0, 25, 50], [w['start'] for w in result])


class PeakTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {'AURACUT_WORKSPACE': str(self.root)}
        (self.root / 'Input_Media').mkdir()
        (self.root / 'Output_Renders').mkdir()
        (self.root / 'Input_Media' / 'source.mp4').write_bytes(b'input')
        for name in ('app.storage.workspace', 'app.peakcut.workspace'):
            patcher = patch(name, return_value=self.root)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_safety_rejects_filename_path_tricks_and_temp_outputs(self):
        for name in ('../source.mp4', '..\\source.mp4', 'C:\\test.mp4', 'CON.mp4'):
            with self.assertRaises(AppError, msg=name):
                peakcut.source_file(self.config, name)
        temp = self.root / 'Output_Renders' / '._peakcut_pending.mp4'
        temp.write_bytes(b'not finished')
        with self.assertRaises(AppError):
            storage.safe_file(self.config, 'Output_Renders', temp.name)
        self.assertFalse(any(f['name'] == temp.name for f in storage.list_files(self.config)))

    def test_no_audio_falls_back_to_even_windows(self):
        with patch('app.peakcut.probe', return_value={'duration': 70, 'audio': False}), patch('app.peakcut.run_ffmpeg') as ffmpeg:
            result = peakcut.analyze(self.config, 'source.mp4', 20, 3)
        ffmpeg.assert_not_called()
        self.assertIn('No audio found, using evenly spaced moments', result['message'])
        self.assertEqual([0, 25, 50], [window['start'] for window in result['windows']])
        self.assertTrue(all(window['score'] is None for window in result['windows']))

    def test_filter_args_each_mode_are_lists(self):
        with patch('app.peakcut.executable', return_value='ffmpeg'):
            whole = peakcut.render_args('input.mp4', 'output.mp4', 1, 16, 'whole_blur', 'h264_amf')
            zoom = peakcut.render_args('input.mp4', 'output.mp4', 1, 16, 'zoom_blur', 'h264_amf')
            full = peakcut.render_args('input.mp4', 'output.mp4', 1, 16, 'full_screen', 'h264_amf')
        self.assertIsInstance(whole, list)
        f1 = whole[whole.index('-filter_complex') + 1]
        f2 = zoom[zoom.index('-filter_complex') + 1]
        f3 = full[full.index('-filter_complex') + 1]
        self.assertIn('boxblur', f1)
        self.assertIn('force_original_aspect_ratio=decrease', f1)
        self.assertIn('boxblur', f2)
        self.assertIn('1.35', f2)
        self.assertIn('crop=1080:1920', f3)
        self.assertNotIn('boxblur', f3)
        for args in (whole, zoom, full):
            self.assertIn('h264_amf', args)
            self.assertIn('aac', args)
            self.assertIn('30', args)

    def test_gpu_fails_then_cpu_succeeds_without_overwriting(self):
        probe = {'duration': 30.0, 'audio': True, 'streams': [{'codec_type': 'video', 'width': 1080, 'height': 1920}]}
        output_probe = dict(probe, duration=15.0)
        calls = []
        def fake_ffmpeg(args, timeout, callback):
            encoder = args[args.index('-c:v') + 1]
            calls.append(encoder)
            Path(args[-1]).write_bytes(b'encoded')
            return (False, 'AMF device unavailable') if encoder == 'h264_amf' else (True, '')
        existing = self.root / 'Output_Renders' / 'peakcut_source_0.00-15.00_whole_blur.mp4'
        existing.write_bytes(b'do not overwrite')
        with patch('app.peakcut.probe', side_effect=[probe, output_probe]), patch('app.peakcut.executable', return_value='ffmpeg'), patch('app.peakcut.run_ffmpeg', side_effect=fake_ffmpeg):
            result = peakcut.render(self.config, 'source.mp4', 0, 15, 'whole_blur')
        self.assertEqual(['h264_amf', 'libx264'], calls)
        self.assertEqual('libx264', result['encoder'])
        self.assertIn('GPU encode failed, used CPU', result['warning'])
        self.assertIn('AMF device unavailable', result['warning'])
        self.assertEqual(b'do not overwrite', existing.read_bytes())
        self.assertTrue(result['filename'].endswith('_1.mp4'))
        self.assertEqual(b'encoded', (existing.parent / result['filename']).read_bytes())
        self.assertFalse(any(p.name.startswith('._peakcut_') for p in existing.parent.iterdir()))

    def test_failure_reason_prefers_root_cause_over_generic_thread_error(self):
        lines = ['[AMF] DLL libamf failed to open', '[encoder] Task finished with error code -22',
                 '[encoder] Terminating thread with return code -22 (Invalid argument)']
        self.assertEqual(lines[0], peakcut.last_error(lines, 'no details'))

    def test_rejects_incorrect_render_duration_and_cleans_up(self):
        source_info = {'duration': 30, 'audio': True}
        output_info = {'duration': 3, 'audio': True, 'streams': [{'codec_type': 'video', 'width': 1080, 'height': 1920}]}
        def fake_ffmpeg(args, timeout, callback):
            Path(args[-1]).write_bytes(b'incomplete')
            return True, ''
        with patch('app.peakcut.probe', side_effect=[source_info, output_info]), patch('app.peakcut.executable', return_value='ffmpeg'), patch('app.peakcut.run_ffmpeg', side_effect=fake_ffmpeg) as ffmpeg:
            with self.assertRaises(AppError) as exc:
                peakcut.render(self.config, 'source.mp4', 0, 15, 'full_screen')
        self.assertIn('Expected 15.00 seconds', exc.exception.real_cause)
        self.assertEqual(1, ffmpeg.call_count)
        self.assertFalse(list((self.root / 'Output_Renders').iterdir()))

    def test_both_encoders_fail_returns_both_causes_and_cleans_temp(self):
        with patch('app.peakcut.probe', return_value={'duration': 30, 'audio': True}), patch('app.peakcut.executable', return_value='ffmpeg'), patch('app.peakcut.run_ffmpeg', side_effect=[(False, 'GPU failure'), (False, 'CPU failure')]):
            with self.assertRaises(AppError) as exc:
                peakcut.render(self.config, 'source.mp4', 0, 15, 'full_screen')
        self.assertIn('GPU failure', exc.exception.real_cause)
        self.assertIn('CPU failure', exc.exception.real_cause)
        self.assertFalse(list((self.root / 'Output_Renders').iterdir()))

    def test_api_rejects_bad_paths_and_invalid_params(self):
        with patch('app.main.settings', return_value=self.config):
            with TestClient(app) as client:
                bad = client.post('/api/peakcut/analyze', json={'filename': '../source.mp4'})
                self.assertEqual(400, bad.status_code)
                self.assertTrue(all(k in bad.json() for k in ('what_failed', 'real_cause', 'fix')))
                self.assertEqual(422, client.post('/api/peakcut/analyze', json={'filename': 'source.mp4', 'clip_seconds': 14}).status_code)
                self.assertEqual(422, client.post('/api/peakcut/analyze', json={'filename': 'source.mp4', 'count': 6}).status_code)
                self.assertEqual(400, client.post('/api/peakcut/render', json={'filename': 'source.mp4', 'start': 0, 'end': 15, 'framing': 'bad'}).status_code)
                self.assertEqual(404, client.get('/api/jobs/not-a-job').status_code)

    def test_short_video_job_returns_structured_error(self):
        class ImmediateExecutor:
            def submit(self, fn):
                fn()
        with patch('app.main.settings', return_value=self.config), patch('app.jobs._executor', ImmediateExecutor()), patch('app.peakcut.probe', return_value={'duration': 2, 'audio': False}):
            with TestClient(app) as client:
                started = client.post('/api/peakcut/analyze', json={'filename': 'source.mp4'}).json()
                job = client.get('/api/jobs/' + started['job_id']).json()
        self.assertEqual('failed', job['status'])
        self.assertIn('at least 15 seconds', job['error']['real_cause'])
        self.assertTrue(job['error']['what_failed'])
        self.assertTrue(job['error']['fix'])

    def test_api_enqueues_background_jobs(self):
        with patch('app.main.settings', return_value=self.config), patch('app.jobs.submit', return_value='test-job') as submit:
            with TestClient(app) as client:
                result = client.post('/api/peakcut/analyze', json={'filename': 'source.mp4'})
                self.assertEqual('test-job', result.json()['job_id'])
                self.assertEqual(30, submit.call_args.args[4])
                self.assertEqual(3, submit.call_args.args[5])
                result = client.post('/api/peakcut/render', json={'filename': 'source.mp4', 'start': 0, 'end': 15, 'framing': 'whole_blur'})
                self.assertEqual('test-job', result.json()['job_id'])


class JobTests(unittest.TestCase):
    def test_job_states_and_error_structure(self):
        class ImmediateExecutor:
            def submit(self, fn):
                fn()
        def work(progress):
            progress(22, 'Working')
            return {'value': 5}
        def fail(progress):
            raise AppError('Analysis', 'Bad video', 'Choose a good video.')
        with patch.object(jobs, '_executor', ImmediateExecutor()):
            job = jobs.get(jobs.submit('Analysis', work))
            self.assertEqual('done', job['status'])
            self.assertEqual(100, job['percent'])
            self.assertEqual(5, job['result']['value'])
            failed = jobs.get(jobs.submit('Analysis', fail))
            self.assertEqual('failed', failed['status'])
            self.assertEqual('Bad video', failed['error']['real_cause'])
            self.assertTrue(failed['error']['fix'])


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg and ffprobe are required for the real media test')
class RealMediaTests(unittest.TestCase):
    def test_real_audio_analysis_and_cpu_render(self):
        # On Windows, use the required local workspace. Elsewhere use a temporary test workspace.
        temporary = None
        if os.name == 'nt':
            root = Path(r'C:\AuraCut_Workspace')
            (root / 'Input_Media').mkdir(parents=True, exist_ok=True)
            (root / 'Output_Renders').mkdir(parents=True, exist_ok=True)
        else:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name)
            (root / 'Input_Media').mkdir()
            (root / 'Output_Renders').mkdir()
        fd, name = tempfile.mkstemp(prefix='peakcut_test_', suffix='.mp4', dir=str(root / 'Input_Media'))
        os.close(fd)
        source = Path(name)
        self.addCleanup(lambda: source.unlink(missing_ok=True))
        subprocess.run([shutil.which('ffmpeg'), '-hide_banner', '-v', 'error', '-y',
                        '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=30',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
                        '-t', '20', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', str(source)],
                       check=True, capture_output=True, timeout=120)
        config = {'AURACUT_WORKSPACE': str(root)}
        analysis = peakcut.analyze(config, source.name, 15, 3)
        self.assertEqual(1, len(analysis['windows']))
        self.assertIsNotNone(analysis['windows'][0]['score'])
        moment = analysis['windows'][0]
        result = peakcut.render(config, source.name, moment['start'], moment['end'], 'full_screen', cpu_only=True)
        output = root / 'Output_Renders' / result['filename']
        self.addCleanup(lambda: output.unlink(missing_ok=True))
        info = peakcut.probe(output)
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        self.assertEqual((1080, 1920), (video['width'], video['height']))
        self.assertTrue(info['audio'])
        self.assertAlmostEqual(15, info['duration'], delta=0.5)


if __name__ == '__main__':
    unittest.main()
