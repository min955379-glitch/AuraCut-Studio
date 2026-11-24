import asyncio
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import autobackup, captions, peakcut, roman, storage
from app.main import render_with_backup


def words(texts):
    return [{'text': text, 'start': i * .4, 'end': (i + 1) * .4} for i, text in enumerate(texts)]


class RomanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'Input_Media').mkdir()
        (self.root / 'Output_Renders').mkdir()
        self.source = self.root / 'Input_Media' / 'speech.mp4'
        self.source.write_bytes(b'video')
        self.config = {'AURACUT_WORKSPACE': str(self.root)}
        self.input = words(['नमस्ते', 'world', 'दोस्त'])

    def run_convert(self, data=None, mode='roman_hindi'):
        return roman.convert(self.config, self.source, 0, 20, data or self.input, mode, 'small', 'hi')

    def test_equal_words_and_preserved_english_and_cache(self):
        response = json.dumps([{'number': 1, 'word': 'namaste'}, {'number': 2, 'word': 'world'}, {'number': 3, 'word': 'dost'}])
        with patch('app.brain_ai.ollama_generate', new=AsyncMock(return_value=response)) as model:
            output, message, warning = self.run_convert()
            self.assertEqual(['namaste', 'world', 'dost'], [w['text'] for w in output])
            self.assertEqual(self.input[0]['start'], output[0]['start'])
            self.assertIn('Answered by: Ollama', message)
            self.assertFalse(warning)
            cached, message, _ = self.run_convert(words(['नमस्कार', 'world', 'दोस्त']))
            self.assertEqual(output, cached)
            self.assertIn('saved Roman', message)
            self.assertEqual(1, model.await_count)

    def test_wrong_length_retries_then_line_fallback(self):
        responses = [json.dumps(['bad']), json.dumps(['bad']), json.dumps({'line':'namaste world dost'})]
        with patch('app.brain_ai.ollama_generate', new=AsyncMock(side_effect=responses)) as model:
            output, _, warning = self.run_convert()
        self.assertEqual(3, model.await_count)
        self.assertFalse(warning)
        self.assertEqual(['namaste','world','dost'], [w['text'] for w in output])
        self.assertEqual(self.input[0]['start'], output[0]['start'])
        self.assertAlmostEqual(self.input[-1]['end'], output[-1]['end'])

    def test_line_fallback_spreads_time_across_changed_word_count(self):
        replies = [json.dumps(['bad']), json.dumps(['bad']), json.dumps({'line':'namaste world'})]
        with patch('app.brain_ai.ollama_generate', new=AsyncMock(side_effect=replies)):
            result, _, warning = self.run_convert()
        self.assertFalse(warning)
        self.assertEqual(['namaste', 'world'], [word['text'] for word in result])
        self.assertEqual(self.input[0]['start'], result[0]['start'])
        self.assertAlmostEqual(self.input[-1]['end'], result[-1]['end'])
        self.assertAlmostEqual(result[0]['end'], result[1]['start'])

    def test_changed_english_rejected_and_total_failure_original(self):
        bad = json.dumps(['namaste', 'planet', 'dost'])
        with patch('app.brain_ai.ollama_generate', new=AsyncMock(return_value=bad)):
            result, _, warning = self.run_convert()
        self.assertEqual(self.input, result)
        self.assertIn('Roman conversion failed, used original script', warning)
        self.assertIn('English', warning)
        self.assertTrue('Fix:' in warning)

    def test_chunking_and_consistency(self):
        data = words(['नमस्ते'] * 41)
        calls = []
        async def gen(prompt, config):
            calls.append(prompt)
            payload = prompt.split('Input JSON: ', 1)[1].split(' IMPORTANT:',1)[0]
            group = json.loads(payload)
            return json.dumps(['namaste' if len(calls)==1 else 'namaskar'] * len(group))
        with patch('app.brain_ai.ollama_generate', side_effect=gen):
            result, _, warning = self.run_convert(data)
        self.assertEqual(2, len(calls))
        self.assertEqual(['namaste']*41, [w['text'] for w in result])
        self.assertFalse(warning)

    def test_english_input_skips_conversion(self):
        with patch('app.brain_ai.ollama_generate', new=AsyncMock()) as model:
            result, message, warning = self.run_convert(words(['hello', 'world']))
        model.assert_not_awaited()
        self.assertIn('skipped', message)
        self.assertFalse(warning)
        self.assertEqual('hello', result[0]['text'])

    def test_gemini_first_and_ollama_when_invalid(self):
        cfg = dict(self.config, GEMINI_API_KEY='secret', GEMINI_MODEL='test')
        with patch('app.brain_ai.gemini_generate', new=AsyncMock(return_value='bad json')) as g, patch('app.brain_ai.ollama_generate', new=AsyncMock(return_value=json.dumps(['namaste','world','dost']))) as o:
            result, message, _ = roman.convert(cfg, self.source, 0, 20, self.input, 'roman_urdu', 'small', 'hi')
        self.assertEqual(1, g.await_count)
        self.assertEqual(1, o.await_count)
        self.assertEqual('namaste', result[0]['text'])
        self.assertIn('Ollama', message)

    def test_ass_srt_match_each_mode_and_translation_task(self):
        source_info = {'duration': 30, 'audio': True}
        output_info = {'duration': 15, 'audio': True, 'streams':[{'codec_type':'video','width':1080,'height':1920}]}
        samples = {'english':words(['hello','world']), 'original':words(['नमस्ते','world']), 'roman_hindi':words(['नमस्ते','world']), 'roman_urdu':words(['سلام','world'])}
        for mode, source_words in samples.items():
            captured = {}
            def encode(args, timeout, on_line, **kwargs):
                captured['ass'] = (Path(kwargs['cwd']) / 'captions.ass').read_text(encoding='utf-8')
                Path(args[-1]).write_bytes(b'encoded')
                return True, ''
            def extract(source, wav, start, length, progress):
                wav.write_bytes(b'wav')
            converted = words(['namaste','world']) if mode=='roman_hindi' else words(['salaam','world'])
            with self.subTest(mode=mode), patch('app.peakcut.probe', side_effect=[source_info,output_info]), patch('app.peakcut.executable', return_value='ffmpeg'), patch('app.peakcut.run_ffmpeg', side_effect=encode), patch('app.peakcut.extract_clip_audio', side_effect=extract), patch('app.captions.engine_status', return_value=(True,'')), patch('app.captions.transcribe_clip', return_value=source_words) as transcribe, patch('app.roman.convert', return_value=(converted,'Answered by: Ollama','')):
                result = peakcut.render(self.config, 'speech.mp4', 0, 15, 'full_screen', {'enabled':True,'mode':mode}, cpu_only=True)
            expected = converted if mode.startswith('roman_') else source_words
            srt = (self.root / 'Output_Renders' / result['srt_filename']).read_text(encoding='utf-8-sig')
            for word in expected:
                self.assertIn(word['text'], srt)
                self.assertIn(word['text'].upper(), captured['ass'])
            self.assertEqual('translate' if mode=='english' else 'as_spoken', transcribe.call_args.args[4])


class AutoBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'
        self.drive = self.root / 'drive'
        (self.work / 'Output_Renders').mkdir(parents=True)
        (self.work / 'Input_Media').mkdir()
        self.drive.mkdir()
        self.cfg = {'AURACUT_WORKSPACE': str(self.work), 'AURACUT_DRIVE_BACKUP': str(self.drive)}
        self.mp4 = self.work / 'Output_Renders' / 'clip.mp4'
        self.mp4.write_bytes(b'video')

    def wait(self, name, status):
        for _ in range(200):
            data = autobackup.status(self.cfg)['records'].get('Output_Renders/' + name, {})
            if data.get('status') == status:
                return data
            time.sleep(.01)
        self.fail('Timed out waiting for {}; got {}'.format(status, data))

    def test_render_completes_without_waiting_for_backup_and_skips_empty_srt(self):
        srt = self.work / 'Output_Renders' / 'clip.srt'
        srt.write_bytes(b'')
        gate = threading.Event()
        started = threading.Event()
        def slow(config, folder, name, progress):
            started.set()
            gate.wait(3)
            return {'message':'copied'}
        self.addCleanup(gate.set)
        with patch('app.peakcut.render', return_value={'filename':'clip.mp4','srt_filename':'clip.srt'}), patch('app.storage.backup_file', side_effect=slow):
            begin = time.monotonic()
            result = render_with_backup(self.cfg, 'input.mp4', 0, 15, 'full_screen', {}, lambda *_: None)
            self.assertLess(time.monotonic()-begin, 1)
            self.assertEqual('queued', result['auto_backup'])
            self.assertTrue(started.wait(1))
            self.assertNotIn('Output_Renders/clip.srt', autobackup.status(self.cfg)['records'])
            gate.set()
            self.wait('clip.mp4', 'backed_up')

    def test_drive_missing_leaves_pending_then_retries(self):
        self.drive.rmdir()
        autobackup.enqueue(self.cfg, 'Output_Renders', ['clip.mp4'])
        record = self.wait('clip.mp4', 'pending')
        self.assertIn('missing', record['message'])
        self.assertIn('Start Google Drive', record['message'])
        self.assertTrue(self.mp4.exists())
        self.drive.mkdir()
        self.assertEqual(1, autobackup.retry_pending(self.cfg))
        self.wait('clip.mp4', 'backed_up')
        self.assertEqual(self.mp4.read_bytes(), (self.drive / self.mp4.name).read_bytes())
        self.assertTrue(autobackup.status(self.cfg)['last']['time'])

    def test_interrupted_backup_becomes_pending_after_restart(self):
        path = self.work / 'auto_backup_state.json'
        path.write_text(json.dumps({'records': {'Output_Renders/clip.mp4': {'folder':'Output_Renders','name':'clip.mp4','status':'backing_up'}}, 'last':None}))
        record = autobackup.status(self.cfg)['records']['Output_Renders/clip.mp4']
        self.assertEqual('pending', record['status'])
        self.assertIn('interrupted', record['message'])
        self.assertFalse((self.drive / 'clip.mp4').exists())

    def test_retry_continues_after_missing_pending_file(self):
        records = {
            'Output_Renders/missing.mp4': {'folder':'Output_Renders','name':'missing.mp4','status':'pending'},
            'Output_Renders/clip.mp4': {'folder':'Output_Renders','name':'clip.mp4','status':'pending'},
        }
        (self.work / 'auto_backup_state.json').write_text(json.dumps({'records':records,'last':None}))
        with self.assertRaises(Exception) as caught:
            autobackup.retry_pending(self.cfg)
        self.assertIn('missing.mp4', str(caught.exception))
        self.wait('clip.mp4','backed_up')
        self.assertEqual('pending', autobackup.status(self.cfg)['records']['Output_Renders/missing.mp4']['status'])

    def test_disabled_render_never_schedules(self):
        cfg = dict(self.cfg, AURACUT_AUTO_BACKUP='false')
        with patch('app.peakcut.render', return_value={'filename':'clip.mp4','srt_filename':None}), patch('app.autobackup.enqueue') as enqueue:
            result = render_with_backup(cfg, 'input.mp4', 0, 15, 'full_screen', {}, lambda *_: None)
        enqueue.assert_not_called()
        self.assertEqual('off', result['auto_backup'])

    def test_multiple_files_and_off(self):
        srt = self.work / 'Output_Renders' / 'clip.srt'
        srt.write_bytes(b'caption')
        self.assertTrue(autobackup.enabled(self.cfg))
        self.assertFalse(autobackup.enabled({}))
        self.assertFalse(autobackup.inputs_enabled(self.cfg))
        autobackup.enqueue(self.cfg, 'Output_Renders', ['clip.mp4','clip.srt'])
        self.wait('clip.mp4','backed_up')
        self.wait('clip.srt','backed_up')
        self.assertEqual(b'caption',(self.drive/'clip.srt').read_bytes())

    def test_file_flags_for_available_backed_up_and_incomplete(self):
        item = storage.list_files(self.cfg)[0]
        self.assertTrue(item['drive_available'])
        self.assertFalse(item['backed_up'])
        (self.drive/'clip.mp4').write_bytes(b'x')
        self.assertTrue(storage.list_files(self.cfg)[0]['backup_incomplete'])
        (self.drive/'clip.mp4').write_bytes(b'video')
        self.assertTrue(storage.list_files(self.cfg)[0]['backed_up'])
        (self.drive/'clip.mp4').unlink()
        self.drive.rmdir()
        self.assertFalse(storage.list_files(self.cfg)[0]['drive_available'])


