"""006B waiting-file regressions; all files and servers are temporary fiction."""
from unittest.mock import patch
import unittest
import test_logs
import test_logs_http


class WaitingFileTests(unittest.TestCase):
    setUp = test_logs.LogTests.setUp
    configure = test_logs.LogTests.configure
    def test_missing_directory_is_waiting_without_creating_source(self):
        self.log = self.root / 'software-not-started' / 'logs' / 'app.log'
        self.configure()
        for _ in range(2):
            result = self.service.poll()
            source = result['sources'][0]
            self.assertEqual(source['status'], 'waiting_file')
            self.assertEqual(source['error'], '')
            self.assertTrue(source['note'])
            self.assertIsNone(source['lastReadAt'])
            self.assertEqual(result['total'], 0)
            self.assertFalse(self.log.parent.exists())

    def test_appearance_disappearance_and_restart_recover_without_duplicates(self):
        self.configure()
        self.assertEqual(self.service.poll()['sources'][0]['status'], 'waiting_file')
        original = b'INFO fictional newly generated\n'
        self.log.write_bytes(original)
        result = self.service.poll()
        self.assertEqual(result['sources'][0]['status'], 'ok')
        self.assertEqual(result['sources'][0]['note'], '')
        self.assertTrue(result['sources'][0]['lastReadAt'])
        self.assertEqual(self.service.poll()['total'], 1)
        last_read = self.service.state()['sources'][0]['lastReadAt']
        rotated = self.root / 'kept.log'
        self.log.rename(rotated)
        source = self.service.poll()['sources'][0]
        self.assertEqual(source['status'], 'waiting_file')
        self.assertEqual(source['lastReadAt'], last_read)
        rotated.rename(self.log)
        self.assertEqual(self.service.poll()['total'], 1)
        self.assertEqual(self.log.read_bytes(), original)

    def test_missing_base_keeps_existing_rotation_and_recovers(self):
        rotation = self.root / 'app.log.1'
        original = b'INFO fictional rotated\n'
        rotation.write_bytes(original)
        self.configure(rotationGlob='app.log*')
        result = self.service.poll()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['sources'][0]['status'], 'waiting_file')
        self.assertEqual(result['sources'][0]['error'], '')
        self.assertTrue(result['sources'][0]['lastReadAt'])
        self.log.write_bytes(b'INFO fictional active\n')
        self.assertEqual(self.service.poll()['total'], 2)
        self.assertEqual(self.service.poll()['sources'][0]['status'], 'ok')
        self.assertEqual(rotation.read_bytes(), original)

    def test_rotation_disappears_during_read_without_blocking_remaining_files(self):
        self.log.write_bytes(b'INFO fictional active\n')
        rotation = self.root / 'app.log.1'
        rotation.write_bytes(b'INFO fictional disappearing\n')
        self.configure(rotationGlob='app.log*')
        read = self.service._read

        def disappearing(db, source, file, *args):
            if file == rotation:
                raise FileNotFoundError('fictional rotation race')
            return read(db, source, file, *args)

        with patch.object(self.service, '_read', side_effect=disappearing):
            result = self.service.poll()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['sources'][0]['status'], 'waiting_file')
        self.assertEqual(result['sources'][0]['error'], '')

    def test_permission_and_encoding_failures_remain_error_disabled_stays_disabled(self):
        self.log.write_bytes(b'INFO fictional\n')
        self.configure()
        with patch.object(self.service, '_read', side_effect=PermissionError('fictional access denied')):
            source = self.service.poll()['sources'][0]
        self.assertEqual(source['status'], 'error')
        self.assertIn('access denied', source['error'])
        self.assertIsNone(source['lastReadAt'])
        self.log.write_bytes(b'\xff\n')
        self.assertEqual(self.service.poll()['sources'][0]['status'], 'error')
        disabled = self.service.configure(dict(software='editing', path=str(self.root / 'absent.log')))['source']
        source = next(s for s in self.service.poll()['sources'] if s['id'] == disabled['id'])
        self.assertEqual(source['status'], 'disabled')
        self.assertIsNone(source['lastReadAt'])


class WaitingFileHttpTests(unittest.TestCase):
    setUp = test_logs_http.LogsHttpTests.setUp
    stop = test_logs_http.LogsHttpTests.stop
    request = test_logs_http.LogsHttpTests.request
    config = test_logs_http.LogsHttpTests.config
    def test_waiting_file_dto_and_automatic_poll_recovery(self):
        path = self.root / 'fiction.log'
        self.assertEqual(self.request('/api/logs/configure', self.config())[0], 200)
        status, result = self.request('/api/logs/poll', {})
        self.assertEqual(status, 200)
        source = result['sources'][0]
        self.assertEqual(source['status'], 'waiting_file')
        self.assertEqual(source['error'], '')
        self.assertTrue(source['note'])
        self.assertIsNone(source['lastReadAt'])
        self.assertFalse(path.exists())
        path.write_bytes(b'INFO fictional recovery\n')
        self.assertEqual(self.request('/api/logs/poll', {})[1]['total'], 1)
        result = self.request('/api/logs/poll', {})[1]
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['sources'][0]['status'], 'ok')
        self.assertEqual(result['sources'][0]['note'], '')
        self.assertTrue(result['sources'][0]['lastReadAt'])


if __name__ == '__main__':
    unittest.main()
