"""007 Windows PATH-isolation package checks; never a clean-VM certification.

Only starts and terminates its own process trees; all fixtures are fictional.
"""
from __future__ import annotations
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    args = parser.parse_args()
    source = args.package.resolve()
    archive = Path(str(source) + '.zip')
    report_dir = ROOT / 'test-results' / ('便携包 隔离验证 ' + time.strftime('%Y%m%d-%H%M%S'))
    report_dir.mkdir(parents=True)
    summary = {'scope': 'Windows PATH isolation on development host, not clean Windows VM', 'source': str(source), 'checks': [], 'ok': False}
    processes = []
    handles = []

    def check(name, detail=None):
        summary['checks'].append({'name': name, 'ok': True, 'detail': detail})
        print('PASS ' + name, flush=True)

    def stop(process):
        if process.poll() is None:
            subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'), '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=20)
            process.wait(timeout=20)

    try:
        with zipfile.ZipFile(archive) as zipped:
            for item in zipped.infolist():
                assert not Path(item.filename).is_absolute() and '..' not in Path(item.filename).parts
            zipped.extractall(report_dir)
        package = report_dir / source.name
        python = package / '_runtime/python.exe'
        env = dict(os.environ, PATH=str(Path(os.environ['SystemRoot']) / 'System32') + ';' + os.environ['SystemRoot'], PYTHONHOME=str(report_dir / '不存在 home'), PYTHONPATH=str(report_dir / '不存在 path'), PLAYWRIGHT_NODEJS_PATH=str(report_dir / '不存在 node.exe'), PYTHONUTF8='1')

        def run(arguments, expected=0):
            result = subprocess.run(arguments, cwd=package, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
            assert result.returncode == expected, (arguments, result.returncode, result.stdout, result.stderr)
            return result.stdout + result.stderr

        def py(code):
            return run([str(python), '-I', '-B', '-X', 'utf8', '-c', code])

        manifest = json.loads((package / 'package-manifest.json').read_text(encoding='utf-8'))
        forbidden = {'data', 'runtime', 'logs', 'pip', 'ensurepip', 'setuptools', 'wheel', 'test', 'tests', 'node_modules'}
        for item in manifest['files']:
            path = Path(item['path'])
            assert not set(path.parts) & forbidden or path.parts[:3] == ('app', 'demo', 'data'), str(path)
            assert path.suffix.lower() not in {'.xlsx', '.xls', '.sqlite', '.sqlite3', '.db', '.log', '.env'}, str(path)
        for library in (package / '_runtime').glob('python*.zip'):
            with zipfile.ZipFile(library) as stdlib:
                assert not any(set(Path(name).parts) & {'pip', 'ensurepip', 'venv', 'idlelib'} for name in stdlib.namelist())
        check('manifest excludes business data and development installers', len(manifest['files']))
        output = run([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', str(package / 'Start.cmd'), '--check'])
        assert '"ok": true' in output and '"isolated": true' in output, output
        check('Start.cmd --check with poisoned environment', output.strip())
        inspected = json.loads(py("import sys,json,sqlite3,ssl,ctypes,PIL,greenlet,playwright; print(json.dumps({'paths':sys.path,'modules':{m.__name__:m.__file__ for m in (sqlite3,ssl,ctypes,PIL,greenlet,playwright)},'isolated':sys.flags.isolated}))"))
        assert inspected['isolated']
        for path in inspected['paths'] + list(inspected['modules'].values()):
            assert Path(path).resolve().is_relative_to(package.resolve()), path
        check('runtime paths and dependencies confined to extracted package', inspected)
        run([str(python), '-I', '-B', '-m', 'tools.publisher_worker', '--help'])
        check('independent bundled worker help')

        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        assert port != 8861
        base = 'http://127.0.0.1:' + str(port)

        def request(path, data=None, token=None):
            headers = {'Content-Type': 'application/json', 'Origin': base}
            if token: headers['X-Console-Token'] = token
            req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as response:
                raw = response.read()
                return json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw

        def start():
            log = (report_dir / ('server-' + str(len(processes)) + '.log')).open('wb'); handles.append(log)
            process = subprocess.Popen([str(python), '-I', '-B', '-X', 'utf8', str(package / 'app/tools/portable_launcher.py'), '--port', str(port), '--no-browser'], cwd=package, env=env, stdout=log, stderr=log)
            processes.append(process)
            for _ in range(100):
                assert process.poll() is None, 'Server exited; inspect server log'
                try: return process, request('/api/health')['token']
                except OSError: time.sleep(.1)
            raise AssertionError('Server readiness timeout')

        process, token = start()
        module = next((package / 'app/demo/modules').glob('*.js')).name
        for route in ('/', '/app.js', '/modules/' + module, '/diagrams/architecture.html', '/diagrams/workflow.html'):
            assert len(request(route)) > 100, route
        check('HTTP application and bundled diagrams available', {'port': port})
        spec = importlib.util.spec_from_file_location('portable_fictional_fixture', ROOT / 'tests/test_local_import.py')
        fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
        content = fixture.workbook([fixture.HEADERS, fixture.task(account='portable-fictional', name='虚构便携账号')])
        imported = request('/api/import/commit', {'kind': 'tasks', 'filename': '虚构便携任务.xlsx', 'content': base64.b64encode(content).decode(), 'options': {}}, token)
        assert imported['ok'], imported
        tasks = request('/api/state')['tasks']; assert len(tasks) == 1, tasks
        task_id = tasks[0]['id']
        reviewed = request('/api/tasks/review', {'taskId': task_id, 'note': '虚构持久化检查', 'duplicateConfirmed': False}, token)
        assert reviewed['ok'], reviewed
        stop(process)
        process, token = start()
        tasks = request('/api/state')['tasks']
        assert len(tasks) == 1 and tasks[0]['id'] == task_id and tasks[0]['note'] == '虚构持久化检查', tasks
        check('fictional HTTP import and review persisted after restart', {'taskId': task_id})
        stop(process)
        runtime = package / 'data'
        setup = "from control_console.store import ConsoleStore; from control_console.publisher_execution import PublishExecutionService; s=ConsoleStore(" + repr(str(runtime)) + "); p=PublishExecutionService(s,None,launch_workers=False); r={'id':'portable-fictional-run','state':'queued','accounts':[{'accountId':'portable-fictional','state':'queued','control':'stop'}],'items':[{'id':'fictional-item','accountId':'portable-fictional','state':'queued'}]}; exec(\"with s.connection(write=True) as c: p._put(c,'run',r['id'],r)\")"
        py(setup)
        worker = subprocess.Popen([str(python), '-I', '-B', '-m', 'tools.publisher_worker', '--runtime', str(runtime), '--run-id', 'portable-fictional-run', '--account-id', 'portable-fictional'], cwd=package, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        processes.append(worker); stdout, stderr = worker.communicate(timeout=30)
        assert worker.returncode == 0, stderr.decode('utf-8', errors='replace')
        result = json.loads(py("import json; from control_console.store import ConsoleStore; from control_console.publisher_execution import PublishExecutionService; s=ConsoleStore(" + repr(str(runtime)) + "); p=PublishExecutionService(s,None,launch_workers=False); exec(\"with s.connection() as c: print(json.dumps(p._run(c,'portable-fictional-run')))\")"))
        assert result['state'] == 'stopped' and result['accounts'][0]['pid'] == worker.pid and result['items'][0]['state'] == 'stopped', result
        check('real independent worker executes stop fixture without browser access', result)
        log_output = py("from control_console.store import ConsoleStore; from control_console.publisher_execution import PublishExecutionService; s=ConsoleStore(" + repr(str(runtime)) + "); p=PublishExecutionService(s,None,launch_workers=False); exec(\"with s.connection(write=True) as c:\\n r=p._run(c,'portable-fictional-run'); r['items'][0]['taskId']='fictional-log-task'; p._put(c,'run',r['id'],r)\"); p._log('portable-fictional-run','portable-fictional','fictional-item','虚构便携日志持久化'); print(p._log_path('portable-fictional-run','portable-fictional'))").strip()
        logfile = Path(log_output)
        before = logfile.read_text(encoding='utf-8')
        process, token = start()
        assert logfile.read_text(encoding='utf-8') == before and '虚构便携日志持久化' in before
        stop(process)
        check('fictional UTF-8 execution log preserved through server restart', str(logfile.relative_to(package)))
        blocked_runtime = package / 'runtime-is-file'
        blocked_runtime.write_text('fictional path obstacle', encoding='utf-8')
        output = run([str(python), '-I', '-B', '-X', 'utf8', str(package / 'app/tools/portable_launcher.py'), '--runtime-dir', str(blocked_runtime), '--port', str(port), '--no-browser'], expected=1)
        assert 'Traceback' not in output and '启动失败' in output, output
        check('runtime path is file fails readably without orphaned process', output.strip())
        driver = package / '_runtime/site-packages/playwright/driver/node.exe'
        held = driver.with_suffix('.test-held')
        driver.rename(held)
        try:
            # Direct launcher avoids cmd pause while testing the same check routine.
            output = run([str(python), '-I', '-B', '-X', 'utf8', str(package / 'app/tools/portable_launcher.py'), '--check'], expected=1)
            assert '驱动缺失' in output and '完整解压' in output, output
            check('offline missing bundled driver has understandable diagnostic', output.strip())
        finally:
            held.rename(driver)
        summary['ok'] = True
    except Exception as error:
        summary['error'] = repr(error)
        print('FAIL ' + repr(error), flush=True)
    finally:
        for process in processes: stop(process)
        for handle in handles: handle.close()
        summary['reportDirectory'] = str(report_dir)
        (report_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
