"""Optional CPU speech captions and local ASS/SRT preparation."""
import html
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from app.common import AppError, ROOT, logger, workspace

FONT_PATH = ROOT / 'app' / 'assets' / 'fonts' / 'AuraCutCaption-Bold.ttf'
INSTALL_TIMEOUT = 1800
SPEECH_TIMEOUT = 1800
_ENGINE_LOCK = threading.Lock()
GOLD_ASS = '&H0055ADD6'
IVORY_ASS = '&H00E5F1F7'
DARK_ASS = '&H00161312'
LANGUAGES = {'auto': None, 'en': 'en', 'ur': 'ur', 'hi': 'hi'}
TEXT_MODES = {'as_spoken': 'transcribe', 'translate': 'translate'}
STYLES = {'bold_gold': (72, 5), 'clean_ivory': (58, 3)}


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


def run_process(args, cwd, timeout, progress, initial_stage, on_line=None, env=None):
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
            tail.append(_safe_error(line.strip())[-1200:])
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
    segments, _ = model.transcribe(str(wav_path), language=LANGUAGES[language], task=TEXT_MODES[text_mode],
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


def transcribe_clip(wav_path, model_dir, model_size, language, text_mode, clip_length, progress=lambda *_: None):
    if model_size not in ('small', 'base') or language not in LANGUAGES or text_mode not in TEXT_MODES:
        raise AppError('Captions options', 'Unsupported speech model, language, or text mode.', 'Choose one of the listed captions options.')
    output = wav_path.parent / 'words.json'
    env = os.environ.copy()
    env.update({'HF_HOME': str(model_dir), 'HF_HUB_CACHE': str(model_dir), 'HF_HUB_DOWNLOAD_TIMEOUT': '120',
                'HF_HUB_ETAG_TIMEOUT': '30', 'HF_HUB_DISABLE_TELEMETRY': '1',
                'HF_HUB_DISABLE_PROGRESS_BARS': '1', 'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1', 'PYTHONIOENCODING': 'utf-8'})
    args = [sys.executable, '-u', '-m', 'app.speech_worker', str(wav_path), str(model_dir), model_size,
            language, text_mode, str(clip_length), str(output)]
    def worker_progress(line, set_stage):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return  # Non-JSON library output is retained for diagnostics on failure.
        if isinstance(event, dict) and isinstance(event.get('stage'), str):
            set_stage(event['stage'])
    ok, detail = run_process(args, ROOT, SPEECH_TIMEOUT, progress, 'Preparing speech model', worker_progress, env)
    if not ok:
        raise AppError('Speech transcription', 'Speech model download or transcription failed: {}'.format(detail),
                       'Check internet access for the first model download, free local disk space and the captions engine, then retry.')
    try:
        words = json.loads(output.read_text(encoding='utf-8'))
        if not isinstance(words, list):
            raise ValueError('word list is not an array')
        return words
    except (OSError, UnicodeError, ValueError) as exc:
        raise AppError('Speech transcription', 'Could not read the word timestamps: {}'.format(exc),
                       'Check local workspace permissions and retry the transcription.') from exc


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


def ass_text(words, style):
    if style not in STYLES:
        raise AppError('Caption style', 'Unknown caption style: {}'.format(style), 'Choose Bold Gold or Clean Ivory.')
    font_size, outline = STYLES[style]
    header = ('[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n'
              '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
              'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
              'Style: Default,AuraCut Caption,{},{},{},{},{},-1,0,0,0,100,100,0,0,1,{},0,2,80,80,420,1\n\n'
              '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n').format(
                  font_size, IVORY_ASS, GOLD_ASS, DARK_ASS, DARK_ASS, outline)
    events = []
    for group in group_words(words):
        def text_for(active):
            parts = []
            longest = max(len(' '.join(w['text'] for w in line)) for line in group['lines'])
            size = max(8, min(font_size, int(font_size * 24 / max(24, longest))))
            for line in group['lines']:
                pieces = []
                for word in line:
                    value = ass_safe(word['text'])
                    if word is active:
                        value = '{\\1c&H55ADD6&}' + value + '{\\1c&HE5F1F7&}'
                    pieces.append(value)
                parts.append(' '.join(pieces))
            return '{\\fs' + str(size) + '}' + r'\N'.join(parts)
        def append_event(start, end, active):
            if end <= start:
                return
            start_cs, end_cs = round(start * 100), round(end * 100)
            if end_cs <= start_cs:
                end_cs = start_cs + 1
            events.append('Dialogue: 0,{},{},Default,,0,0,0,,{}'.format(format_ass_time(start_cs / 100),
                          format_ass_time(end_cs / 100), text_for(active)))
        items = group['words']
        for i, word in enumerate(items):
            next_start = items[i + 1]['start'] if i + 1 < len(items) else word['end']
            end = min(word['end'], next_start) if i + 1 < len(items) else word['end']
            append_event(word['start'], end, word)
            if end < next_start:
                append_event(end, next_start, None)
    return header + '\n'.join(events) + ('\n' if events else '')
