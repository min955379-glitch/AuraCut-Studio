"""Optional CPU speech captions and local ASS/SRT preparation."""
import html
import json
import math
import os
import re
import shutil
import struct
from functools import lru_cache
import subprocess
import sys
import threading
import traceback
import unicodedata
import wave
import tempfile
import time
from collections import deque
from pathlib import Path

from app.common import AppError, ROOT, logger, workspace

FONT_PATH = ROOT / 'app' / 'assets' / 'fonts' / 'AuraCutCaption-Bold.ttf'
INSTALL_TIMEOUT = 1800
SPEECH_TIMEOUT = 1800
_ENGINE_LOCK = threading.Lock()
GOLD_ASS = '&H0055ADD6&'
IVORY_ASS = '&H00E5F1F7&'
DARK_ASS = '&H00161312&'
NAVY_ASS = '&H004E2B10&'
STEEL_ASS = '&H00845B2B&'
LANGUAGES = {'auto': None, 'en': 'en', 'ur': 'ur', 'hi': 'hi'}
TEXT_MODES = {'as_spoken': 'transcribe', 'translate': 'translate'}
STYLES = {
    'bold_gold_glow': {'outline': 4.5, 'shadow': 2, 'outline_color': DARK_ASS, 'glow': NAVY_ASS, 'pop': True},
    'clean_ivory_pro': {'outline': 3.5, 'shadow': 1, 'outline_color': DARK_ASS, 'glow': None, 'pop': False},
    'steel_blue_glow': {'outline': 4.5, 'shadow': 2, 'outline_color': DARK_ASS, 'glow': STEEL_ASS, 'pop': False},
}
SIZES = {'medium': 72, 'large': 88, 'extra_large': 104}
# Preserve requests from older clients, while presenting only the new presets.
LEGACY_STYLES = {'bold_gold': 'bold_gold_glow', 'clean_ivory': 'clean_ivory_pro'}
ASS_FILTER = 'ass=filename=captions.ass:fontsdir=fonts'


def engine_status():
    """Probe a fresh interpreter so optional dependencies are never imported at startup."""
    try:
        result = subprocess.run([sys.executable, '-c', 'from faster_whisper import WhisperModel'],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, '{}: {}'.format(type(exc).__name__, exc)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return False, detail[-1] if detail else 'Import exited with code {}'.format(result.returncode)
    return True, ''


def _safe_error(text):
    # Pip may report an authenticated index URL; never put its credentials in the log or UI.
    return re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[redacted]@', text)


def run_process(args, cwd, timeout, progress, initial_stage, on_line=None, env=None, stderr_lines=None):
    """Run a bounded background subprocess, showing an elapsed-time heartbeat."""
    try:
        process = subprocess.Popen(args, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding='utf-8', errors='replace', bufsize=1)
    except OSError as exc:
        raise AppError('Captions process', 'Could not start {}: {}'.format(args[0], exc),
                       'Check the Python environment and local folder permissions, then retry.') from exc
    tail = deque(maxlen=30)
    stdout_tail = deque(maxlen=20)
    stage = [initial_stage]
    reader_errors = []
    done = threading.Event()
    lock = threading.Lock()
    started = time.monotonic()
    def set_stage(value):
        with lock:
            stage[0] = value
        progress(None, value)
    def read_stdout():
        try:
            for line in process.stdout:
                text = _safe_error(line.strip())
                if text:
                    stdout_tail.append(text[-1200:])
                    if on_line:
                        on_line(text, set_stage)
        except Exception as exc:
            reader_errors.append(exc)
            process.kill()
    def read_stderr():
        for line in process.stderr:
            safe_line = _safe_error(line.rstrip('\r\n'))
            tail.append(safe_line[-1200:])
            if stderr_lines is not None:
                stderr_lines.append(safe_line)
    def heartbeat():
        while not done.wait(10):
            with lock:
                current = stage[0]
            progress(None, '{} ({} seconds elapsed)'.format(current, int(time.monotonic() - started)))
    threads = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=read_stderr, daemon=True),
               threading.Thread(target=heartbeat, daemon=True)]
    set_stage(initial_stage)
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
    finally:
        done.set()
        if process.poll() is None:
            process.kill()
            process.wait()
        for thread in threads:
            thread.join(timeout=5)
        process.stdout.close()
        process.stderr.close()
    if reader_errors:
        raise AppError('Captions process', 'Could not read subprocess progress: {}'.format(reader_errors[0]),
                       'Check the local Python environment, then retry.')
    if timed_out:
        return False, 'Timed out after {} seconds.'.format(timeout)
    if process.returncode:
        lines = [line for line in list(tail) + list(stdout_tail) if line]
        errors = [line for line in lines if re.search(r'error|failed|exception|denied|no matching|not found|timeout', line, re.I)]
        return False, (errors[-1] if errors else lines[-1] if lines else 'Process exited with code {}'.format(process.returncode))[-1200:]
    return True, ''


