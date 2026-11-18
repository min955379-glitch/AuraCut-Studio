"""Isolated optional speech worker; all model files stay in the local model directory."""
import json
import sys
import traceback
import wave
from pathlib import Path


def stage(text, phase):
    print(json.dumps({'stage': text, 'phase': phase}), flush=True)


def read_clip_audio(wav_path):
    # PeakCut extracts 16 kHz mono PCM. Passing an array avoids PyAV decoding;
    # it also makes the exact audio format fed to Whisper explicit.
    import numpy as np
    with wave.open(str(wav_path), 'rb') as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, 'NONE'):
            raise ValueError('Expected a mono, 16 kHz, 16-bit PCM WAV from PeakCut.')
        samples = wav.readframes(wav.getnframes())
    return np.frombuffer(samples, dtype='<i2').astype(np.float32) / 32768.0


def main():
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model
    from huggingface_hub.errors import LocalEntryNotFoundError
    from app.captions import transcribe_words

    wav, model_dir, size, language, text_mode, length, output = sys.argv[1:]
    if size not in ('small', 'base'):
        raise ValueError('Unsupported model size: {}'.format(size))
    model_dir = str(Path(model_dir))
    stage('Checking local speech model', 'model')
    try:
        model_path = Path(download_model(size, local_files_only=True, cache_dir=model_dir))
        saved = all((model_path / name).is_file() for name in ('model.bin', 'config.json', 'tokenizer.json'))
    except LocalEntryNotFoundError:
        saved = False
    if saved:
        stage('Using saved speech model', 'model')
        model = WhisperModel(size, device='cpu', compute_type='int8', download_root=model_dir, local_files_only=True)
    else:
        approximate = 'about 500 MB' if size == 'small' else 'about 150 MB'
        stage('Downloading speech model, {}, one time only'.format(approximate), 'download')
        # Same public constructor shape as the user's successful standalone test.
        model = WhisperModel(size, device='cpu', compute_type='int8', download_root=model_dir)
    stage('Transcribing selected clip', 'transcribe')
    audio = read_clip_audio(wav)
    words = transcribe_words(model, audio, float(length), language, text_mode,
                             on_segment=lambda sec: stage('Transcribing: {:.1f} seconds processed'.format(sec), 'transcribe'))
    Path(output).write_text(json.dumps(words, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
