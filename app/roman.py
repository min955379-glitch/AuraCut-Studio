"""Roman caption conversion through the existing Gemini/Ollama router."""
import asyncio
import hashlib
import json
import re
from pathlib import Path

from app import brain_ai, captions
from app.common import AppError, logger, workspace

CHUNK = 40
MODES = ('english', 'roman_hindi', 'roman_urdu', 'original')


def latin_word(word):
    # Preserve already-Latin words (and numbers/punctuation) verbatim.
    return all(ord(c) < 128 for c in word)


def valid_roman(text):
    return bool(text and re.fullmatch(r"[A-Za-z0-9.,!?;:'\"()\-]+", text))


def decode(text):
    if not isinstance(text, str):
        raise ValueError('AI response was not text')
    return json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.I))


def convert(config, source, start, end, words, mode, model_size, language, progress=lambda *_: None):
    if mode not in ('roman_hindi', 'roman_urdu') or not words:
        return words, '', ''
    if all(latin_word(w['text']) for w in words):
        return words, 'Speech is already in Latin letters; Roman conversion skipped.', ''
    stat = Path(source).stat()
    key_data = {'version': 2, 'source': str(Path(source).resolve()), 'size': stat.st_size,
                'mtime': stat.st_mtime_ns, 'start': start, 'end': end, 'mode': mode,
                'model': model_size, 'language': language}
    key = hashlib.sha256(json.dumps(key_data, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    folder = workspace(config) / 'caption_cache'
    if folder.is_symlink() or (hasattr(folder, 'is_junction') and folder.is_junction()):
        raise AppError('Roman caption cache', 'The caption cache is a link or junction.', 'Use a real local caption_cache folder.')
    folder.mkdir(parents=True, exist_ok=True)
    cache = folder / (key + '.json')
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding='utf-8'))
            if isinstance(data.get('words'), list) and data.get('provider') in ('Gemini', 'Ollama', 'Gemini + Ollama'):
                progress(None, 'Using saved Roman conversion · Answered by: {}'.format(data['provider']))
                return data['words'], 'Using saved Roman conversion. Answered by: {}{}'.format(data['provider'], ' · Line timing is approximate.' if data.get('line_fallback') else ''), ''
            raise ValueError('cache format is invalid')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning('Roman caption cache could not be read: %s | Fix: Check local cache permissions; converting again.', exc)
    progress(None, 'Converting to Roman letters')
    style = 'Roman Hindi (Hinglish)' if mode == 'roman_hindi' else 'Roman Urdu'
    providers = set()
    spellings = {}
    reasons = []
    used_lines = False

    def route(prompt, validator):
        result, provider = asyncio.run(brain_ai.route_validated(prompt, config, validator))
        providers.add(provider)
        progress(None, 'Converting to Roman letters · Answered by: {}'.format(provider))
        return result

    def batch(group, strict=False):
        numbered = [{'number': i + 1, 'word': w['text']} for i, w in enumerate(group)]
        prompt = ('Transliterate to simple consistent {}. Do not translate. Preserve already-English Latin words exactly, including punctuation. '
                  'No emojis. Return only a JSON array of objects: [{{"number":1,"word":"..."}}, ...]. '
                  'Exactly one Latin-letter word for each numbered input, in order; do not merge, split, add or remove words. '
                  'Use these existing spellings consistently: {}. Input JSON: {}{}').format(
                      style, json.dumps(spellings, ensure_ascii=False), json.dumps(numbered, ensure_ascii=False),
                      ' IMPORTANT: output must contain exactly {} numbered objects.'.format(len(group)) if strict else '')
        def validate(response):
            result = decode(response)
            if not isinstance(result, list) or len(result) != len(group):
                raise ValueError('Expected {} numbered words, got {}'.format(len(group), len(result) if isinstance(result, list) else 'non-array'))
            converted = []
            for i, (entry, original) in enumerate(zip(result, group), 1):
                if isinstance(entry, str):
                    value = entry.strip()
                elif isinstance(entry, dict) and entry.get('number') == i and isinstance(entry.get('word'), str):
                    value = entry['word'].strip()
                else:
                    raise ValueError('Word {} is missing or out of order'.format(i))
                if not valid_roman(value):
                    raise ValueError('Word {} is not one Latin word'.format(i))
                if latin_word(original['text']) and value != original['text']:
                    raise ValueError('English word {} was changed'.format(i))
                converted.append(value)
            return converted
        return route(prompt, validate)

    def line(group):
        text = ' '.join(w['text'] for w in group)
        prompt = ('Transliterate this line into simple {} without translation or emoji; preserve English words exactly. '
                  'Keep these spellings consistent: {}. Return only JSON as {{"line":"Latin words here"}}. Input: {}').format(style, json.dumps(spellings, ensure_ascii=False), json.dumps(text, ensure_ascii=False))
        def validate(response):
            data = decode(response)
            value = data.get('line') if isinstance(data, dict) else data
            if not isinstance(value, str) or not value.split() or not all(valid_roman(w) for w in value.split()):
                raise ValueError('Line was not valid Latin text')
            tokens = value.split()
            english = [w['text'] for w in group if latin_word(w['text'])]
            pos = 0
            for word in english:
                try:
                    pos = tokens.index(word, pos) + 1
                except ValueError:
                    raise ValueError('Line conversion changed or omitted English word: {}'.format(word))
            return tokens
        tokens = route(prompt, validate)
        begin, finish = group[0]['start'], max(w['end'] for w in group)
        step = (finish - begin) / len(tokens)
        return [{'text': token, 'start': begin + i * step, 'end': begin + (i + 1) * step} for i, token in enumerate(tokens)]

    try:
        result = []
        for offset in range(0, len(words), CHUNK):
            group = words[offset:offset + CHUNK]
            values = None
            for strict in (False, True):
                try:
                    values = batch(group, strict)
                    break
                except Exception as exc:
                    reasons.append(str(exc))
                    logger.warning('Roman conversion attempt failed: %s | Fix: Retrying with stricter instructions or shorter lines.', exc)
            if values is None:
                used_lines = True
                # Preserve chronology even when the model cannot return one token per input.
                for screen in captions.group_words(group):
                    result.extend(line(screen['words']))
            else:
                for original, value in zip(group, values):
                    previous = spellings.get(original['text'])
                    if previous is not None and not latin_word(original['text']):
                        value = previous
                    spellings[original['text']] = value
                    result.append({**original, 'text': value})
        provider = ' + '.join(name for name in ('Gemini', 'Ollama') if name in providers)
        cache.write_text(json.dumps({'words': result, 'provider': provider, 'line_fallback': used_lines}, ensure_ascii=False), encoding='utf-8')
        return result, 'Answered by: {}{}'.format(provider, ' · Line timing is approximate.' if used_lines else ''), ''
    except Exception as exc:
        reasons.append(str(exc))
        cause = '; '.join(reasons[-4:])
        logger.warning('Roman conversion failed: %s | Fix: Check Gemini/Ollama and retry. Used original script.', cause)
        return words, '', 'Roman conversion failed, used original script. Cause: {}. Fix: Check Gemini or start Ollama, then retry.'.format(cause)
