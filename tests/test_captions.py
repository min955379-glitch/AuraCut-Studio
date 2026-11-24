import json
import os
import shutil
import subprocess
import tempfile
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from app import captions, jobs, peakcut, storage
from app.common import AppError
from app.main import app


WORDS = [
    {'text': 'Hello', 'start': 0.20, 'end': 0.80},
    {'text': 'wonderful', 'start': 0.90, 'end': 1.40},
    {'text': 'world', 'start': 1.50, 'end': 2.00},
    {'text': 'this', 'start': 2.10, 'end': 2.50},
    {'text': 'works', 'start': 2.60, 'end': 3.10},
    {'text': 'next', 'start': 3.20, 'end': 3.60},
]


class BuilderTests(unittest.TestCase):
    def test_groups_lines_word_times_colors_and_safe_zone(self):
        groups = captions.group_words(WORDS, max_words=4, max_line_chars=16)
        self.assertTrue(all(len(g['words']) <= 4 for g in groups))
        self.assertTrue(all(len(g['lines']) <= 2 for g in groups))
        self.assertEqual([w['text'] for w in WORDS], [w['text'] for g in groups for w in g['words']])
        for preset in captions.STYLES:
            text = captions.ass_text(WORDS, preset)
            self.assertIn('PlayResX: 1080', text)
            self.assertIn('PlayResY: 1920', text)
            self.assertIn(',90,90,420,1', text)
            self.assertIn('&H0055ADD6', text)
            self.assertIn('&H00E5F1F7', text)
            self.assertIn('&H00161312', text)
            self.assertNotIn('00FF00', text.upper())
            self.assertIn('0:00:00.20,0:00:00.80', text)
            self.assertIn('HELLO', text)
            self.assertIn('Dialogue: 1,0:00:00.80,0:00:00.90', text)
        self.assertIn(r'\fscx115\fscy115', captions.ass_text(WORDS, 'bold_gold_glow'))
        self.assertNotIn(r'\fscx115', captions.ass_text(WORDS, 'clean_ivory_pro'))
        self.assertIn(r'\3c&H845B2B&', captions.ass_text(WORDS, 'steel_blue_glow'))
        self.assertIn(r'\3c&H4E2B10&', captions.ass_text(WORDS, 'bold_gold_glow'))
        for size, expected in captions.SIZES.items():
            self.assertIn('AuraCut Caption,{},'.format(expected), captions.ass_text(WORDS, size=size))
        self.assertIn('HELLO', captions.ass_text(WORDS, all_caps=True))
        self.assertIn('Hello', captions.ass_text(WORDS, all_caps=False))

    def test_srt_numbering_timing_and_escaping(self):
        words = [{'text': '<hi> & -->', 'start': 0, 'end': 1.25}, {'text': 'next\nline', 'start': 2, 'end': 61.9996}]
        result = captions.srt_text(words)
        self.assertIn('1\n00:00:00,000 --> 00:01:02,000', result)
        self.assertIn('&lt;hi&gt; &amp; --&gt;', result)
        self.assertIn('next line', result)
        self.assertEqual('01:01:01,234', captions.format_srt_time(3661.234))
        self.assertEqual('', captions.srt_text([]))
        unsafe = captions.ass_text([{'text': r'{\fnInjected}Hi', 'start': 0, 'end': 1}], 'bold_gold', all_caps=False)
        self.assertNotIn(r'{\fnInjected}', unsafe)
        self.assertIn('｛＼fnInjected｝Hi', unsafe)

    def test_transcription_options_and_clip_relative_timestamps(self):
        word = lambda text, start, end: SimpleNamespace(word=text, start=start, end=end)
        segment = SimpleNamespace(text='hello world', end=2.3, words=[word(' hello', .3, .8), word(' world', 1.1, 2.3)])
        for language in ('auto', 'en', 'ur', 'hi'):
            for text_mode in ('as_spoken', 'translate'):
                model = MagicMock()
                model.transcribe.return_value = (iter([segment]), None)
                result = captions.transcribe_words(model, 'clip.wav', 5, language, text_mode)
                self.assertEqual(.3, result[0]['start'])
                self.assertEqual(2.3, result[1]['end'])
                self.assertEqual(captions.LANGUAGES[language], model.transcribe.call_args.kwargs['language'])
                self.assertEqual(captions.TEXT_MODES[text_mode], model.transcribe.call_args.kwargs['task'])
                self.assertTrue(model.transcribe.call_args.kwargs['word_timestamps'])
                self.assertTrue(model.transcribe.call_args.kwargs['vad_filter'])
        model = MagicMock()
        model.transcribe.return_value = (iter([]), None)
        self.assertEqual([], captions.transcribe_words(model, 'clip.wav', 5, 'auto', 'as_spoken'))
        model.transcribe.return_value = (iter([SimpleNamespace(text='speech', words=None)]), None)
        with self.assertRaisesRegex(ValueError, 'no word timestamps'):
            captions.transcribe_words(model, 'clip.wav', 5, 'auto', 'as_spoken')

    def test_filter_uses_bare_names_not_windows_paths(self):
        with patch('app.peakcut.executable', return_value='ffmpeg'):
            args = peakcut.render_args(r'C:\AuraCut_Workspace\Input_Media\test.mp4', r'C:\AuraCut_Workspace\Output_Renders\temp.mp4', 5, 20, 'whole_blur', 'h264_amf', with_captions=True)
        filt = args[args.index('-filter_complex') + 1]
        self.assertIn('ass=filename=captions.ass:fontsdir=fonts', filt)
        self.assertNotIn('C:', filt)
        self.assertNotIn('\\', filt)
        self.assertIn('[captioned]', args)

    def test_engine_is_optional_and_no_torch_dependency_declared(self):
        root = Path(__file__).resolve().parents[1]
        core = (root / 'requirements.txt').read_text().lower()
        extra = (root / 'requirements-captions.txt').read_text().lower()
        self.assertNotIn('faster-whisper', core)
        self.assertIn('faster-whisper', extra)
        self.assertNotIn('torch', extra)


class CaptionRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        drive = Path(self.tmp.name) / 'drive'
        drive.mkdir()
        self.config = {'AURACUT_WORKSPACE': str(self.root), 'AURACUT_DRIVE_BACKUP': str(drive)}
        for folder in ('Input_Media', 'Output_Renders'):
            (self.root / folder).mkdir()
        (self.root / 'Input_Media' / 'video.mp4').write_bytes(b'source')
        for target in ('app.storage.workspace', 'app.peakcut.workspace', 'app.captions.workspace'):
            patcher = patch(target, return_value=self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.output_info = {'duration': 15.0, 'audio': True, 'streams': [{'codec_type': 'video', 'width': 1080, 'height': 1920}]}

    def fake_encode(self, args, timeout, on_line, **kwargs):
        Path(args[-1]).write_bytes(b'encoded')
        return True, ''

    def test_missing_engine_renders_without_captions_and_warns(self):
        source_info = {'duration': 30.0, 'audio': True}
        with patch('app.peakcut.probe', side_effect=[source_info, self.output_info]), patch('app.peakcut.executable', return_value='ffmpeg'), patch('app.peakcut.run_ffmpeg', side_effect=self.fake_encode), patch('app.captions.engine_status', return_value=(False, 'missing faster_whisper')):
            result = peakcut.render(self.config, 'video.mp4', 0, 15, 'full_screen', {'enabled': True}, cpu_only=True)
        self.assertFalse(result['captions_burned'])
        self.assertIsNone(result['srt_filename'])
        self.assertIn('Rendered without captions', result['warning'])
        self.assertIn('missing faster_whisper', result['warning'])
        self.assertTrue((self.root / 'Output_Renders' / result['filename']).exists())

    def test_no_audio_renders_without_srt(self):
        source_info = {'duration': 30.0, 'audio': False}
        info = dict(self.output_info, audio=False)
        with patch('app.peakcut.probe', side_effect=[source_info, info]), patch('app.peakcut.executable', return_value='ffmpeg'), patch('app.peakcut.run_ffmpeg', side_effect=self.fake_encode), patch('app.captions.transcribe_clip') as transcribe:
            result = peakcut.render(self.config, 'video.mp4', 0, 15, 'full_screen', {'enabled': True}, cpu_only=True)
        transcribe.assert_not_called()
        self.assertIn('No speech found, rendered without captions', result['caption_message'])
        self.assertIsNone(result['srt_filename'])
        self.assertEqual([], list((self.root / 'Output_Renders').glob('*.srt')))

    def test_transcribed_speech_burns_once_with_relative_paths_and_backup(self):
        source_info = {'duration': 30.0, 'audio': True}
        calls = []
        def encode(args, timeout, on_line, **kwargs):
            directory = Path(kwargs['cwd'])
            self.assertTrue((directory / 'captions.ass').exists())
            self.assertTrue((directory / 'fonts' / 'AuraCutCaption-Bold.ttf').exists())
            filt = args[args.index('-filter_complex') + 1]
            self.assertIn('ass=filename=captions.ass:fontsdir=fonts', filt)
            self.assertNotIn(str(self.root), filt)
            calls.append(args[args.index('-c:v') + 1])
            Path(args[-1]).write_bytes(b'encoded')
            return (False, 'GPU failure') if calls[-1] == 'h264_amf' else (True, '')
        def extract(source, wav, start, length, progress):
            self.assertEqual(10, start)
            wav.write_bytes(b'wav')
        with patch('app.peakcut.probe', side_effect=[source_info, self.output_info]), patch('app.peakcut.executable', return_value='ffmpeg'), patch('app.peakcut.run_ffmpeg', side_effect=encode), patch('app.peakcut.extract_clip_audio', side_effect=extract), patch('app.captions.engine_status', return_value=(True, '')), patch('app.captions.transcribe_clip', return_value=WORDS) as transcribe:
            result = peakcut.render(self.config, 'video.mp4', 10, 25, 'whole_blur', {'enabled': True, 'model_size': 'base', 'language': 'ur', 'text_mode': 'translate', 'style': 'clean_ivory'})
        self.assertEqual(['h264_amf', 'libx264'], calls)
        self.assertTrue(result['captions_burned'])
        self.assertIn('used CPU', result['warning'])
        self.assertEqual(('base', 'ur', 'translate', 15), transcribe.call_args.args[2:6])
        srt_path = self.root / 'Output_Renders' / result['srt_filename']
        self.assertIn('00:00:00,200', srt_path.read_text(encoding='utf-8-sig'))
        self.assertEqual(Path(result['filename']).stem, Path(result['srt_filename']).stem)
        self.assertTrue(storage.backup_file(self.config, 'Output_Renders', result['srt_filename'])['ok'])
        self.assertTrue(storage.backup_file(self.config, 'Output_Renders', result['filename'])['ok'])
        self.assertFalse(any(p.name.startswith('._peakcut_') for p in (self.root / 'Output_Renders').iterdir()))

    def test_no_speech_with_engine_does_not_create_srt(self):
        source_info = {'duration': 30.0, 'audio': True}
        def extract(source, wav, start, length, progress):
            wav.write_bytes(b'wav')
        with patch('app.peakcut.probe', side_effect=[source_info, self.output_info]), patch('app.peakcut.executable', return_value='ffmpeg'), patch('app.peakcut.run_ffmpeg', side_effect=self.fake_encode), patch('app.peakcut.extract_clip_audio', side_effect=extract), patch('app.captions.engine_status', return_value=(True, '')), patch('app.captions.transcribe_clip', return_value=[]):
            result = peakcut.render(self.config, 'video.mp4', 0, 15, 'full_screen', {'enabled': True}, cpu_only=True)
        self.assertFalse(result['captions_burned'])
        self.assertIn('No speech found', result['caption_message'])
        self.assertIsNone(result['srt_filename'])
        self.assertEqual([], list((self.root / 'Output_Renders').glob('*.srt')))

    def test_pair_never_overwrites_a_preexisting_srt(self):
        folder = self.root / 'Output_Renders'
        original = folder / 'example.srt'
        original.write_text('keep me')
        video_temp, srt_temp = folder / '._peakcut_video.mp4', folder / '._peakcut_sub.srt'
        video_temp.write_bytes(b'video')
        srt_temp.write_bytes(b'subtitles')
        video, srt = peakcut.publish_pair(video_temp, srt_temp, folder, 'example')
        self.assertEqual('keep me', original.read_text())
        self.assertEqual('example_1.mp4', video.name)
        self.assertEqual('example_1.srt', srt.name)
        self.assertFalse((folder / 'example.mp4').exists())


class EngineTests(unittest.TestCase):
    def test_install_job_status_and_real_error(self):
        with patch('app.captions.run_process', return_value=(False, 'ERROR: No matching distribution found')):
            with self.assertRaises(AppError) as exc:
                captions.install_engine()
        self.assertIn('No matching distribution found', exc.exception.real_cause)
        self.assertTrue(exc.exception.fix)
        with patch('app.captions.run_process', return_value=(True, '')), patch('app.captions.engine_status', return_value=(True, '')):
            result = captions.install_engine()
        self.assertIn('installed and ready', result['message'])

    def test_worker_cache_download_and_cpu_configuration(self):
        from app import speech_worker
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_path = root / 'model'
            model_path.mkdir()
            for name in ('model.bin', 'config.json', 'tokenizer.json'):
                (model_path / name).write_text('fixture')
            wav = root / 'clip.wav'
            wav.write_bytes(b'fixture')
            output = root / 'words.json'
            class CacheMiss(Exception):
                pass
            calls = []
            def download(size, **kwargs):
                calls.append((size, kwargs))
                if kwargs['local_files_only'] and len(calls) == 1:
                    raise CacheMiss('not in cache')
                return str(model_path)
            model = MagicMock()
            model.transcribe.return_value = (iter([]), None)
            factory = MagicMock(return_value=model)
            fake_fw = types.ModuleType('faster_whisper')
            fake_fw.WhisperModel = factory
            fake_utils = types.ModuleType('faster_whisper.utils')
            fake_utils.download_model = download
            fake_hub = types.ModuleType('huggingface_hub')
            fake_errors = types.ModuleType('huggingface_hub.errors')
            fake_errors.LocalEntryNotFoundError = CacheMiss
            modules = {'faster_whisper': fake_fw, 'faster_whisper.utils': fake_utils,
                       'huggingface_hub': fake_hub, 'huggingface_hub.errors': fake_errors}
            with patch.dict(sys.modules, modules), patch.object(sys, 'argv', ['worker', str(wav), str(root), 'small', 'ur', 'translate', '15', str(output)]), patch('app.speech_worker.stage') as stage, patch('app.speech_worker.read_clip_audio', return_value='audio'):
                speech_worker.main()
            self.assertEqual([True], [call[1]['local_files_only'] for call in calls])
            self.assertEqual(str(root), calls[0][1]['cache_dir'])
            self.assertEqual(('small',), factory.call_args.args)
            self.assertEqual(str(root), factory.call_args.kwargs['download_root'])
            self.assertNotIn('local_files_only', factory.call_args.kwargs)
            self.assertEqual('cpu', factory.call_args.kwargs['device'])
            self.assertEqual('int8', factory.call_args.kwargs['compute_type'])
            self.assertEqual('translate', model.transcribe.call_args.kwargs['task'])
            self.assertEqual('ur', model.transcribe.call_args.kwargs['language'])
            self.assertEqual([], json.loads(output.read_text()))
            self.assertTrue(any('about 500 MB' in call.args[0] for call in stage.call_args_list))

    def test_audio_extract_is_limited_to_selected_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'clip.wav'
            def execute(args, timeout, on_line):
                self.assertEqual('10.000', args[args.index('-ss') + 1])
                self.assertEqual('15.000', args[args.index('-t') + 1])
                wav.write_bytes(b'pcm')
                return True, ''
            with patch('app.peakcut.executable', return_value='ffmpeg'), patch('app.peakcut.run_ffmpeg', side_effect=execute):
                peakcut.extract_clip_audio(Path('source.mp4'), wav, 10, 15, lambda *_: None)

    def test_background_install_route(self):
        with patch('app.jobs.submit', return_value='install-job') as submit:
            with TestClient(app) as client:
                response = client.post('/api/captions/install')
        self.assertEqual('install-job', response.json()['job_id'])
        self.assertIs(captions.install_engine, submit.call_args.args[1])


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg and ffprobe are required for the real subtitle test')
class RealAssTests(unittest.TestCase):
    def test_real_five_second_ass_burn(self):
        if os.name == 'nt':
            parent = Path(r'C:\AuraCut_Workspace\Output_Renders')
            parent.mkdir(parents=True, exist_ok=True)
            directory = tempfile.TemporaryDirectory(prefix='._peakcut_test_', dir=str(parent))
        else:
            directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        work = Path(directory.name)
        (work / 'fonts').mkdir()
        shutil.copy2(captions.FONT_PATH, work / 'fonts' / 'AuraCutCaption-Bold.ttf')
        (work / 'captions.ass').write_text(captions.ass_text(WORDS[:3], 'bold_gold'), encoding='utf-8')
        source, target = work / 'source.mp4', work / 'burned.mp4'
        subprocess.run([shutil.which('ffmpeg'), '-hide_banner', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'testsrc2=size=320x180:rate=30', '-t', '5', '-c:v', 'libx264', '-preset', 'ultrafast', str(source)],
                       check=True, capture_output=True, timeout=90)
        render = subprocess.run([shutil.which('ffmpeg'), '-hide_banner', '-v', 'info', '-y', '-i', str(source), '-vf',
                                 'ass=filename=captions.ass:fontsdir=fonts', '-t', '5', '-c:v', 'libx264', '-preset', 'ultrafast', str(target)],
                                cwd=str(work), capture_output=True, text=True, timeout=90)
        self.assertEqual(0, render.returncode, render.stderr)
        self.assertIn('AuraCutCaption-Bold', render.stderr)
        info = peakcut.probe(target)
        self.assertAlmostEqual(5, info['duration'], delta=.2)
        self.assertGreater(target.stat().st_size, 0)


if __name__ == '__main__':
    unittest.main()