def install_engine(progress=lambda *_: None):
    if not _ENGINE_LOCK.acquire(blocking=False):
        raise AppError('Captions engine install', 'An installation is already running.', 'Wait for it to finish, then refresh health.')
    try:
        def pip_progress(line, set_stage):
            if line.startswith(('Collecting ', 'Downloading ')):
                set_stage('Installing captions engine: downloading packages')
            elif line.startswith(('Installing collected packages', 'Successfully installed')):
                set_stage('Installing captions engine: installing packages')
        command = [sys.executable, '-u', '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input',
                   '--timeout', '60', '--retries', '1', '-r', str(ROOT / 'requirements-captions.txt')]
        ok, detail = run_process(command, ROOT, INSTALL_TIMEOUT, progress, 'Installing captions engine; this may take several minutes', pip_progress)
        if not ok:
            raise AppError('Captions engine install', 'pip failed: {}'.format(detail),
                           'Check internet access, free disk space and the Python environment, then retry installation.')
        progress(None, 'Checking the installed captions engine')
        installed, reason = engine_status()
        if not installed:
            raise AppError('Captions engine install', 'Installed packages cannot be imported: {}'.format(reason),
                           'Check the Python environment and retry installation.')
        logger.info('Captions engine installed and import verified.')
        return {'message': 'Captions engine installed and ready.'}
    finally:
        _ENGINE_LOCK.release()


def model_folder(config):
    folder = workspace(config) / 'models'
    if folder.is_symlink() or (hasattr(folder, 'is_junction') and folder.is_junction()):
        raise AppError('Speech model', 'The local models folder is a link or junction: {}'.format(folder),
                       'Use a real local models folder, not a Google Drive link or junction.')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def clean_word(text):
    return ' '.join(str(text).split())


def transcribe_words(model, wav_path, clip_length, language, text_mode, on_segment=lambda *_: None):
    """Whisper timestamps are relative to the extracted clip, never the source video."""
    if language not in LANGUAGES or text_mode not in TEXT_MODES:
        raise AppError('Captions options', 'Unsupported spoken language or text mode.', 'Choose one of the listed captions options.')
    segments, _ = model.transcribe(wav_path, language=LANGUAGES[language], task=TEXT_MODES[text_mode],
                                   word_timestamps=True, vad_filter=True)
    words = []
    for segment in segments:
        found = getattr(segment, 'words', None)
        if not found and str(getattr(segment, 'text', '')).strip():
            raise ValueError('Speech was detected but the speech engine returned no word timestamps.')
        for item in found or []:
            text = clean_word(item.word)
            start, end = float(item.start), float(item.end)
            if not math.isfinite(start) or not math.isfinite(end):
                raise ValueError('The speech engine returned non-finite word timestamps.')
            start, end = max(0.0, start), min(float(clip_length), end)
            if text and end > start:
                words.append({'text': text, 'start': start, 'end': end})
        on_segment(min(float(clip_length), float(getattr(segment, 'end', 0))))
    words.sort(key=lambda word: (word['start'], word['end']))
    return words


