"""Isolated optional speech worker; all model files stay in the local model directory."""
import json
import sys
from pathlib import Path


def stage(text):
    print(json.dumps({'stage': text}), flush=True)


def main():
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model
    from huggingface_hub.errors import LocalEntryNotFoundError
    from app.captions import transcribe_words

    wav, model_dir, size, language, text_mode, length, output = sys.argv[1:]
    if size not in ('small', 'base'):
        raise ValueError('Unsupported model size: {}'.format(size))
    model_dir = str(Path(model_dir))
    stage('Checking local speech model')
    try:
        model_path = Path(download_model(size, local_files_only=True, cache_dir=model_dir))
        if not all((model_path / name).is_file() for name in ('model.bin', 'config.json', 'tokenizer.json')):
            model_path = None
    except LocalEntryNotFoundError:
        model_path = None
    if model_path is None:
        approximate = 'about 500 MB' if size == 'small' else 'about 150 MB'
        stage('Downloading speech model, {}, one time only'.format(approximate))
        model_path = Path(download_model(size, local_files_only=False, cache_dir=model_dir, use_auth_token=False))
        missing = [name for name in ('model.bin', 'config.json', 'tokenizer.json') if not (model_path / name).is_file()]
        if missing:
            raise FileNotFoundError('Downloaded model is missing: {}'.format(', '.join(missing)))
    stage('Loading speech model on CPU (int8)')
    model = WhisperModel(str(model_path), device='cpu', compute_type='int8', download_root=model_dir, local_files_only=True)
    stage('Transcribing selected clip')
    words = transcribe_words(model, wav, float(length), language, text_mode,
                             on_segment=lambda sec: stage('Transcribing: {:.1f} seconds processed'.format(sec)))
    Path(output).write_text(json.dumps(words, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('{}: {}'.format(type(exc).__name__, exc), file=sys.stderr, flush=True)
        sys.exit(1)
