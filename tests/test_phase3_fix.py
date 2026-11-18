import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import captions, jobs, speech_worker, storage
from app.common import AppError, LOG_LINES


class SpeechFixTests(unittest.TestCase):
    def test_saved_model_never_downloads_and_uses_local_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for filename in ('model.bin', 'config.json', 'tokenizer.json'):
                (root / filename).write_bytes(b'fixture')
            class CacheMiss(Exception):
                pass
            download = MagicMock(return_value=str(root))
            model = MagicMock()
            model.transcribe.return_value = (iter([]), None)
            factory = MagicMock(return_value=model)
            fw = types.ModuleType('faster_whisper')
            fw.WhisperModel = factory
            utils = types.ModuleType('faster_whisper.utils')
            utils.download_model = download
            hub = types.ModuleType('huggingface_hub')
            errors = types.ModuleType('huggingface_hub.errors')
            errors.LocalEntryNotFoundError = CacheMiss
            mods = {'faster_whisper': fw, 'faster_whisper.utils': utils,
                    'huggingface_hub': hub, 'huggingface_hub.errors': errors}
            output = root / 'words.json'
            with patch.dict(sys.modules, mods), patch.object(sys, 'argv', ['worker', str(root / 'audio.wav'), str(root), 'small', 'en', 'as_spoken', '2', str(output)]), patch('app.speech_worker.read_clip_audio', return_value='waveform'), patch('app.speech_worker.stage') as stage:
                speech_worker.main()
            download.assert_called_once_with('small', local_files_only=True, cache_dir=str(root))
            factory.assert_called_once_with('small', device='cpu', compute_type='int8', download_root=str(root), local_files_only=True)
            self.assertIn(('Using saved speech model', 'model'), [c.args for c in stage.call_args_list])
            self.assertEqual('waveform', model.transcribe.call_args.args[0])

    def test_failures_are_separate_and_full_traceback_persisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'Output_Renders' / '._peakcut_test'
            directory.mkdir(parents=True)
            wav = directory / 'clip.wav'
            lines = ['Traceback (most recent call last):'] + ['  frame {}'.format(i) for i in range(12)] + ['TypeError: example failure']
            for phase, label in [('download', 'Speech model download'), ('transcribe', 'Speech transcription')]:
                def fail(args, cwd, timeout, progress, initial_stage, on_line, env, stderr_lines):
                    on_line(json.dumps({'phase': phase, 'stage': phase}), lambda _: None)
                    stderr_lines.extend(lines)
                    return False, lines[-1]
                with patch('app.captions.run_process', side_effect=fail), self.assertRaises(AppError) as caught:
                    captions.transcribe_clip(wav, root / 'models', 'small', 'en', 'as_spoken', 2)
                self.assertEqual(label, caught.exception.what_failed)
                self.assertEqual('\n'.join(lines[-10:]), caught.exception.technical_details)
                self.assertIn('TypeError: example failure', caught.exception.real_cause)
            log = (root / 'logs' / 'captions.log').read_text(encoding='utf-8')
            self.assertIn('\n'.join(lines), log)
            self.assertIn('Speech model download', log)
            self.assertIn('Speech transcription', log)
            self.assertTrue(any('Traceback (most recent call last)' in item for item in LOG_LINES))

    def test_real_subprocess_trace_is_not_truncated(self):
        lines = []
        command = [sys.executable, '-c', 'def boom():\n raise TypeError("metadata_errors")\nboom()']
        ok, detail = captions.run_process(command, Path.cwd(), 10, lambda *_: None, 'Testing', stderr_lines=lines)
        self.assertFalse(ok)
        self.assertIn('metadata_errors', detail)
        self.assertIn('Traceback (most recent call last)', '\n'.join(lines))
        self.assertTrue(any('in boom' in line for line in lines))
        self.assertTrue(any('in <module>' in line for line in lines))

    def test_job_preserves_technical_details(self):
        import time
        def fail(progress):
            error = AppError('Speech transcription', 'bad audio', 'retry')
            error.technical_details = 'Traceback tail'
            raise error
        job_id = jobs.submit('Speech', fail)
        for _ in range(100):
            job = jobs.get(job_id)
            if job['status'] == 'failed':
                break
            time.sleep(.01)
        self.assertEqual('Traceback tail', job['error']['technical_details'])

    def test_wav_reader_returns_pcm_array_without_pyav(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest('numpy is part of optional speech engine')
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / 'audio.wav'
            with wave.open(str(wav), 'wb') as out:
                out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                out.writeframes(b'\0\0\xff\x7f')
            values = speech_worker.read_clip_audio(wav)
            self.assertIsInstance(values, np.ndarray)
            self.assertEqual(np.float32, values.dtype)
            self.assertEqual(0, values[0])
            self.assertAlmostEqual(32767 / 32768, values[1])

    def test_silence_generator_and_health_test(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'Output_Renders').mkdir()
            def transcribe(wav, *args):
                with wave.open(str(wav), 'rb') as source:
                    self.assertEqual((1, 2, 16000, 32000), (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getnframes()))
                return []
            with patch('app.captions.workspace', return_value=root), patch('app.captions.engine_status', return_value=(True, '')), patch('app.captions.transcribe_clip', side_effect=transcribe):
                result = captions.test_engine({})
            self.assertIn('PASS', result['message'])


class DriveFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work, self.drive = self.root / 'work', self.root / 'drive'
        (self.work / 'Output_Renders').mkdir(parents=True)
        (self.work / 'Input_Media').mkdir()
        self.drive.mkdir()
        (self.work / 'Output_Renders' / 'sample.mp4').write_bytes(b'1234567890')
        self.config = {'AURACUT_WORKSPACE': str(self.work), 'AURACUT_DRIVE_BACKUP': str(self.drive)}

    def test_poll_growing_copy_and_copy_progress(self):
        target = self.drive / 'sample.mp4'
        real_fsync = os.fsync
        def delayed(fd):
            real_fsync(fd)
            with target.open('r+b') as out:
                out.truncate(3)
        sleeps = []
        def grow(delay):
            sleeps.append(delay)
            target.write_bytes(b'1234567890')
        stages = []
        with patch('app.storage.os.fsync', side_effect=delayed), patch('app.storage.time.sleep', side_effect=grow):
            result = storage.backup_file(self.config, 'Output_Renders', 'sample.mp4', progress=lambda _, stage: stages.append(stage))
        self.assertTrue(result['ok'])
        self.assertEqual([2], sleeps)
        self.assertTrue(any('Copying,' in stage for stage in stages))
        self.assertTrue(any('Waiting for Drive' in stage for stage in stages))

    def test_never_matches_keeps_partial_and_safe_retry(self):
        target = self.drive / 'sample.mp4'
        real_fsync = os.fsync
        def truncate(fd):
            real_fsync(fd)
            with target.open('r+b') as f:
                f.truncate(4)
        with patch('app.storage.os.fsync', side_effect=truncate), patch('app.storage.VERIFY_TIMEOUT', 0):
            with self.assertRaises(AppError) as exc:
                storage.backup_file(self.config, 'Output_Renders', 'sample.mp4')
        self.assertIn('source 10 bytes; copy 4 bytes', exc.exception.real_cause)
        self.assertIn('Wait and click Back up again', exc.exception.fix)
        self.assertEqual(4, target.stat().st_size)
        self.assertTrue(storage.list_files(self.config)[0]['backup_incomplete'])
        self.assertTrue(storage.backup_file(self.config, 'Output_Renders', 'sample.mp4')['ok'])
        self.assertEqual(b'1234567890', target.read_bytes())

    def test_timeout_polls_every_two_seconds_for_ninety_seconds(self):
        target = self.drive / 'sample.mp4'
        real_fsync = os.fsync
        clock = [0.0]
        intervals = []
        def truncate(fd):
            real_fsync(fd)
            with target.open('r+b') as out:
                out.truncate(2)
        def wait(seconds):
            intervals.append(seconds)
            clock[0] += seconds
        with patch('app.storage.os.fsync', side_effect=truncate), patch('app.storage.time.monotonic', side_effect=lambda: clock[0]), patch('app.storage.time.sleep', side_effect=wait):
            with self.assertRaises(AppError):
                storage.backup_file(self.config, 'Output_Renders', 'sample.mp4')
        self.assertEqual([2] * 45, intervals)
        self.assertEqual(2, target.stat().st_size)

    def test_already_and_different_file_never_overwritten(self):
        target = self.drive / 'sample.mp4'
        target.write_bytes(b'1234567890')
        with patch('app.storage.os.fsync', side_effect=AssertionError('must not recopy')):
            self.assertIn('Already backed up', storage.backup_file(self.config, 'Output_Renders', 'sample.mp4')['message'])
        target.write_bytes(b'other')
        with self.assertRaises(AppError):
            storage.backup_file(self.config, 'Output_Renders', 'sample.mp4')
        self.assertEqual(b'other', target.read_bytes())


@unittest.skipUnless(shutil.which('node'), 'Node is only used for this JavaScript regression test')
class PeakUIFixTests(unittest.TestCase):
    def test_failed_analysis_clears_old_results_and_displays_cause(self):
        js = r'''
const fs = require('fs'), vm = require('vm');
const sandbox = {fetch: async ()=>({ok:true,json:async()=>({ok:true})}), setTimeout};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('app/static/app.js','utf8'),sandbox);
(async()=>{
 const app=sandbox.studio(); app.peakResults=[{rank:1}];app.peakAnalyzedFile='old'; app.peakFilename='new';
 app.request=async ()=>({job_id:'job'});
 app.pollPeak=async ()=>{throw {what_failed:'Analysis',real_cause:'Video too short',fix:'Choose a longer video'};};
 await app.findPeaks();
 if(app.peakResults.length || app.peakAnalyzedFile || !app.peakStage.includes('Video too short') || !app.error) process.exit(1);
})().catch(()=>process.exit(2));
'''
        result = subprocess.run(['node', '-e', js], capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)


class RealToneTest(unittest.TestCase):
    def test_real_two_second_silence_when_model_is_cached(self):
        if not shutil.which('ffmpeg'):
            self.skipTest('ffmpeg not installed')
        try:
            from faster_whisper.utils import download_model
            from huggingface_hub.errors import LocalEntryNotFoundError
        except ImportError:
            self.skipTest('optional faster-whisper not installed')
        raw = os.environ.get('AURACUT_TEST_MODELS')
        if not raw:
            self.skipTest('set AURACUT_TEST_MODELS to a local cache containing small; test never downloads')
        try:
            model = Path(download_model('small', local_files_only=True, cache_dir=raw))
        except LocalEntryNotFoundError:
            self.skipTest('small model not cached')
        if not all((model / name).is_file() for name in ('model.bin', 'config.json', 'tokenizer.json')):
            self.skipTest('cached model is incomplete')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wav = root / 'silence.wav'
            subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono', '-t', '2', '-c:a', 'pcm_s16le', str(wav)], check=True, timeout=30)
            self.assertEqual([], captions.transcribe_clip(wav, Path(raw), 'small', 'en', 'as_spoken', 2))


if __name__ == '__main__':
    unittest.main()