class FontCoverageTests(unittest.TestCase):
    def test_bundled_font_covers_sample_original_scripts(self):
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            self.skipTest('fonttools is only needed for font inspection')
        font = TTFont(captions.FONT_PATH)
        self.addCleanup(font.close)
        cmap = font.getBestCmap()
        for char in 'नमस्ते दुनिया سلام دنیا اردو':
            if char != ' ':
                self.assertIn(ord(char), cmap)
        scripts = {record.ScriptTag for record in font['GSUB'].table.ScriptList.ScriptRecord}
        self.assertTrue({'arab', 'deva', 'dev2'} <= scripts)


@unittest.skipUnless(shutil.which('node'), 'node needed for UI test only')
class LabelTests(unittest.TestCase):
    def test_labels(self):
        script = '''const fs=require('fs'),vm=require('vm');const c={};vm.createContext(c);vm.runInContext(fs.readFileSync('app/static/app.js','utf8'),c);const a=c.studio();const f={folder:'Output_Renders',name:'x',drive_available:true,backed_up:false,backup_incomplete:false};const eq=(s)=>{if(a.fileBackupLabel(f)!==s)throw Error(a.fileBackupLabel(f))};eq('Not backed up yet');a.autoRecords={'Output_Renders/x':{status:'backing_up'}};eq('Backing up');a.autoRecords={};f.backed_up=true;eq('Backed up');f.backed_up=false;f.backup_incomplete=true;eq('Backup incomplete (click Back up again)');f.drive_available=false;eq('Drive unavailable (start Google Drive)');'''
        result = subprocess.run(['node','-e',script], capture_output=True, text=True, timeout=10)
        self.assertEqual(0,result.returncode,result.stderr)


if __name__ == '__main__':
    unittest.main()
