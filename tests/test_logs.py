"""Fictional local logs only; ingestion never modifies its sources."""
import codecs
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from control_console.logs import LogService
from control_console.importers import ImportFailure


class LogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.service = LogService(self.root / 'runtime', lambda: {'task-fiction'})
        self.log = self.root / 'app.log'

    def configure(self, **extra):
        return self.service.configure(dict(software='material', path=str(self.log), enabled=True, **extra))['source']

    def test_utf8_incremental_read_only_and_trusted_association(self):
        original = 'INFO 中文 taskId=task-fiction\nERROR 未知 taskId=unknown\n'.encode()
        self.log.write_bytes(original)
        self.configure()
        self.service.poll()
        self.assertEqual(self.service.poll()['total'], 2)
        self.assertEqual(self.log.read_bytes(), original)
        self.assertEqual(self.service.summary()['tasks']['task-fiction']['count'], 1)
        self.assertEqual(self.service.state({'taskId': 'unknown'})['total'], 0)
        with self.log.open('ab') as stream:
            stream.write('INFO 新增\n'.encode())
        self.assertEqual(self.service.poll()['total'], 3)
        self.assertEqual(self.service.state({'level': 'ERROR', 'search': '未知'})['total'], 1)

    def test_utf16_clear_same_length_and_replace(self):
        self.log.write_text('INFO 旧行\n', encoding='utf-16')
        self.configure(encoding='utf-16')
        self.assertEqual(self.service.poll()['total'], 1)
        self.log.write_text('INFO 新行\n', encoding='utf-16')
        self.assertEqual(self.service.poll()['total'], 2)
        self.log.write_bytes(codecs.BOM_UTF16_LE)
        self.service.poll()
        with self.log.open('ab') as stream:
            stream.write('ERROR 重建\n'.encode('utf-16-le'))
        self.assertEqual(self.service.poll()['total'], 3)
        replacement = self.root / 'replacement'
        replacement.write_text('INFO 替换\n', encoding='utf-16')
        os.replace(replacement, self.log)
        self.assertEqual(self.service.poll()['total'], 4)

    def test_rotation_and_concurrent_idempotency(self):
        self.log.write_text('INFO first\n', encoding='utf8')
        self.configure(rotationGlob='app.log*')
        self.service.poll()
        self.log.rename(self.root / 'app.log.1')
        self.log.write_text('INFO second\n', encoding='utf8')
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda _: self.service.poll(), range(10)))
        self.assertEqual(self.service.state()['total'], 2)

    def test_partial_multibyte_and_read_budget(self):
        self.service.MAX_BYTES = 20
        self.log.write_bytes('INFO 中文'.encode()[:-1])
        self.configure()
        self.assertEqual(self.service.poll()['total'], 0)
        with self.log.open('ab') as stream:
            stream.write('文'.encode()[-1:] + b'\n')
        self.assertEqual(self.service.poll()['events'][0]['message'], 'INFO 中文')
        with self.log.open('ab') as stream:
            stream.write(b'INFO one\nINFO two\nINFO three\n')
        first = self.service.poll()
        self.assertEqual(first['sources'][0]['status'], 'limited')
        self.assertEqual(self.service.poll()['total'], 4)

    def test_thirty_days_and_copy_cleanup(self):
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        new = datetime.now(timezone.utc).isoformat()
        self.log.write_text(f'{old} INFO old\n{new} INFO recent\nINFO unstamped\n', encoding='utf8')
        self.configure()
        self.assertEqual(self.service.poll()['total'], 2)
        before = self.log.read_bytes()
        with self.service._db() as db:
            db.execute('UPDATE events SET retained=?', (old,))
        self.assertEqual(self.service.poll()['total'], 0)
        self.assertEqual(self.log.read_bytes(), before)
        old_file = self.root / 'old.log'
        old_file.write_text('INFO old mtime\n')
        stamp = (datetime.now(timezone.utc) - timedelta(days=31)).timestamp()
        os.utime(old_file, (stamp, stamp))
        self.service.configure(dict(software='editing', path=str(old_file), enabled=True))
        self.assertEqual(self.service.poll()['total'], 0)

    def test_validation_disabled_missing_encoding_and_delete(self):
        for extra in ({'path': 'relative.log'}, {'path': str(self.root)}, {'rotationGlob': '../*'}, {'rotationGlob': '**'}, {'encoding': 'gbk'}, {'enabled': 'yes'}, {'software': 'unknown'}):
            body = dict(software='material', path=str(self.log))
            body.update(extra)
            with self.assertRaises(ImportFailure):
                self.service.configure(body)
        source = self.service.configure(dict(software='material', path=str(self.log)))['source']
        self.assertEqual(self.service.poll()['sources'][0]['status'], 'disabled')
        source = self.configure(encoding='utf-16')
        self.assertEqual(self.service.poll()['sources'][-1]['status'], 'waiting_file')
        self.log.write_bytes(b'no bom\n')
        self.assertEqual(self.service.poll()['sources'][-1]['status'], 'error')
        self.service.configure({'id': source['id'], 'delete': True})
        self.assertEqual(len(self.service.state()['sources']), 1)
        with self.assertRaises(ImportFailure):
            self.service.state({'page': 0})

    def test_restart_pagination_and_line_budget(self):
        self.log.write_text('INFO fictional\n' * 55, encoding='utf8')
        self.configure()
        self.service.MAX_LINES = 30
        self.assertEqual(self.service.poll()['total'], 30)
        restarted = LogService(self.root / 'runtime')
        result = restarted.poll()
        self.assertEqual(result['total'], 55)
        self.assertEqual(len(result['events']), 50)
        self.assertEqual(len(restarted.state({'page': 2})['events']), 5)
        self.assertEqual(restarted.poll()['total'], 55)

    def test_utf16_partial_character_and_invalid_bytes(self):
        data = codecs.BOM_UTF16_LE + 'INFO 中文\n'.encode('utf-16-le')
        self.log.write_bytes(data[:-3])
        self.configure(encoding='utf-16')
        self.assertEqual(self.service.poll()['total'], 0)
        with self.log.open('ab') as stream:
            stream.write(data[-3:])
        self.assertEqual(self.service.poll()['total'], 1)
        self.assertEqual(self.service.poll()['total'], 1)
        self.log.write_bytes(b'\xff\n')
        self.assertEqual(self.service.poll()['sources'][0]['status'], 'error')

    def test_levels_are_explicit_and_filter_is_case_insensitive(self):
        self.log.write_text('普通消息\nwarning 虚构警告\nerror 虚构失败\n', encoding='utf8')
        self.configure()
        self.service.poll()
        self.assertEqual(self.service.state({'level': 'unknown'})['events'][0]['message'], '普通消息')
        self.assertEqual(self.service.state({'level': 'warn'})['total'], 1)
        self.assertEqual(self.service.state({'level': 'error'})['total'], 1)
        for bad in ('warning', 'TRACE', 'any'):
            with self.assertRaises(ImportFailure):
                self.service.state({'level': bad})

    def test_config_text_limits_nul_empty_label_and_unicode_path(self):
        for extra in ({'label': ''}, {'label': '   '}, {'label': 'x' * 201}, {'path': str(self.log) + '\x00'}, {'path': 'x' * 4097}, {'rotationGlob': 'x' * 256}, {'id': 'x' * 101}):
            body = dict(software='material', path=str(self.log))
            body.update(extra)
            with self.assertRaises(ImportFailure):
                self.service.configure(body)
        unicode_log = self.root / '虚构日志.log'
        unicode_log.write_text('中文消息\n', encoding='utf8')
        self.service.configure(dict(software='editing', label='虚构剪辑日志', path=str(unicode_log), enabled=True))
        self.assertEqual(self.service.poll()['events'][0]['message'], '中文消息')

    def test_summary_queries_only_selected_trusted_tasks(self):
        self.log.write_text('INFO taskId=task-fiction\nINFO taskId=other\nplain line\n', encoding='utf8')
        self.configure()
        self.service.poll()
        self.assertEqual(self.service.summary(['task-fiction'])['tasks']['task-fiction']['count'], 1)
        self.assertEqual(self.service.summary(['other'])['tasks'], {})
        self.assertEqual(self.service.summary([])['tasks'], {})

    def test_local_text_log_paths_and_rotation_exclude_other_formats(self):
        for path in (r'\\server\share\app.log', r'\\?\C:\app.log', r'\\.\C:\app.log', '//server/share/app.log', str(self.root / 'video.mp4'), str(self.root / 'business.sqlite3'), str(self.root / 'program.exe')):
            with self.assertRaises(ImportFailure):
                self.service.configure(dict(software='material', path=path))
        self.log.write_text('INFO main\n', encoding='utf8')
        (self.root / 'app.log.1').write_text('INFO rotated\n', encoding='utf8')
        (self.root / 'app.log.sqlite3').write_bytes(b'\xffbinary business db')
        (self.root / 'app.log.mp4').write_bytes(b'\xffbinary video')
        self.configure(rotationGlob='app.log*')
        result = self.service.poll()
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['sources'][0]['status'], 'ok')
        text_log = self.root / '虚构线程日志.txt'
        text_log.write_text('INFO thread\n', encoding='utf8')
        self.service.configure(dict(software='publish', path=str(text_log), enabled=True))
        self.assertEqual(self.service.poll()['total'], 3)

    def test_touch_is_idempotent_and_small_middle_rewrite_detected(self):
        original = 'INFO ' + 'a' * 600 + '\n'
        self.log.write_text(original, encoding='utf8')
        self.configure()
        self.assertEqual(self.service.poll()['total'], 1)
        stat = self.log.stat()
        os.utime(self.log, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
        self.assertEqual(self.service.poll()['total'], 1)
        # Preserve head/tail, size and even timestamp: the bounded whole-file
        # fingerprint must still detect a middle rewrite of this small file.
        altered = original[:350] + 'b' + original[351:]
        self.log.write_text(altered, encoding='utf8')
        os.utime(self.log, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
        self.assertEqual(self.service.poll()['total'], 2)
        self.assertEqual(self.service.poll()['total'], 2)


if __name__ == '__main__':
    unittest.main()
