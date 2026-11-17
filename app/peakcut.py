"""Find loud moments and render local vertical video without loading media into memory."""
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections import deque
from pathlib import Path

from app import captions
from app.common import AppError, logger, workspace
from app.storage import safe_file

FRAMINGS = ('whole_blur', 'zoom_blur', 'full_screen')
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.mpeg', '.mpg', '.m2ts', '.mts', '.3gp', '.wmv', '.flv', '.ts'}
PROBE_TIMEOUT = 30
ANALYZE_TIMEOUT = 7200
RENDER_TIMEOUT = 7200
TEMP_PREFIX = '._peakcut_'


def executable(name):
    path = shutil.which(name)
    if not path:
        raise AppError('PeakCut', '{} was not found on PATH.'.format(name),
                       'Install FFmpeg and add its bin folder to PATH, then restart AuraCut.')
    return str(Path(path).resolve())


def source_file(config, filename):
    path = safe_file(config, 'Input_Media', filename)
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise AppError('PeakCut video', 'The selected file is not a supported video: {}'.format(filename),
                       'Choose a video file from Input_Media.')
    return path


def last_error(lines, fallback):
    lines = [line.strip() for line in lines if line.strip()]
    candidates = [line for line in lines if re.search(r'error|failed|invalid|unsupported|not found|permission denied|no such|cannot|could not', line, re.I)]
    # FFmpeg often ends with generic thread failures; prefer the earlier underlying cause.
    specific = [line for line in candidates if not re.search(r'conversion failed|terminating thread|task finished with error|error while (?:filtering|processing|opening encoder)', line, re.I)]
    return (specific[0] if specific else candidates[0] if candidates else lines[-1] if lines else fallback)[-600:]


