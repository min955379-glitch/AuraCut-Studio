"""Pixel measurements against real FFmpeg/libass, never mocked."""
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from app import captions, peakcut
from app.common import workspace

WIDTH, HEIGHT = 1080, 1920
FRAME_BYTES = WIDTH * HEIGHT * 3
MIN_CAPS = {'medium': 52, 'large': 64, 'extra_large': 76}
SAMPLE = [{'text': word, 'start': i * .4, 'end': (i + 1) * .4}
          for i, word in enumerate(('Your', 'captions', 'look', 'great'))]


def local_root():
    if os.name == 'nt':
        return {'AURACUT_WORKSPACE': r'C:\AuraCut_Workspace'}, None
    tmp = tempfile.TemporaryDirectory(prefix='auracut_style_')
    return {'AURACUT_WORKSPACE': tmp.name}, tmp


def decode(image):
    return subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-i', 'pipe:0', '-frames:v', '1',
                           '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], input=image, capture_output=True,
                          timeout=30, check=True).stdout


def color_bands(data):
    """Find solid ivory/gold pixels, excluding dark outline, glow and shadow."""
    rows = {}
    ivory_rows = set()
    for i in range(0, len(data), 3):
        r, g, b = data[i:i + 3]
        ivory = r >= 205 and g >= 200 and b >= 185
        gold = r >= 165 and g >= 120 and 40 <= b <= 160 and r > g + 10 and g > b + 20
        if ivory or gold:
            x, y = (i // 3) % WIDTH, (i // 3) // WIDTH
            rows.setdefault(y, []).append(x)
            if ivory:
                ivory_rows.add(y)
    bands = []
    for y in sorted(rows):
        if not bands or y - bands[-1][-1] > 3:
            bands.append([y])
        else:
            bands[-1].append(y)
    result = []
    for band in bands:
        xs = [x for y in band for x in rows[y]]
        whites = [y for y in band if y in ivory_rows]
        result.append({'left': min(xs), 'right': max(xs), 'top': band[0], 'bottom': band[-1],
                       'width': max(xs) - min(xs) + 1,
                       'ivory_height': max(whites) - min(whites) + 1 if whites else 0})
    return result


def full_bounds(data):
    background = data[:3]
    left, right, top, bottom = WIDTH, -1, HEIGHT, -1
    for i in range(0, len(data), 3):
        if data[i:i + 3] != background:
            x, y = (i // 3) % WIDTH, (i // 3) // WIDTH
            left, right, top, bottom = min(left, x), max(right, x), min(top, y), max(bottom, y)
    return left, right, top, bottom


def ass_frames(directory, words, preset='bold_gold_glow', size='extra_large', count=1, all_caps=True):
    captions.prepare_ass(directory, words, preset, size, all_caps)
    command = [shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
               'color=c=0x121316:s=1080x1920:r=1:d={}'.format(count), '-vf', captions.ASS_FILTER,
               '-frames:v', str(count), '-pix_fmt', 'rgb24', '-threads', '1', '-f', 'rawvideo', 'pipe:1']
    raw = subprocess.run(command, cwd=str(directory), capture_output=True, timeout=30, check=True).stdout
    assert len(raw) == count * FRAME_BYTES
    return [raw[i:i + FRAME_BYTES] for i in range(0, len(raw), FRAME_BYTES)]


@unittest.skipUnless(shutil.which('ffmpeg'), 'Real FFmpeg with libass is required')
class CaptionPixelTests(unittest.TestCase):
    def setUp(self):
        self.config, tmp = local_root()
        if tmp:
            self.addCleanup(tmp.cleanup)
        self.output = workspace(self.config) / 'Output_Renders'

    def check_metrics(self, data, size, preset):
        self.assertEqual(FRAME_BYTES, len(data))
        bands = color_bands(data)
        self.assertEqual(2, len(bands), bands)
        self.assertGreaterEqual(max(band['ivory_height'] for band in bands), MIN_CAPS[size], bands)
        self.assertGreaterEqual(bands[0]['top'], 150)
        self.assertLessEqual(bands[-1]['bottom'], HEIGHT - 380)
        for band in bands:
            self.assertGreater(band['width'], 0)
            self.assertGreaterEqual(band['left'], 90)
            self.assertLess(band['right'], WIDTH - 90)
        self.assertLessEqual(bands[1]['top'] - bands[0]['bottom'] - 1, round(.35 * captions.SIZES[size]))
        if size == 'large':
            width = max(b['width'] for b in bands)
            self.assertGreaterEqual(width, .55 * WIDTH, bands)
            self.assertLessEqual(width, .80 * WIDTH, bands)
        return bands

    def test_nine_real_previews_measure_letter_height_and_width(self):
        for preset in captions.STYLES:
            for size in captions.SIZES:
                with self.subTest(preset=preset, size=size):
                    image = captions.preview_png(self.config, preset, size, True)
                    self.assertEqual((WIDTH, HEIGHT), struct.unpack('>II', image[16:24]))
                    self.check_metrics(decode(image), size, preset)

    def test_real_peakcut_burn_video_matches_preview_in_all_sizes(self):
        with tempfile.TemporaryDirectory(prefix='._peakcut_size_', dir=str(self.output)) as tmp:
            root = Path(tmp)
            source = root / 'source.mp4'
            subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                            'color=c=0x121316:s=1080x1920:r=30:d=0.2', '-c:v', 'libx264',
                            '-threads', '1', '-pix_fmt', 'yuv420p', str(source)],
                           capture_output=True, timeout=60, check=True)
            for size in captions.SIZES:
                with self.subTest(size=size):
                    subdir = root / size
                    subdir.mkdir()
                    captions.prepare_ass(subdir, SAMPLE, 'bold_gold_glow', size, True)
                    output = root / (size + '.mp4')
                    args = peakcut.render_args(source, output, 0, .1, 'full_screen', 'libx264', with_captions=True)
                    ok, detail = peakcut.run_ffmpeg(args, 90, lambda line: None, cwd=subdir)
                    self.assertTrue(ok, detail)
                    frame = subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-i', str(output), '-frames:v', '1',
                                            '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                                           capture_output=True, timeout=30, check=True).stdout
                    video_bands = self.check_metrics(frame, size, 'bold_gold_glow')
                    preview_bands = color_bands(decode(captions.preview_png(self.config, 'bold_gold_glow', size, True)))
                    self.assertLessEqual(abs(max(b['ivory_height'] for b in video_bands) - max(b['ivory_height'] for b in preview_bands)), 2)
                    self.assertLessEqual(abs(max(b['width'] for b in video_bands) - max(b['width'] for b in preview_bands)), 4)

    def test_long_word_and_hindi_urdu_stay_inside_safe_margins(self):
        samples = [
            ['SUPERCALIFRAGILISTICEXPIALIDOCIOUS'],
            ['W' * 80],
            ['YOUR', 'CAPTIONS', 'LOOK', 'GREAT'],
            ['नमस्ते', 'दोस्तों', 'आप', 'कैसे'],
            ['سلام', 'دنیا', 'آپ', 'کیسے'],
        ]
        for sample in samples:
            words = [{'text': w, 'start': i, 'end': i + 1} for i, w in enumerate(sample)]
            for size in captions.SIZES:
                with self.subTest(sample=sample, size=size), tempfile.TemporaryDirectory(prefix='._peakcut_size_', dir=str(self.output)) as tmp:
                    for frame in ass_frames(Path(tmp), words, size=size, count=len(words)):
                        left, right, top, bottom = full_bounds(frame)
                        self.assertLess(left, right, 'Expected visible text')
                        self.assertGreaterEqual(left, 90)
                        self.assertLess(right, WIDTH - 90)
                        self.assertGreaterEqual(top, 150)
                        self.assertLess(bottom, HEIGHT - 380)

    def test_impossibly_long_word_reports_a_clear_error(self):
        word = [{'text': 'W' * 4000, 'start': 0, 'end': 1}]
        with self.assertRaises(captions.AppError) as error:
            captions.ass_text(word)
        self.assertTrue(error.exception.what_failed)
        self.assertTrue(error.exception.real_cause)
        self.assertTrue(error.exception.fix)
