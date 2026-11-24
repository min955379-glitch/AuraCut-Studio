import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import captions, peakcut
from app.main import app


def local_temporary_directory():
    # On Windows, test processing must also stay on the local workspace.
    if os.name == 'nt':
        parent = Path(r'C:\AuraCut_Workspace\Output_Renders')
        parent.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(prefix='._peakcut_style_test_', dir=str(parent))
    return tempfile.TemporaryDirectory()


class StyleTests(unittest.TestCase):
    def test_long_word_is_scaled_not_cut_and_no_more_than_two_lines(self):
        words = [{'text': 'ExtremelyLongUnbreakableWord', 'start': 0, 'end': 1},
                 {'text': 'more', 'start': 1, 'end': 2}]
        for size in captions.SIZES:
            text = captions.ass_text(words, size=size, all_caps=False)
            self.assertIn('ExtremelyLongUnbreakableWord', text)
            for line in text.splitlines():
                if line.startswith('Dialogue:'):
                    self.assertLessEqual(line.count(r'\N'), 1)
            long_event = next(line for line in text.splitlines() if line.startswith('Dialogue: 1,') and 'ExtremelyLongUnbreakableWord' in line)
            actual_size = int(re.search(r'\\fs(\d+)', long_event).group(1))
            self.assertLess(actual_size, math.ceil(captions.SIZES[size] * captions.ASS_SIZE_FACTOR))
            self.assertIn(r'\fs%d}' % math.ceil(captions.SIZES[size] * captions.ASS_SIZE_FACTOR), text)

    def test_invalid_request_and_missing_ffmpeg_report_real_error(self):
        with TestClient(app) as client:
            invalid = client.post('/api/captions/preview', json={'preset': 'unknown'})
            self.assertEqual(422, invalid.status_code)
            self.assertEqual('Invalid request', invalid.json()['what_failed'])
            with patch('app.captions.shutil.which', return_value=None):
                missing = client.post('/api/captions/preview', json={})
            self.assertEqual(400, missing.status_code)
            self.assertIn('FFmpeg', missing.json()['real_cause'])

    def test_render_receives_selected_style_size_and_caps(self):
        with local_temporary_directory() as tmp:
            root = Path(tmp)
            for name in ('Input_Media', 'Output_Renders'):
                (root / name).mkdir()
            (root / 'Input_Media' / 'video.mp4').write_bytes(b'source')
            config = {'AURACUT_WORKSPACE': tmp}
            source = {'duration': 20, 'audio': True}
            output = {'duration': 15, 'audio': True, 'streams': [{'codec_type': 'video', 'width': 1080, 'height': 1920}]}
            words = [{'text': 'Hello', 'start': 0, 'end': 1}]
            def extract(_, wav, *args):
                wav.write_bytes(b'wav')
            def encode(args, timeout, on_line, cwd=None):
                self.assertIn(captions.ASS_FILTER, args[args.index('-filter_complex') + 1])
                text = (Path(cwd) / 'captions.ass').read_text(encoding='utf-8')
                self.assertIn('AuraCut Caption,231,', text)
                self.assertIn(r'\3c&H845B2B&', text)
                self.assertIn('Hello', text)
                Path(args[-1]).write_bytes(b'output')
                return True, ''
            with patch('app.storage.workspace', return_value=root), patch('app.peakcut.workspace', return_value=root), patch('app.captions.workspace', return_value=root), patch('app.peakcut.probe', side_effect=[source, output]), patch('app.peakcut.executable', return_value='ffmpeg'), patch('app.peakcut.run_ffmpeg', side_effect=encode), patch('app.peakcut.extract_clip_audio', side_effect=extract), patch('app.captions.engine_status', return_value=(True, '')), patch('app.captions.transcribe_clip', return_value=words):
                result = peakcut.render(config, 'video.mp4', 0, 15, 'full_screen',
                                        {'enabled': True, 'preset': 'steel_blue_glow', 'size': 'extra_large', 'all_caps': False}, cpu_only=True)
            self.assertTrue(result['captions_burned'])

    def test_api_render_passes_options_to_job(self):
        with patch('app.main.settings', return_value={}), patch('app.peakcut.source_file'), patch('app.jobs.submit', return_value='job') as submit:
            with TestClient(app) as client:
                result = client.post('/api/peakcut/render', json={'filename': 'a.mp4', 'start': 0, 'end': 15, 'framing': 'full_screen', 'captions': {'enabled': True, 'preset': 'steel_blue_glow', 'size': 'medium', 'all_caps': False}})
        self.assertEqual(200, result.status_code, result.text)
        options = submit.call_args.args[-1]
        self.assertEqual(('steel_blue_glow', 'medium', False), (options['preset'], options['size'], options['all_caps']))


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is required for a real preview')
class RealPreviewTests(unittest.TestCase):
    def test_real_peakcut_filter_burns_a_short_video(self):
        with local_temporary_directory() as tmp:
            directory = Path(tmp)
            words = [{'text': word, 'start': 0, 'end': 1} for word in ('YOUR', 'CAPTIONS', 'LOOK', 'GREAT')]
            captions.prepare_ass(directory, words, 'steel_blue_glow', 'large', True)
            source, output = directory / 'source.mp4', directory / 'output.mp4'
            subprocess.run([shutil.which('ffmpeg'), '-hide_banner', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=c=0x121316:s=180x320:r=30:d=0.2', '-c:v', 'libx264', '-threads', '1', str(source)],
                           cwd=tmp, capture_output=True, timeout=30, check=True)
            args = peakcut.render_args(source, output, 0, 0.1, 'full_screen', 'libx264', with_captions=True)
            subprocess.run(args, cwd=tmp, capture_output=True, timeout=90, check=True)
            self.assertGreater(output.stat().st_size, 1000)
            decoded = subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-i', str(output), '-frames:v', '1',
                                      '-f', 'null', '-'], cwd=tmp, capture_output=True, timeout=30)
            self.assertEqual(0, decoded.returncode, decoded.stderr.decode(errors='replace'))

    def test_preview_png_and_safe_area_for_all_presets(self):
        with local_temporary_directory() as tmp:
            config = {'AURACUT_WORKSPACE': tmp}
            (Path(tmp) / 'Output_Renders').mkdir()
            with patch('app.main.settings', return_value=config), patch('app.captions.workspace', return_value=Path(tmp)), TestClient(app) as client:
                for preset in captions.STYLES:
                    with self.subTest(preset=preset):
                        response = client.post('/api/captions/preview', json={'preset': preset, 'size': 'large', 'all_caps': True})
                        self.assertEqual(200, response.status_code, response.text if response.status_code != 200 else '')
                        self.assertEqual('image/png', response.headers['content-type'])
                        self.assertEqual(b'\x89PNG\r\n\x1a\n', response.content[:8])
                        self.assertEqual((1080, 1920), struct.unpack('>II', response.content[16:24]))
                        # Decode pixels to confirm that text exists and its visible
                        # effects stay within the required safe area in this sample.
                        result = subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-i', 'pipe:0', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], input=response.content, capture_output=True, timeout=15, check=True)
                        data = result.stdout
                        background = data[:3]
                        coords = [(i // 3 % 1080, i // 3 // 1080) for i in range(0, len(data), 3) if data[i:i+3] != background]
                        self.assertTrue(coords)
                        self.assertGreaterEqual(min(x for x, y in coords), 90)
                        self.assertLess(max(x for x, y in coords), 990)
                        self.assertGreaterEqual(min(y for x, y in coords), 150)
                        self.assertLess(max(y for x, y in coords), 1540)
            self.assertEqual([], list((Path(tmp) / 'Output_Renders').iterdir()))


if __name__ == '__main__':
    unittest.main()
