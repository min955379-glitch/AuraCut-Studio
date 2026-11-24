"""Pixel-level checks of real ASS frames and the PeakCut FFmpeg burn path."""
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app import captions, peakcut
from app.common import workspace
from test_caption_size_real import (WIDTH, HEIGHT, MIN_CAPS, ass_frames, color_bands,
                                    decode, full_bounds, local_root)


def visible_bands(data, threshold=0):
    """Include visible outline and glow, not only the ivory/gold letter fills."""
    background = data[:3]
    rows, gold_rows = set(), set()
    for i in range(0, len(data), 3):
        r, g, b = data[i:i + 3]
        y = i // (3 * WIDTH)
        if max(abs(r - background[0]), abs(g - background[1]), abs(b - background[2])) > threshold:
            rows.add(y)
        if r >= 165 and g >= 120 and 40 <= b <= 160 and r > g + 10 and g > b + 20:
            gold_rows.add(y)
    bands = []
    for y in sorted(rows):
        if not bands or y - bands[-1][-1] > 1:
            bands.append([y])
        else:
            bands[-1].append(y)
    return [(band[0], band[-1], len(gold_rows.intersection(band))) for band in bands]


@unittest.skipUnless(shutil.which('ffmpeg'), 'Real FFmpeg with libass is required')
class CaptionGapTests(unittest.TestCase):
    def setUp(self):
        self.config, tmp = local_root()
        if tmp:
            self.addCleanup(tmp.cleanup)
        self.output = workspace(self.config) / 'Output_Renders'

    def assert_clear(self, data, size, active_line, check_cap_height=True, max_gap=45, threshold=0):
        bands = visible_bands(data, threshold)
        self.assertEqual(2, len(bands), bands)
        gap = bands[1][0] - bands[0][1] - 1
        self.assertGreaterEqual(gap, 12, bands)
        # A modest upper bound catches layout changes that create a disconnected block.
        self.assertLessEqual(gap, max_gap, bands)
        self.assertGreater(bands[active_line][2], 0, bands)
        if check_cap_height:
            fills = color_bands(data)
            self.assertEqual(2, len(fills), fills)
            self.assertGreaterEqual(max(row['ivory_height'] for row in fills), MIN_CAPS[size], fills)
        left, right, top, bottom = full_bounds(data)
        self.assertGreaterEqual(left, 90)
        self.assertLess(right, WIDTH - 90)
        self.assertGreaterEqual(top, 150)
        self.assertLess(bottom, HEIGHT - 380)
        return gap

    def test_all_presets_sizes_and_active_line_positions(self):
        words = [{'text': w, 'start': i, 'end': i + 1}
                 for i, w in enumerate(('YOUR', 'CAPTIONS', 'LOOK', 'GREAT'))]
        for preset in captions.STYLES:
            for size in captions.SIZES:
                with self.subTest(preset=preset, size=size):
                    groups = captions.caption_groups(words, math.ceil(captions.SIZES[size] * captions.ASS_SIZE_FACTOR), captions.STYLES[preset]['pop'])
                    self.assertEqual(2, len(groups[0]['lines']))
                    lower = groups[0]['lines'][1][0]['text']
                    lower_index = next(i for i, w in enumerate(words) if w['text'] == lower)
                    with tempfile.TemporaryDirectory(prefix='._peakcut_gap_', dir=str(self.output)) as tmp:
                        frames = ass_frames(Path(tmp), words, preset=preset, size=size, count=4)
                        self.assert_clear(frames[0], size, 0)
                        self.assert_clear(frames[lower_index], size, 1)

    def test_descenders_and_shaped_scripts_in_both_line_positions(self):
        samples = [
            ('big', 'pig', 'jumping', 'quietly'),
            ('नमस्ते', 'दोस्तों', 'आप', 'कैसे'),
            ('سلام', 'دنیا', 'آپ', 'کیسے'),
        ]
        for sample in samples:
            words = [{'text': w, 'start': i, 'end': i + 1} for i, w in enumerate(sample)]
            for size in captions.SIZES:
                with self.subTest(sample=sample, size=size):
                    groups = captions.caption_groups(words, math.ceil(captions.SIZES[size] * captions.ASS_SIZE_FACTOR), True)
                    with tempfile.TemporaryDirectory(prefix='._peakcut_gap_', dir=str(self.output)) as tmp:
                        frames = ass_frames(Path(tmp), words, size=size, count=len(words), all_caps=False)
                        for group in groups:
                            if len(group['lines']) == 2:
                                for line_index in (0, 1):
                                    text = group['lines'][line_index][0]['text']
                                    index = next(i for i, w in enumerate(words) if w['text'] == text)
                                    self.assert_clear(frames[index], size, line_index, check_cap_height=False, max_gap=max(70, math.ceil(.85 * captions.SIZES[size])))

    def test_all_previews_show_a_gold_word_on_line_two(self):
        for preset in captions.STYLES:
            for size in captions.SIZES:
                for caps in (True, False):
                    with self.subTest(preset=preset, size=size, caps=caps):
                        frame = decode(captions.preview_png(self.config, preset, size, caps))
                        # ALL CAPS off is included for preview placement and gap;
                        # lowercase letter height is not a capital-height measure.
                        bands = visible_bands(frame)
                        self.assertEqual(2, len(bands), bands)
                        self.assertGreater(bands[1][2], 0, bands)
                        self.assertEqual(bands[0][2], 0, bands)
                        self.assertGreaterEqual(bands[1][0] - bands[0][1] - 1, 12, bands)

    def test_real_peakcut_video_with_lower_active_word(self):
        words = [{'text': word, 'start': i * .1, 'end': (i + 1) * .1}
                 for i, word in enumerate(('AND', 'THE', 'LITTLE', 'PUT'))]
        with tempfile.TemporaryDirectory(prefix='._peakcut_gap_', dir=str(self.output)) as tmp:
            root = Path(tmp)
            subdir = root / 'subs'
            subdir.mkdir()
            captions.prepare_ass(subdir, words, 'bold_gold_glow', 'large', True)
            source, output = root / 'source.mp4', root / 'result.mp4'
            subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                            'color=c=0x121316:s=1080x1920:r=30:d=0.5', '-c:v', 'libx264',
                            '-threads', '1', '-pix_fmt', 'yuv420p', str(source)],
                           capture_output=True, timeout=60, check=True)
            args = peakcut.render_args(source, output, 0, .4, 'full_screen', 'libx264', with_captions=True)
            ok, detail = peakcut.run_ffmpeg(args, 90, lambda _: None, cwd=subdir)
            self.assertTrue(ok, detail)
            frame = subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-i', str(output), '-ss', '0.3',
                                    '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                                   capture_output=True, timeout=30, check=True).stdout
            self.assert_clear(frame, 'large', 1, threshold=4)
