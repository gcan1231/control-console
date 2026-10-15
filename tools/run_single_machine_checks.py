"""Run the fictional single-machine suite; never connect to the live workspace."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent


def catalog(python, node, suite):
    files = sorted((ROOT / 'tests').glob('*.test.mjs'))
    browsers = [p for p in files if 'browser' in p.name]
    units = [p for p in files if p not in browsers]
    result = []
    if suite in ('all', 'backend'):
        result.append(('backend', [python, '-B', '-m', 'unittest', 'discover', '-s', 'tests']))
    if suite in ('all', 'unit'):
        result.append(('node-unit', [node, '--test', *[str(p.relative_to(ROOT)) for p in units]]))
    if suite in ('all', 'browser'):
        result.extend((p.stem, [node, str(p.relative_to(ROOT))]) for p in browsers)
    return result


def stop_owned(process):
    """Terminate only the process tree this runner has just started."""
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def preview(python, env, log):
    source = """import sys,threading
from http.server import ThreadingHTTPServer
from tools.preview_server import PreviewHandler
s=ThreadingHTTPServer(('127.0.0.1',0),PreviewHandler)
t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
print(s.server_port,flush=True)
try: sys.stdin.readline()
finally: s.shutdown();s.server_close();t.join()
"""
    process = subprocess.Popen([python, '-B', '-u', '-c', source], cwd=ROOT, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                               start_new_session=os.name != 'nt')
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
    try:
        port = int(lines.get(timeout=15).strip())
        if not 1 <= port <= 65535 or port == 8861:
            raise ValueError('Unexpected preview port')
    except (queue.Empty, ValueError):
        stop_owned(process)
        process.stdout.close()
        raise RuntimeError('Could not start an isolated read-only preview') from None
    except BaseException:
        stop_owned(process)
        process.stdout.close()
        raise
    return process, f'http://127.0.0.1:{port}'


def main():
    # Keep Chinese report paths readable in Windows shells and captured logs.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('all', 'backend', 'unit', 'browser'), default='all')
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--node', default=shutil.which('node') or 'node')
    parser.add_argument('--timeout', type=int, default=600, help='Seconds allowed per suite/script')
    parser.add_argument('--list', action='store_true', help='List commands without starting tests')
    args = parser.parse_args()
    commands = catalog(args.python, args.node, args.suite)
    if args.list:
        print(json.dumps([{'id': name, 'command': cmd} for name, cmd in commands], ensure_ascii=False, indent=2))
        return 0
    if not 30 <= args.timeout <= 3600:
        parser.error('--timeout must be between 30 and 3600 seconds')
    folder = ROOT / 'test-results' / 'single-machine' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    folder.mkdir(parents=True)
    env = dict(os.environ, PYTHON=args.python, PYTHONDONTWRITEBYTECODE='1',
               PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    env.pop('DEMO_URL', None)
    if not env.get('PLAYWRIGHT_MODULE'):
        candidates = [ROOT / 'node_modules' / 'playwright',
                      Path(args.python).resolve().parent.parent / 'node' / 'node_modules' / 'playwright']
        for candidate in candidates:
            if candidate.is_dir():
                env['PLAYWRIGHT_MODULE'] = str(candidate)
                break
    report = {'startedAt': datetime.now().astimezone().isoformat(), 'suite': args.suite,
              'python': args.python, 'node': args.node, 'results': [],
              'boundary': 'Fictional fixtures and isolated loopback servers only; no real business execution.'}
    report_path = folder / 'summary.json'
    for name, command in commands:
        print(f'Running {name}...', flush=True)
        started = time.monotonic()
        record = {'id': name, 'command': command, 'log': str(folder / (name + '.txt'))}
        server = None
        process = None
        try:
            with (folder / (name + '.txt')).open('wb') as output, (folder / (name + '-preview.txt')).open('wb') as server_log:
                current_env = env.copy()
                if name == 'browser.test':
                    server, current_env['DEMO_URL'] = preview(args.python, current_env, server_log)
                process = subprocess.Popen(command, cwd=ROOT, env=current_env,
                                           stdout=output, stderr=subprocess.STDOUT,
                                           start_new_session=os.name != 'nt')
                try:
                    record['exitCode'] = process.wait(timeout=args.timeout)
                except subprocess.TimeoutExpired:
                    record.update(exitCode=124, error='Test timeout; owned process tree stopped')
                    stop_owned(process)
        except (OSError, RuntimeError) as error:
            record.update(exitCode=1, error=str(error))
        except KeyboardInterrupt:
            if process is not None:
                stop_owned(process)
            record.update(exitCode=130, error='Interrupted by user')
        finally:
            if server is not None:
                try:
                    server.stdin.close()
                    server.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    stop_owned(server)
                finally:
                    server.stdout.close()
            record['seconds'] = round(time.monotonic() - started, 3)
            path = Path(record['log'])
            if path.exists():
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 128 * 1024))
                    tail = stream.read().decode('utf-8', errors='replace')
                match = re.search(r'Ran (\d+) tests? in', tail) if name == 'backend' else re.search(r'# tests (\d+)', tail)
                if match:
                    record['testCount'] = int(match[1])
            report['results'].append(record)
            report['ok'] = all(r['exitCode'] == 0 for r in report['results'])
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'{name}: exit {record["exitCode"]} ({record["seconds"]}s)', flush=True)
        if record['exitCode'] == 130:
            break
    report['finishedAt'] = datetime.now().astimezone().isoformat()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Report: {report_path}', flush=True)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