def _transcribe_clip(wav_path, model_dir, model_size, language, text_mode, clip_length, progress=lambda *_: None):
    if model_size not in ('small', 'base') or language not in LANGUAGES or text_mode not in TEXT_MODES:
        raise AppError('Captions options', 'Unsupported speech model, language, or text mode.', 'Choose one of the listed captions options.')
    output = wav_path.parent / 'words.json'
    env = os.environ.copy()
    env.update({'HF_HOME': str(model_dir), 'HF_HUB_CACHE': str(model_dir), 'HF_HUB_DOWNLOAD_TIMEOUT': '120',
                'HF_HUB_ETAG_TIMEOUT': '30', 'HF_HUB_DISABLE_TELEMETRY': '1',
                'HF_HUB_DISABLE_PROGRESS_BARS': '1', 'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1', 'PYTHONIOENCODING': 'utf-8'})
    args = [sys.executable, '-u', '-m', 'app.speech_worker', str(wav_path), str(model_dir), model_size,
            language, text_mode, str(clip_length), str(output)]
    phase = ['model']
    stderr_lines = []
    def worker_progress(line, set_stage):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return  # Non-JSON library output is retained for diagnostics on failure.
        if isinstance(event, dict) and isinstance(event.get('stage'), str):
            if event.get('phase') in ('model', 'download', 'transcribe'):
                phase[0] = event['phase']
            logger.info('Captions: %s', event['stage'])
            set_stage(event['stage'])
    ok, detail = run_process(args, ROOT, SPEECH_TIMEOUT, progress, 'Preparing speech model', worker_progress, env, stderr_lines)
    if not ok:
        what = {'model': 'Speech model loading', 'download': 'Speech model download', 'transcribe': 'Speech transcription'}[phase[0]]
        lines = stderr_lines or [detail]
        trace = '\n'.join(lines)
        # Keep the complete subprocess traceback on local disk, as well as in the app log.
        logger.error('%s traceback:\n%s', what, trace)
        try:
            log_dir = wav_path.parent.parent.parent / 'logs'
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / 'captions.log').open('a', encoding='utf-8') as log:
                log.write('\n{}\n{}\n'.format(what, trace))
        except OSError as exc:
            detail += '; Could not write captions.log: {}'.format(exc)
        fix = ('Check internet access and local disk space, then retry.' if phase[0] == 'download' else
               'Check the technical details, local disk space and captions engine, then retry.')
        err = AppError(what, detail, fix)
        err.technical_details = '\n'.join(lines[-10:])
        raise err
    try:
        words = json.loads(output.read_text(encoding='utf-8'))
        if not isinstance(words, list):
            raise ValueError('word list is not an array')
        return words
    except (OSError, UnicodeError, ValueError) as exc:
        raise AppError('Speech transcription', 'Could not read the word timestamps: {}'.format(exc),
                       'Check local workspace permissions and retry the transcription.') from exc


def transcribe_clip(wav_path, model_dir, model_size, language, text_mode, clip_length, progress=lambda *_: None):
    try:
        return _transcribe_clip(wav_path, model_dir, model_size, language, text_mode, clip_length, progress)
    except Exception as exc:
        if getattr(exc, 'technical_details', None):
            raise
        trace = _safe_error(traceback.format_exc())
        logger.error('Speech transcription traceback:\n%s', trace)
        try:
            log_dir = wav_path.parent.parent.parent / 'logs'
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / 'captions.log').open('a', encoding='utf-8') as log:
                log.write('\nSpeech transcription\n{}\n'.format(trace))
        except OSError as log_error:
            trace += '\nCould not write captions.log: {}'.format(log_error)
            logger.error('Could not write captions.log: %s | Fix: Check workspace permissions.', log_error)
        if not isinstance(exc, AppError):
            exc = AppError('Speech transcription', '{}: {}'.format(type(exc).__name__, exc),
                           'Check technical details and the local captions engine, then retry.')
        exc.technical_details = '\n'.join(trace.splitlines()[-10:])
        raise exc


