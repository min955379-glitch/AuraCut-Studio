"""Stateless per-request Gemini-first router and scene validation."""
import asyncio
import json
import math
import re
import sys
from urllib.parse import urlsplit

import httpx
from google import genai
from google.genai import types

from app.common import AppError, DEFAULT_MODEL, clean, logger

MAX_YT_CREATE_CHARS = 880
DEFAULT_GEMINI_TIMEOUT_SECONDS = 12
MIN_GEMINI_TIMEOUT_SECONDS = 10


def gemini_timeout(config):
    """Return the effective timeout and a warning (if any), without changing config."""
    raw = config.get('GEMINI_TIMEOUT_SECONDS', '').strip()
    if not raw:
        return DEFAULT_GEMINI_TIMEOUT_SECONDS, None
    try:
        seconds = float(raw)
        if not math.isfinite(seconds):
            raise ValueError('not a finite number')
    except ValueError:
        return DEFAULT_GEMINI_TIMEOUT_SECONDS, (
            'GEMINI_TIMEOUT_SECONDS is not a finite number: {}. Using {} seconds.'
            .format(clean(raw, config), DEFAULT_GEMINI_TIMEOUT_SECONDS))
    if seconds < MIN_GEMINI_TIMEOUT_SECONDS:
        return MIN_GEMINI_TIMEOUT_SECONDS, (
            'GEMINI_TIMEOUT_SECONDS is {} seconds, below the 10-second minimum. Using 10 seconds.'
            .format(clean(raw, config)))
    if seconds > sys.maxsize / 1000:
        return DEFAULT_GEMINI_TIMEOUT_SECONDS, (
            'GEMINI_TIMEOUT_SECONDS is too large: {}. Using {} seconds.'
            .format(clean(raw, config), DEFAULT_GEMINI_TIMEOUT_SECONDS))
    return seconds, None


def ollama_url(config):
    url = config.get('OLLAMA_URL', '').strip().rstrip('/') or 'http://127.0.0.1:11434'
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('OLLAMA_URL must be a local http or https URL without credentials, query, or fragment')
    return url


def validate_scenes(text, count):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('AI returned an empty response')
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I)
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError('AI response was not valid JSON: {}'.format(exc)) from exc
    scenes = data.get('scenes') if isinstance(data, dict) else data
    if not isinstance(scenes, list) or len(scenes) != count:
        raise ValueError('AI returned the wrong number of scenes; expected {}'.format(count))
    result = []
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict) or not isinstance(scene.get('title'), str) or not scene['title'].strip() or not isinstance(scene.get('video_prompt'), str) or not scene['video_prompt'].strip():
            raise ValueError('Scene {} needs a nonempty title and video_prompt'.format(index))
        result.append({'title': scene['title'].strip(), 'video_prompt': scene['video_prompt'].strip()})
    return result


def overlong(scenes):
    return [(i, len(scene['video_prompt'])) for i, scene in enumerate(scenes, 1) if len(scene['video_prompt']) > MAX_YT_CREATE_CHARS]


async def gemini_generate(prompt, config):
    timeout, warning = gemini_timeout(config)
    if warning:
        logger.warning('Gemini timeout | Cause: %s | Fix: Set GEMINI_TIMEOUT_SECONDS to 10 or more in .env.', warning)
    client = genai.Client(api_key=config['GEMINI_API_KEY'], http_options=types.HttpOptions(
        timeout=math.ceil(timeout * 1000), retry_options=types.HttpRetryOptions(attempts=1)))
    try:
        async def call():
            response = await client.aio.models.generate_content(
                model=config['GEMINI_MODEL'], contents=prompt,
                config=types.GenerateContentConfig(automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True, maximum_remote_calls=1)))
            return response.text
        return await asyncio.wait_for(call(), timeout=timeout)
    finally:
        await client.aio.aclose()


async def ollama_generate(prompt, config):
    url = ollama_url(config)
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        response = await client.post(url + '/api/generate', json={
            'model': config.get('OLLAMA_MODEL', '').strip() or DEFAULT_MODEL,
            'prompt': prompt, 'stream': False, 'format': 'json'})
        response.raise_for_status()
        data = response.json()
        return data.get('response', '') if isinstance(data, dict) else ''


def reason(exc, config):
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return 'timed out ({})'.format(type(exc).__name__)
    if isinstance(exc, httpx.HTTPStatusError):
        return clean('HTTP {}: {}'.format(exc.response.status_code, exc.response.text[:500]), config)
    message = str(exc) or type(exc).__name__
    return clean('{}: {}'.format(type(exc).__name__, message), config)


async def route_once(prompt, count, config):
    gemini_failure = 'not configured (GEMINI_API_KEY and GEMINI_MODEL are both required)'
    if config.get('GEMINI_API_KEY', '').strip() and config.get('GEMINI_MODEL', '').strip():
        try:
            scenes = validate_scenes(await gemini_generate(prompt, config), count)
            return scenes, 'Gemini', None
        except Exception as exc:
            gemini_failure = reason(exc, config)
            logger.warning('Gemini failed: %s | Fix: check the Gemini key, model, network, and quota; trying Ollama.', gemini_failure)
    try:
        scenes = validate_scenes(await ollama_generate(prompt, config), count)
        return scenes, 'Ollama', gemini_failure
    except Exception as exc:
        ollama_failure = reason(exc, config)
        raise AppError('Script generation', 'Gemini: {}. Ollama: {}.'.format(gemini_failure, ollama_failure),
                       'Check the Gemini key and model in .env; start Ollama and install the configured model.', 503) from exc


async def generate_script(topic, count, config):
    prompt = ('Create {} short video scenes about the topic below. Return only JSON in the form '
              '{{"scenes":[{{"title":"short title","video_prompt":"visual video prompt"}}]}}. '
              'Each video_prompt must be at most {} characters. Topic: {}').format(count, MAX_YT_CREATE_CHARS, topic)
    for attempt in range(3):
        scenes, provider, fallback = await route_once(prompt, count, config)
        too_long = overlong(scenes)
        if not too_long:
            answered = provider
            if provider == 'Ollama':
                answered += ' (Gemini failed: {})'.format(fallback)
            return {'ok': True, 'scenes': scenes, 'answered_by': answered}
        details = ', '.join('scene {}: {} characters'.format(i, length) for i, length in too_long)
        if attempt == 2:
            raise AppError('Script prompt length', 'After two shortening retries, {} exceeded {} characters.'.format(details, MAX_YT_CREATE_CHARS),
                           'Try a simpler topic or fewer scenes, then generate again.', 422)
        logger.warning('Prompt too long: %s | Fix: requesting a shorter complete script (retry %s of 2).', details, attempt + 1)
        prompt += '\nThe previous response had overlong prompts ({}). Rewrite all {} scenes, keeping each video_prompt at most {} characters; do not cut words. Return the same JSON format.'.format(details, count, MAX_YT_CREATE_CHARS)
    raise AssertionError('Unreachable')
