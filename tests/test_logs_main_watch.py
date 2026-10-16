"""Exercise main's real three-second watcher with isolated fictional data."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from tools.run_single_machine_checks import stop_owned


class MainLogWatcherTests(unittest.TestCase):
    def test_missing_file_appears_and_is_collected_without_post_poll(self):
        with tempfile.TemporaryDirectory(prefix='console-main-log-watch-') as directory:
            root = Path(directory)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            self.assertNotEqual(port, 8861)
            with (root / 'server-output.txt').open('wb') as output:
                process = subprocess.Popen(
                    [sys.executable, '-B', '-u', 'tools/local_server.py', '--port', str(port),
                     '--runtime-dir', str(root / 'runtime')],
                    cwd=Path(__file__).resolve().parents[1], stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=os.name != 'nt')
                try:
                    def request(route, body=None, token=None):
                        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                        try:
                            connection.request('GET' if body is None else 'POST', route,
                                               None if body is None else json.dumps(body),
                                               {'Content-Type': 'application/json', 'X-Console-Token': token or ''})
                            response = connection.getresponse()
                            self.assertEqual(response.status, 200)
                            return json.loads(response.read())
                        finally:
                            connection.close()

                    def await_result(route, predicate, timeout=12):
                        deadline = time.monotonic() + timeout
                        last = None
                        while time.monotonic() < deadline:
                            self.assertIsNone(process.poll(), 'Our temporary main server exited')
                            try:
                                last = request(route)
                            except (ConnectionError, OSError):
                                time.sleep(0.1)
                                continue
                            if predicate(last):
                                return last
                            time.sleep(0.1)
                        self.fail(f'Temporary watcher did not reach expected state: {last!r}')

                    health = await_result('/api/health', lambda result: result.get('token'))
                    # Verify the endpoint belongs to our isolated runtime before configuring.
                    self.assertEqual(request('/api/logs/state')['sources'], [])
                    log = root / 'fiction.log'
                    request('/api/logs/configure', dict(software='editing', path=str(log), enabled=True), health['token'])
                    waiting = await_result('/api/logs/state', lambda result: result['sources'][0]['status'] == 'waiting_file')
                    self.assertIsNone(waiting['sources'][0]['lastReadAt'])
                    self.assertFalse(log.exists())
                    original = b'INFO fictional main watcher recovery\n'
                    log.write_bytes(original)
                    recovered = await_result('/api/logs/state', lambda result: result['total'] == 1 and result['sources'][0]['status'] == 'ok')
                    self.assertEqual(recovered['sources'][0]['error'], '')
                    self.assertEqual(recovered['sources'][0]['note'], '')
                    previous_read = recovered['sources'][0]['lastReadAt']
                    next_round = await_result('/api/logs/state', lambda result: result['sources'][0]['lastReadAt'] != previous_read)
                    self.assertEqual(next_round['total'], 1)
                    self.assertEqual(log.read_bytes(), original)
                finally:
                    # Include the Windows venv launcher's child holding the output file.
                    # Stop only this Popen tree, never a discovered PID.
                    stop_owned(process)
            # Windows may release the terminated child's inherited handle just after
            # taskkill returns. Retry only our temporary output file, with a bound.
            deadline = time.monotonic() + 5
            while True:
                try:
                    (root / 'server-output.txt').unlink()
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)


if __name__ == '__main__':
    unittest.main()