def test_engine(config, progress=lambda *_: None):
    installed, reason = engine_status()
    if not installed:
        raise AppError('Captions engine test', reason, 'Click Install captions engine in PeakCut, then retry.')
    folder = workspace(config) / 'Output_Renders'
    with tempfile.TemporaryDirectory(prefix='._peakcut_test_', dir=str(folder)) as temporary:
        wav_path = Path(temporary) / 'silence.wav'
        with wave.open(str(wav_path), 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\0\0' * 32000)
        words = transcribe_clip(wav_path, model_folder(config), 'small', 'en', 'as_spoken', 2, progress)
        if words:
            raise AppError('Captions engine test', 'The engine detected speech in generated silence: {}'.format(words),
                           'Check the captions engine installation and retry.')
    return {'message': 'PASS: model loaded and 2 seconds of silence transcribed; no speech found.'}


def group_words(words, max_words=5, max_line_chars=24):
    groups, group, lines = [], [], []
    for word in words:
        text = clean_word(word['text'])
        if not text:
            continue
        line = lines[-1] if lines else []
        length = sum(len(item['text']) for item in line) + max(0, len(line) - 1)
        if group and (len(group) >= max_words or (len(lines) == 2 and line and length + 1 + len(text) > max_line_chars)):
            groups.append({'words': group, 'lines': lines})
            group, lines, line = [], [], []
        if not lines or (line and length + 1 + len(text) > max_line_chars):
            lines.append([])
        item = {'text': text, 'start': float(word['start']), 'end': float(word['end'])}
        group.append(item)
        lines[-1].append(item)
    if group:
        groups.append({'words': group, 'lines': lines})
    return groups


def format_srt_time(seconds):
    total = max(0, round(seconds * 1000))
    hours, total = divmod(total, 3600000)
    minutes, total = divmod(total, 60000)
    seconds, millis = divmod(total, 1000)
    return '{:02d}:{:02d}:{:02d},{:03d}'.format(hours, minutes, seconds, millis)


def format_ass_time(seconds):
    total = max(0, round(seconds * 100))
    hours, total = divmod(total, 360000)
    minutes, total = divmod(total, 6000)
    seconds, centis = divmod(total, 100)
    return '{}:{:02d}:{:02d}.{:02d}'.format(hours, minutes, seconds, centis)


def srt_text(words):
    result = []
    for index, group in enumerate(group_words(words), 1):
        lines = [' '.join(html.escape(word['text'], quote=False) for word in line) for line in group['lines']]
        result.append('{}\n{} --> {}\n{}'.format(index, format_srt_time(group['words'][0]['start']),
                      format_srt_time(group['words'][-1]['end']), '\n'.join(lines)))
    return '\n\n'.join(result) + ('\n' if result else '')


def ass_safe(text):
    # ASS override characters must never be interpreted as formatting commands.
    return text.replace('\\', '＼').replace('{', '｛').replace('}', '｝')


# ASS font size is based on the font's full ascender-to-descender box, not
# its capital height. This bundled font's 714-unit cap height occupies only
# 714/2112 of that box. Calibrate ASS sizes to the requested visible sizes.
ASS_SIZE_FACTOR = 2.22
SAFE_LINE_WIDTH = 864  # 900 px between margins, minus room for outlines/glow
# Retain the original 115% pop, including in two-line blocks.
TWO_LINE_POP_Y = 115
MIN_CLEAR_GAP = 12


@lru_cache(maxsize=1)
def _font_metrics():
    """Read advances from the bundled TrueType font using the standard library."""
    try:
        data = FONT_PATH.read_bytes()
        u16 = lambda pos: struct.unpack_from('>H', data, pos)[0]
        s16 = lambda pos: struct.unpack_from('>h', data, pos)[0]
        u32 = lambda pos: struct.unpack_from('>I', data, pos)[0]
        tables = {}
        for i in range(u16(4)):
            pos = 12 + i * 16
            tables[data[pos:pos + 4]] = u32(pos + 8)
        hhea, hmtx, cmap = tables[b'hhea'], tables[b'hmtx'], tables[b'cmap']
        descent = -s16(hhea + 6)
        full_height = s16(hhea + 4) + descent
        head, loca, glyf = tables[b'head'], tables[b'loca'], tables[b'glyf']
        long_loca = s16(head + 50) == 1
        num_hmetrics = u16(hhea + 34)
        fmt4 = None
        for i in range(u16(cmap + 2)):
            sub = cmap + u32(cmap + 4 + i * 8 + 4)
            if u16(sub) == 4:
                fmt4 = sub
                break
        if not fmt4 or full_height <= 0 or num_hmetrics <= 0:
            raise ValueError('Unsupported bundled font metrics')
        count = u16(fmt4 + 6) // 2
        ends = fmt4 + 14
        starts = ends + count * 2 + 2
        deltas = starts + count * 2
        ranges = deltas + count * 2
        advances, ink_bounds = {}, {}
        # Cmap format 4 covers the bundled font's supported Unicode BMP glyphs.
        for i in range(count):
            first, last = u16(starts + i * 2), min(u16(ends + i * 2), 0xfffe)
            delta, offset = s16(deltas + i * 2), u16(ranges + i * 2)
            for code in range(first, last + 1):
                if offset:
                    address = ranges + i * 2 + offset + 2 * (code - first)
                    glyph = u16(address)
                    glyph = (glyph + delta) & 0xffff if glyph else 0
                else:
                    glyph = (code + delta) & 0xffff
                if glyph:
                    advances[code] = u16(hmtx + 4 * min(glyph, num_hmetrics - 1))
                    start = u32(loca + 4 * glyph) if long_loca else u16(loca + 2 * glyph) * 2
                    end = u32(loca + 4 * (glyph + 1)) if long_loca else u16(loca + 2 * (glyph + 1)) * 2
                    if start != end:
                        ink_bounds[code] = (s16(glyf + start + 4), s16(glyf + start + 8))
        return full_height, advances, descent, ink_bounds
    except (OSError, KeyError, ValueError, struct.error) as exc:
        raise AppError('Caption font', 'Could not read the bundled font metrics: {}'.format(exc),
                       'Restore the bundled caption font and restart AuraCut.') from exc


def _line_width(words, font_size, pop):
    full_height, advances, _, _ = _font_metrics()
    # Unknown glyphs may use fallback fonts: reserve two ems per character.
    # Non-Latin shaping can alter advances, so reserve extra space as well.
    def width(text):
        return sum(advances.get(ord(char), 2000) for char in ass_safe(text))
    sizes = [width(w['text']) for w in words]
    space = advances.get(ord(' '), 500)
    units = sum(sizes) + max(0, len(sizes) - 1) * space
    if pop and sizes:
        units += 0.15 * max(sizes)
    non_latin = any(ord(c) > 127 for w in words for c in w['text'])
    return units * font_size / full_height * (1.20 if non_latin else 1.025)


def caption_groups(words, font_size, pop):
    """Break groups on estimated glyph width, never by cutting a word."""
    groups, group, lines = [], [], []
    for word in words:
        text = clean_word(word['text'])
        if not text:
            continue
        item = {'text': text, 'start': float(word['start']), 'end': float(word['end'])}
        too_wide = _line_width([item], font_size, pop) > SAFE_LINE_WIDTH
        if group and (len(group) >= 4 or too_wide):
            groups.append({'words': group, 'lines': lines})
            group, lines = [], []
        if not lines:
            lines.append([])
        if lines[-1] and _line_width(lines[-1] + [item], font_size, pop) > SAFE_LINE_WIDTH:
            if len(lines) == 2:
                groups.append({'words': group, 'lines': lines})
                group, lines = [], [[]]
            else:
                lines.append([])
        group.append(item)
        lines[-1].append(item)
        if too_wide:
            groups.append({'words': group, 'lines': lines})
            group, lines = [], []
    if group:
        groups.append({'words': group, 'lines': lines})
    return groups


def _line_extents(line):
    """Conservative glyph ink bounds relative to the baseline, in font units."""
    _, _, _, bounds = _font_metrics()
    chars = [char for word in line for char in ass_safe(word['text'])]
    values = [bounds[ord(char)] for char in chars if ord(char) in bounds]
    if not values:
        # Unsupported glyphs may come from a fallback font.
        return -616, 1488
    low, high = min(value[0] for value in values), max(value[1] for value in values)
    if any(ord(char) > 127 for char in chars):
        # Shaping can reposition marks or substitute glyphs not in cmap.
        # Reserve a small additional font-unit allowance for shaped text.
        low, high = low - 100, high + 100
        if any(ord(char) not in bounds and not char.isspace() and unicodedata.category(char) not in ('Cf', 'Mn', 'Me') for char in chars):
            low, high = min(low, -616), max(high, 1488)
    return low, high


def _line_spacing(lines, font_size, style, active):
    """Reserve clearance for ink, active-word pop, outline and glow."""
    if len(lines) < 2:
        return 0
    full_height, _, descent, _ = _font_metrics()
    def scale(word):
        return TWO_LINE_POP_Y / 100 if style['pop'] and word is active else 1.0
    upper = min(scale(word) * (descent + _line_extents([word])[0]) for word in lines[0])
    lower = max(scale(word) * (descent + _line_extents([word])[1]) for word in lines[1])
    # Move the top line only when necessary. The active word's enlarged ink
    # and each layer's entire glow need their own clearance. The lower line
    # stays anchored so it cannot be pushed into the bottom safe zone.
    halo = 19 if style['glow'] else 7
    ink_distance = font_size / full_height * (lower - upper)
    return math.ceil(max(0, ink_distance) + 2 * halo + MIN_CLEAR_GAP + 2)


def ass_text(words, preset='bold_gold_glow', size='large', all_caps=True):
    preset = LEGACY_STYLES.get(preset, preset)
    if preset not in STYLES or size not in SIZES or not isinstance(all_caps, bool):
        raise AppError('Caption style', 'Unsupported preset, size, or all-caps setting.',
                       'Choose one of the listed caption presets and sizes.')
    style, display_size = STYLES[preset], SIZES[size]
    font_size = math.ceil(display_size * ASS_SIZE_FACTOR)
    header = ('[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n'
              '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
              'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
              'Style: Default,AuraCut Caption,{},{},{},{},{},-1,0,0,0,100,100,0,0,1,{},{},2,90,90,420,1\n\n'
              '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n').format(
                  font_size, IVORY_ASS, GOLD_ASS, style['outline_color'], NAVY_ASS, style['outline'], style['shadow'])
    display_words = [{**w, 'text': str(w['text']).upper() if all_caps else str(w['text'])} for w in words]
    events = []
    for group in caption_groups(display_words, font_size, style['pop']):
        width = max(_line_width(line, font_size, style['pop']) for line in group['lines'])
        effective_size = min(font_size, math.floor(font_size * SAFE_LINE_WIDTH / width)) if width else font_size
        if effective_size < 1:
            raise AppError('Caption layout', 'A word is too long to fit even at the smallest caption size.',
                           'Shorten that word and retry; no letters were cut.')
        def text_for(line, active, y):
            pieces = []
            for word in line:
                value = ass_safe(word['text'])
                if word is active:
                    vertical_pop = TWO_LINE_POP_Y if len(group['lines']) == 2 else 115
                    pop = r'\fscx115\fscy' + str(vertical_pop) if style['pop'] else ''
                    reset = r'\fscx100\fscy100' if style['pop'] else ''
                    value = r'{\1c&H55ADD6&' + pop + '}' + value + r'{\1c&HE5F1F7&' + reset + '}'
                pieces.append(value)
            return r'{\an2\pos(540,' + str(y) + r')\fs' + str(effective_size) + '}' + ' '.join(pieces)
        def append_event(start, end, active):
            if end <= start:
                return
            start_cs, end_cs = round(start * 100), round(end * 100)
            if end_cs <= start_cs:
                end_cs = start_cs + 1
            times = '{},{}'.format(format_ass_time(start_cs / 100), format_ass_time(end_cs / 100))
            spacing = _line_spacing(group['lines'], effective_size, style, active)
            for i, line in enumerate(group['lines']):
                y = 1530 - (len(group['lines']) - 1 - i) * spacing
                text = text_for(line, active, y)
                if style['glow']:
                    glow_rgb = style['glow'][4:]
                    events.append('Dialogue: 0,{},Default,,0,0,0,,{{\\1a&HFF&\\3c&H{}\\bord9\\blur5\\shad0}}{}'.format(times, glow_rgb, text))
                events.append('Dialogue: 1,{},Default,,0,0,0,,{}'.format(times, text))
        items = group['words']
        for i, word in enumerate(items):
            next_start = items[i + 1]['start'] if i + 1 < len(items) else word['end']
            end = min(word['end'], next_start) if i + 1 < len(items) else word['end']
            append_event(word['start'], end, word)
            if end < next_start:
                append_event(end, next_start, None)
    return header + '\n'.join(events) + ('\n' if events else '')


def prepare_ass(directory, words, preset, size, all_caps):
    """Use identical relative names and bundled font for previews and renders."""
    (directory / 'captions.ass').write_text(ass_text(words, preset, size, all_caps), encoding='utf-8')
    (directory / 'fonts').mkdir()
    shutil.copy2(FONT_PATH, directory / 'fonts' / 'AuraCutCaption-Bold.ttf')


def preview_png(config, preset, size, all_caps):
    # Validate before starting FFmpeg. All temporary files stay in the local workspace.
    sample = [{'text': word, 'start': i * .1, 'end': (i + 1) * .1}
              for i, word in enumerate(('Your', 'captions', 'look', 'great'))]
    ass_text(sample, preset, size, all_caps)
    # Show the first active word on line 2, including for Extra Large where
    # the first group wraps differently. The same sample and builder are used.
    display = [{**w, 'text': w['text'].upper() if all_caps else w['text']} for w in sample]
    style = STYLES[LEGACY_STYLES.get(preset, preset)]
    groups = caption_groups(display, math.ceil(SIZES[size] * ASS_SIZE_FACTOR), style['pop'])
    preview_group = next((group for group in groups if len(group['lines']) == 2), None)
    if preview_group is None:
        raise AppError('Caption preview', 'The sample could not be arranged on two lines.',
                       'Check the bundled caption font and try again.')
    lower_word = preview_group['lines'][1][0]['text']
    active_index = next(i for i, w in enumerate(display) if w['text'] == lower_word)
    preview_time = active_index * .1
    executable = shutil.which('ffmpeg')
    if not executable:
        raise AppError('Caption preview', 'FFmpeg was not found on PATH.',
                       'Install FFmpeg with the ASS filter and restart AuraCut.')
    folder = workspace(config) / 'Output_Renders'
    try:
        with tempfile.TemporaryDirectory(prefix='._peakcut_preview_', dir=str(folder)) as tmp:
            directory = Path(tmp)
            prepare_ass(directory, sample, preset, size, all_caps)
            command = [executable, '-hide_banner', '-v', 'error', '-f', 'lavfi', '-i',
                       'color=c=0x121316:s=1080x1920:r=10:d=1', '-vf', ASS_FILTER,
                       '-ss', '{:.1f}'.format(preview_time), '-frames:v', '1', '-c:v', 'png', '-threads', '1', '-compression_level', '1',
                       '-f', 'image2pipe', 'pipe:1']
            result = subprocess.run(command, cwd=str(directory), capture_output=True, timeout=15)
            if result.returncode or not result.stdout.startswith(b'\x89PNG\r\n\x1a\n'):
                detail = result.stderr.decode('utf-8', errors='replace').strip() or 'FFmpeg did not return a PNG image.'
                raise AppError('Caption preview', detail[-1200:],
                               'Check that FFmpeg supports the ASS filter and PNG encoder, then retry.')
            return result.stdout
    except subprocess.TimeoutExpired as exc:
        raise AppError('Caption preview', 'FFmpeg timed out after 15 seconds.',
                       'Check the FFmpeg installation and try again.') from exc
    except OSError as exc:
        raise AppError('Caption preview', 'Could not create preview: {}'.format(exc),
                       'Check FFmpeg and local workspace permissions, then retry.') from exc