def probe(path):
    try:
        proc = subprocess.run([executable('ffprobe'), '-v', 'error', '-show_entries',
                               'format=duration:stream=codec_type,width,height', '-of', 'json', str(path)],
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise AppError('PeakCut media check', 'ffprobe timed out after {} seconds.'.format(PROBE_TIMEOUT),
                       'Check that the local video is readable, then try again.') from exc
    except OSError as exc:
        raise AppError('PeakCut media check', 'Could not run ffprobe: {}'.format(exc),
                       'Repair FFmpeg and restart AuraCut.') from exc
    if proc.returncode:
        raise AppError('PeakCut media check', 'ffprobe failed: {}'.format(last_error(proc.stderr.splitlines(), 'exit code {}'.format(proc.returncode))),
                       'Check that the local video is complete and can be played.')
    try:
        data = json.loads(proc.stdout)
        duration = float(data['format']['duration'])
        streams = data['streams']
        if not math.isfinite(duration) or duration <= 0 or not isinstance(streams, list):
            raise ValueError('invalid duration or stream list')
        if not any(s.get('codec_type') == 'video' for s in streams):
            raise ValueError('no video track')
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError('PeakCut media check', 'ffprobe could not identify a video and duration: {}'.format(exc),
                       'Choose a complete video file with a playable video track.') from exc
    return {'duration': duration, 'audio': any(s.get('codec_type') == 'audio' for s in streams), 'streams': streams}


def run_ffmpeg(args, timeout, on_line, cwd=None):
    """Consume stdout while collecting a bounded stderr tail and enforcing a wall-clock timeout."""
    try:
        proc = subprocess.Popen(args, cwd=str(cwd) if cwd is not None else None, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='replace', bufsize=1)
    except OSError as exc:
        raise AppError('PeakCut FFmpeg', 'Could not start FFmpeg: {}'.format(exc),
                       'Repair FFmpeg and restart AuraCut.') from exc
    tail = deque(maxlen=30)
    timed_out = threading.Event()
    def stderr_reader():
        for line in proc.stderr:
            tail.append(line[-1000:])
    thread = threading.Thread(target=stderr_reader, daemon=True)
    thread.start()
    def stop_on_timeout():
        if proc.poll() is None:
            timed_out.set()
            proc.kill()
    timer = threading.Timer(timeout, stop_on_timeout)
    timer.daemon = True
    timer.start()
    try:
        for line in proc.stdout:
            on_line(line.strip())
        proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        thread.join()
        proc.stdout.close()
        proc.stderr.close()
    if timed_out.is_set():
        return False, 'FFmpeg timed out after {} seconds. {}'.format(timeout, last_error(tail, 'No further FFmpeg details.'))
    if proc.returncode:
        return False, last_error(tail, 'FFmpeg exited with code {}'.format(proc.returncode))
    return True, ''


def pick_windows(duration, clip_seconds, count, powers=None):
    if not math.isfinite(duration) or duration < 15:
        raise AppError('PeakCut analysis', 'Video is {:.2f} seconds; at least 15 seconds are required.'.format(duration),
                       'Choose a video at least 15 seconds long.')
    length = min(float(clip_seconds), duration)
    available = min(count, int((duration + 1e-7) // length))
    if powers is None:
        starts = [max(0.0, (duration - length) / 2)] if available == 1 else [(duration - length) * i / (available - 1) for i in range(available)]
        return [{'rank': i + 1, 'start': round(start, 3), 'end': round(start + length, 3), 'score': None} for i, start in enumerate(starts)]
    if not powers:
        raise AppError('PeakCut analysis', 'FFmpeg returned no audio loudness measurements.',
                       'Check that the audio track can be decoded and try again.')
    # One power measurement per second, with a prefix sum for O(1) window scoring.
    required = math.ceil(duration)
    powers = list(powers[:required])
    powers.extend([powers[-1]] * max(0, required - len(powers)))
    sums = [0.0]
    for value in powers:
        sums.append(sums[-1] + value)
    def area(t):
        index = min(int(t), len(powers))
        return sums[index] + (t - index) * powers[index] if index < len(powers) else sums[-1]
    last = max(0.0, duration - length)
    starts = [float(i) for i in range(math.floor(last) + 1)]
    if last - starts[-1] > 1e-7:
        starts.append(last)
    candidates = []
    for start in starts:
        avg = max((area(start + length) - area(start)) / length, 1e-12)
        candidates.append((10 * math.log10(avg), start))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    chosen = []
    def capacity(intervals):
        cursor = 0.0
        room = 0
        for left, right in sorted(intervals):
            room += max(0, int((left - cursor + 1e-7) // length))
            cursor = right
        return room + max(0, int((duration - cursor + 1e-7) // length))
    for score, start in candidates:
        end = start + length
        if any(start < right - 1e-7 and end > left + 1e-7 for left, right in chosen):
            continue
        trial = chosen + [(start, end)]
        if len(trial) + capacity(trial) < available:
            continue
        chosen = trial
        if len(chosen) == available:
            break
    chosen.sort(key=lambda interval: next(-score for score, start in candidates if start == interval[0]))
    scores = {start: score for score, start in candidates}
    return [{'rank': i + 1, 'start': round(left, 3), 'end': round(right, 3), 'score': round(scores[left], 2)}
            for i, (left, right) in enumerate(chosen)]


def analyze(config, filename, clip_seconds=30, count=3, progress=lambda *_: None):
    path = source_file(config, filename)
    progress(None, 'Checking video duration')
    info = probe(path)
    duration = info['duration']
    if duration < 15:
        return pick_windows(duration, clip_seconds, count)  # raises the user-facing short-video error
    notes = []
    if duration < clip_seconds:
        notes.append('Video is shorter than the selected clip length; using the whole video.')
    powers = None
    if not info['audio']:
        notes.append('No audio found, using evenly spaced moments.')
    else:
        powers = []
        def collect(line):
            if line.startswith('lavfi.astats.Overall.RMS_level='):
                try:
                    db = float(line.split('=', 1)[1])
                    powers.append(10 ** (max(-120, min(0, db)) / 10) if math.isfinite(db) else 1e-12)
                    progress(min(98, len(powers) / duration * 100), 'Measuring audio loudness')
                except ValueError as exc:
                    raise AppError('PeakCut analysis', 'FFmpeg returned an invalid loudness value: {}'.format(line),
                                   'Check the audio track and try another video.') from exc
        args = [executable('ffmpeg'), '-hide_banner', '-v', 'error', '-nostats', '-i', str(path), '-map', '0:a:0',
                '-vn', '-af', 'aresample=8000,asetnsamples=n=8000:p=1,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-',
                '-f', 'null', '-']
        progress(0, 'Measuring audio loudness')
        ok, detail = run_ffmpeg(args, ANALYZE_TIMEOUT, collect)
        if not ok:
            raise AppError('PeakCut analysis', 'FFmpeg could not analyze the audio: {}'.format(detail),
                           'Check the audio track and FFmpeg installation, then try again.')
    windows = pick_windows(duration, clip_seconds, count, powers)
    if len(windows) < count:
        notes.append('Only {} non-overlapping moment(s) fit in this video.'.format(len(windows)))
    return {'filename': filename, 'duration': round(duration, 3), 'windows': windows, 'message': ' '.join(notes),
            'scoring': 'audio RMS dBFS' if info['audio'] else 'evenly spaced (no audio)'}


def filter_for(framing):
    if framing == 'full_screen':
        return '[0:v:0]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30,format=yuv420p[v]'
    if framing not in ('whole_blur', 'zoom_blur'):
        raise AppError('PeakCut framing', 'Unknown framing mode: {}'.format(framing), 'Choose one of the three framing modes.')
    foreground = 'scale=1080:1920:force_original_aspect_ratio=decrease'
    if framing == 'zoom_blur':
        foreground += ',scale=trunc(iw*1.35/2)*2:trunc(ih*1.35/2)*2'
    return ('[0:v:0]split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,'
            'crop=1080:1920,boxblur=20:10[blur];[fg]' + foreground + '[front];'
            '[blur][front]overlay=(W-w)/2:(H-h)/2:shortest=1,setsar=1,fps=30,format=yuv420p[v]')


def render_args(source, target, start, end, framing, encoder, with_captions=False):
    filters = filter_for(framing)
    output_label = '[v]'
    if with_captions:
        # Both filenames are relative to the subprocess working directory, never a Windows path.
        filters += ';[v]ass=filename=captions.ass:fontsdir=fonts[captioned]'
        output_label = '[captioned]'
    args = [executable('ffmpeg'), '-hide_banner', '-v', 'error', '-nostats', '-y', '-ss', '{:.3f}'.format(start),
            '-i', str(source), '-t', '{:.3f}'.format(end - start), '-filter_complex', filters,
            '-map', output_label, '-map', '0:a:0?', '-c:v', encoder]
    if encoder == 'libx264':
        args.extend(['-preset', 'veryfast'])
    args.extend(['-r', '30', '-c:a', 'aac', '-movflags', '+faststart', '-progress', 'pipe:1', str(target)])
    return args


def publish_temp(temp, folder, stem):
    for number in range(10000):
        name = stem + ('' if number == 0 else '_{}'.format(number)) + '.mp4'
        dest = folder / name
        try:
            # A same-volume hard link publishes atomically and never replaces an existing file.
            os.link(temp, dest)
            return dest
        except FileExistsError:
            continue
        except OSError as exc:
            raise AppError('PeakCut render', 'Could not publish the finished video: {}'.format(exc),
                           'Check Output_Renders is on a local NTFS drive with write permission and free space.') from exc
    raise AppError('PeakCut render', 'Could not find an unused output filename.', 'Move or rename old renders, then retry.')


def publish_pair(video_temp, srt_temp, folder, stem):
    """Publish matching names without overwriting either file; roll back on failure."""
    for number in range(10000):
        base = stem + ('' if number == 0 else '_{}'.format(number))
        video, srt = folder / (base + '.mp4'), folder / (base + '.srt')
        try:
            os.link(video_temp, video)
        except FileExistsError:
            continue
        except OSError as exc:
            raise AppError('PeakCut render', 'Could not publish the finished video: {}'.format(exc),
                           'Check local Output_Renders permissions and free space, then retry.') from exc
        try:
            os.link(srt_temp, srt)
            return video, srt
        except FileExistsError:
            video.unlink()
        except OSError as exc:
            video.unlink()
            raise AppError('Caption output', 'Could not publish the SRT file: {}'.format(exc),
                           'Check local Output_Renders permissions and free space, then retry.') from exc
    raise AppError('PeakCut render', 'Could not find unused video and SRT filenames.',
                   'Move or rename old renders, then retry.')


def extract_clip_audio(source, wav, start, length, progress):
    progress(0, 'Extracting audio')
    def on_line(line):
        if line.startswith(('out_time_us=', 'out_time_ms=')):
            try:
                seconds = int(line.split('=', 1)[1]) / 1000000
                progress(min(99, seconds / length * 100), 'Extracting audio')
            except ValueError:
                pass  # FFmpeg can report N/A before it produces a timestamp.
    args = [executable('ffmpeg'), '-hide_banner', '-v', 'error', '-nostats', '-y', '-ss', '{:.3f}'.format(start),
            '-i', str(source), '-t', '{:.3f}'.format(length), '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000',
            '-c:a', 'pcm_s16le', '-progress', 'pipe:1', str(wav)]
    ok, detail = run_ffmpeg(args, RENDER_TIMEOUT, on_line)
    if not ok:
        raise AppError('Extracting caption audio', 'FFmpeg could not extract the selected audio: {}'.format(detail),
                       'Check the source audio and local disk space, then try again.')
    if not wav.is_file() or wav.stat().st_size == 0:
        raise AppError('Extracting caption audio', 'FFmpeg produced an empty audio file.',
                       'Check the source audio and local disk space, then try again.')


def render(config, filename, start, end, framing, captions_options=None, progress=lambda *_: None, cpu_only=False):
    source = source_file(config, filename)
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise AppError('PeakCut render', 'Invalid start or end time: {} to {}.'.format(start, end),
                       'Choose a valid ranked moment and try again.')
    filter_for(framing)
    progress(None, 'Checking video duration')
    info = probe(source)
    if end > info['duration'] + 0.001:
        raise AppError('PeakCut render', 'End time {} exceeds the video duration {:.3f}.'.format(end, info['duration']),
                       'Find moments again and choose a valid range.')
    length = end - start
    folder = workspace(config) / 'Output_Renders'
    fd, temp_name = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix='.mp4', dir=str(folder))
    os.close(fd)
    temp = Path(temp_name)
    caption_temp = None
    srt_temp = None
    with_captions = False
    caption_message = ''
    caption_warning = ''
    try:
        options = captions_options or {}
        if options.get('enabled'):
            style = options.get('style', 'bold_gold')
            language = options.get('language', 'auto')
            text_mode = options.get('text_mode', 'as_spoken')
            model_size = options.get('model_size', 'small')
            if style not in captions.STYLES or language not in captions.LANGUAGES or text_mode not in captions.TEXT_MODES or model_size not in ('small', 'base'):
                raise AppError('Captions options', 'An unsupported captions option was requested.', 'Choose one of the listed captions options.')
            installed, reason = captions.engine_status() if info['audio'] else (True, '')
            if not installed:
                caption_warning = ('Captions engine is not installed or cannot load: {}. Rendered without captions. '
                                   'Fix: Click Install captions engine and try again.').format(reason)
                logger.warning('%s', caption_warning)
            else:
                caption_temp = tempfile.TemporaryDirectory(prefix=TEMP_PREFIX, dir=str(folder))
                directory = Path(caption_temp.name)
                words = []
                if info['audio']:
                    wav = directory / 'clip.wav'
                    extract_clip_audio(source, wav, start, length, progress)
                    words = captions.transcribe_clip(wav, captions.model_folder(config), model_size, language, text_mode, length, progress)
                progress(None, 'Building captions')
                srt_temp = directory / 'captions.srt'
                try:
                    srt_temp.write_text(captions.srt_text(words), encoding='utf-8-sig')
                    if words:
                        (directory / 'captions.ass').write_text(captions.ass_text(words, style), encoding='utf-8')
                        (directory / 'fonts').mkdir()
                        shutil.copy2(captions.FONT_PATH, directory / 'fonts' / 'AuraCutCaption-Bold.ttf')
                        with_captions = True
                    else:
                        caption_message = 'No speech found, rendered without captions. The SRT is empty.'
                        logger.info(caption_message)
                except OSError as exc:
                    raise AppError('Building captions', 'Could not write captions or copy the bundled font: {}'.format(exc),
                                   'Check local workspace permissions and free space, then retry.') from exc
        gpu_error = None
        encoders = ('libx264',) if cpu_only else ('h264_amf', 'libx264')
        for encoder in encoders:
            stage = 'Rendering with GPU' if encoder == 'h264_amf' else ('GPU failed; rendering with CPU' if gpu_error else 'Rendering with CPU')
            progress(0, stage)
            def on_line(line):
                if line.startswith(('out_time_us=', 'out_time_ms=')):
                    try:
                        seconds = int(line.split('=', 1)[1]) / 1000000
                        progress(min(98, seconds / length * 100), stage)
                    except ValueError:
                        pass  # FFmpeg can report N/A before it has produced a timestamp.
            if with_captions:
                args = render_args(source, temp, start, end, framing, encoder, with_captions=True)
                ok, detail = run_ffmpeg(args, RENDER_TIMEOUT, on_line, cwd=caption_temp.name)
            else:
                ok, detail = run_ffmpeg(render_args(source, temp, start, end, framing, encoder), RENDER_TIMEOUT, on_line)
            if ok:
                break
            if encoder == 'h264_amf':
                gpu_error = detail
                logger.warning('PeakCut GPU encode failed: %s | Fix: Check AMD drivers and FFmpeg AMF support; retrying on CPU.', detail)
            else:
                raise AppError('PeakCut render', 'CPU encode failed: {}{}'.format(detail, '; GPU encode failed: {}'.format(gpu_error) if gpu_error else ''),
                               'Check FFmpeg, available disk space and the video, then try again.')
        progress(99, 'Verifying the finished video')
        if not temp.is_file() or temp.stat().st_size == 0:
            raise AppError('PeakCut render', 'FFmpeg produced an empty output file.', 'Check FFmpeg and available disk space, then retry.')
        output = probe(temp)
        if info['audio'] and not output['audio']:
            raise AppError('PeakCut render', 'The source had an audio track, but the rendered file has none.',
                           'Check that the source audio can be decoded and AAC encoding is available, then retry.')
        tolerance = max(0.5, min(2.0, length * 0.04))
        if abs(output['duration'] - length) > tolerance:
            raise AppError('PeakCut render', 'Expected {:.2f} seconds but output is {:.2f} seconds.'.format(length, output['duration']),
                           'Check the source video for errors and try rendering again.')
        video = next((s for s in output['streams'] if s.get('codec_type') == 'video'), {})
        if (video.get('width'), video.get('height')) != (1080, 1920):
            raise AppError('PeakCut render', 'Output dimensions are {}x{}, not 1080x1920.'.format(video.get('width'), video.get('height')),
                           'Check the FFmpeg installation and try again.')
        safe_stem = re.sub(r'[^A-Za-z0-9_-]+', '_', source.stem).strip('_')[:60] or 'video'
        stem = 'peakcut_{}_{:.2f}-{:.2f}_{}'.format(safe_stem, start, end, framing)
        srt_destination = None
        if srt_temp is not None:
            destination, srt_destination = publish_pair(temp, srt_temp, folder, stem)
        else:
            destination = publish_temp(temp, folder, stem)
        warnings = [caption_warning] if caption_warning else []
        if gpu_error:
            warnings.append('GPU encode failed, used CPU. Slower. GPU reason: {}. Fix: Check AMD drivers and FFmpeg AMF support.'.format(gpu_error))
        return {'filename': destination.name, 'folder': 'Output_Renders', 'duration': output['duration'],
                'encoder': encoder, 'warning': ' '.join(warnings), 'caption_message': caption_message,
                'captions_burned': with_captions, 'srt_filename': srt_destination.name if srt_destination else None}
    finally:
        temp.unlink(missing_ok=True)
        if caption_temp is not None:
            caption_temp.cleanup()
