"""Bounded, read-only discovery. No source config, startup or business calls."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import threading
from contextlib import closing

from .importers import ImportFailure

_PICKER_LOCK = threading.Lock()


def choose_local_path(kind='directory', title='选择本地文件或目录'):
    """Windows built-in dialog; does not require bundled Python Tcl/Tk."""
    if kind not in ('directory', 'taskWorkbook'):
        raise ImportFailure('local_picker', '不支持的本地选择类型。')
    if os.name != 'nt':
        raise ImportFailure('local_picker', '此电脑无法打开Windows选择窗口，请手动填写本地绝对路径。')
    # Titles pass as environment data, never interpolated into PowerShell code.
    script = """[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog=$null
try {
if ($env:CONSOLE_PICKER_KIND -eq 'directory') {
$dialog=New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description=$env:CONSOLE_PICKER_TITLE
$dialog.ShowNewFolderButton=$false
} else {
$dialog=New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title=$env:CONSOLE_PICKER_TITLE
$dialog.Filter='Excel workbook (*.xlsx)|*.xlsx'
$dialog.CheckFileExists=$true
$dialog.Multiselect=$false
}
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
if ($env:CONSOLE_PICKER_KIND -eq 'directory') {$dialog.SelectedPath} else {$dialog.FileName}
}
} finally {if ($null -ne $dialog) {$dialog.Dispose()}}
"""
    try:
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        with _PICKER_LOCK:
            result = subprocess.run([str(powershell), '-NoProfile', '-STA', '-WindowStyle', 'Hidden', '-Command', script],
                capture_output=True, encoding='utf-8', errors='replace', check=True,
                env={**os.environ, 'CONSOLE_PICKER_KIND': kind, 'CONSOLE_PICKER_TITLE': str(title)[:200]},
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.stdout.strip().lstrip('\ufeff')
    except (OSError, subprocess.SubprocessError) as error:
        raise ImportFailure('local_picker', '无法打开本地选择窗口，请手动填写本地绝对路径。') from error


class DiscoveryService:
    MAX_DIRECTORIES = 120
    MAX_DEPTH = 3
    EXCLUDED = {'.git', 'tests', 'test', 'src', 'node_modules', '.venv', 'vendor', '_internal', 'build'}

    def __init__(self, store, *, roots=None, health_reader=None):
        self.store = store
        # Deliberately ignore DOUYIN_MATERIAL_HOME: it can be a source test override.
        local = os.environ.get('LOCALAPPDATA')
        self.roots = list(roots) if roots is not None else ([Path(local) / 'DouyinSelector'] if local else [])
        self.health_reader = health_reader or self._health
        self._candidates = {}
        self._lock = threading.RLock()

    @staticmethod
    def _linked_path(path):
        """Reject links and Windows junction/reparse points in any ancestor."""
        return any(part.is_symlink() or bool(getattr(part.lstat(), 'st_file_attributes', 0)
                   & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))
                   for part in (path, *path.parents))

    @staticmethod
    def _compatible_database(database):
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
            connection.execute('PRAGMA query_only=ON')
            columns = {row[1] for row in connection.execute('PRAGMA table_info(download_jobs)')}
            required = {'task_id', 'started_at', 'material_ids_json', 'items_json', 'state', 'failed'}
            if not required.issubset(columns):
                return False
            # Prepare the same rowid requirement as MaterialService._jobs;
            # LIMIT 0 inspects compatibility without fetching business rows.
            connection.execute('SELECT rowid FROM download_jobs LIMIT 0')
            return True

    @staticmethod
    def _identity(database, marker):
        info = database.stat()
        # DB bytes may grow while the original software runs; filesystem identity
        # (rather than size/mtime) detects replacement without reading business data.
        return (str(database.resolve()), info.st_dev, info.st_ino,
                hashlib.sha256(marker.read_bytes()).hexdigest() if marker.is_file() and not marker.is_symlink() and marker.stat().st_size <= 8192 else '')

    def select(self, body, material):
        if not isinstance(body, dict) or not isinstance(body.get('candidateId'), str):
            raise ImportFailure('discovery_selection', '请明确选择一个已验证的软件实例。')
        with self._lock:
            cached = self._candidates.get(body['candidateId'])
            if not cached or not cached['candidate']['eligible']:
                raise ImportFailure('discovery_selection', '候选未验证或已过期，请重新识别后选择。')
            database, marker = cached['database'], cached['marker']
            try:
                if self._linked_path(database) or self._linked_path(marker) or self._identity(database, marker) != cached['identity']:
                    raise ImportFailure('discovery_selection', '软件数据或运行标记已变化，请重新识别。')
                candidate = self._candidate(database, marker)
                if not candidate['eligible']:
                    raise ImportFailure('discovery_selection', '软件实例复核失败，请重新识别。')
                if self._linked_path(database) or self._linked_path(marker) or self._identity(database, marker) != cached['identity']:
                    raise ImportFailure('discovery_selection', '复核期间软件数据或运行标记已变化，请重新识别。')
            except OSError as error:
                raise ImportFailure('discovery_selection', '软件数据已不可读，请重新识别。') from error
            return material.settings(candidate['port'], candidate['database'])

    @staticmethod
    def _health(port):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
        try:
            connection.request('GET', '/api/health')
            response = connection.getresponse()
            raw = response.read(256 * 1024 + 1)
            if response.status != 200 or len(raw) > 256 * 1024:
                return {}
            value = json.loads(raw)
            return value.get('data', {}) if isinstance(value, dict) and value.get('ok') is True else {}
        finally:
            connection.close()

    def scan(self, body=None):
        body = {} if body is None else body
        if not isinstance(body, dict):
            raise ImportFailure('discovery_path', '识别参数必须是对象。')
        roots = list(self.roots)
        if body.get('directory'):
            value = body['directory']
            if not isinstance(value, str) or value.startswith(('\\\\', '//')) or '\x00' in value:
                raise ImportFailure('discovery_path', '请选择本机软件目录。')
            selected = Path(value)
            if not selected.is_absolute() or not selected.is_dir():
                raise ImportFailure('discovery_path', '请选择已存在的本机软件目录。')
            roots.insert(0, selected)
        warnings, candidates, seen = [], [], set()
        queue = [(Path(root), 0) for root in roots]
        count = 0
        while queue and count < self.MAX_DIRECTORIES:
            folder, depth = queue.pop(0)
            try:
                if not folder.is_dir() or self._linked_path(folder):
                    continue
                key = str(folder.resolve())
                if key in seen:
                    continue
                seen.add(key)
                count += 1
                database = folder / 'db' / 'material.db'
                if not database.is_file():
                    database = folder / 'material.db'
                marker = folder / 'runtime.json'
                if database.is_file() and not self._linked_path(database):
                    candidates.append(self._candidate(database, marker))
                if depth < self.MAX_DEPTH:
                    for child in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
                        if child.name.casefold() not in self.EXCLUDED | {'db'} and not child.name.startswith('.') and child.is_dir() and not self._linked_path(child):
                            queue.append((child, depth + 1))
            except OSError:
                warnings.append('部分选定目录无法读取，已跳过。')
        if queue:
            warnings.append('已达到有限扫描范围，请直接选择软件数据目录。')
        if not candidates:
            warnings.append('未找到素材数据库；请在原软件核对数据目录后选择，不会读取源码测试配置。')
        with self._lock:
            self._candidates = {}
            for candidate in candidates:
                database = Path(candidate['database'])
                marker = database.parent.parent / 'runtime.json' if database.parent.name == 'db' else database.parent / 'runtime.json'
                try:
                    self._candidates[candidate['id']] = {'candidate': candidate, 'database': database,
                        'marker': marker, 'identity': self._identity(database, marker)}
                except OSError:
                    candidate.update(eligible=False, status='unverified')
        return {'ok': True, 'candidates': candidates, 'warnings': list(dict.fromkeys(warnings))}

    def _candidate(self, database, marker):
        result = {'id': hashlib.sha256(str(database.resolve()).encode()).hexdigest()[:20],
                  'name': '素材任务管理器', 'port': None, 'database': str(database.resolve()),
                  'version': None, 'status': 'unverified', 'eligible': False, 'evidence': ['发现本地material.db，尚未验证运行实例。']}
        try:
            if self._linked_path(database) or not marker.is_file() or self._linked_path(marker) or marker.stat().st_size > 8192:
                return result
            try:
                compatible = self._compatible_database(database)
            except sqlite3.Error:
                compatible = False
            if not compatible:
                result['evidence'].append('数据库不是兼容的SQLite素材报告库，download_jobs结构核对未通过。')
                return result
            result['evidence'].append('只读核对download_jobs报告结构通过；未读取业务记录。')
            runtime = json.loads(marker.read_text(encoding='utf-8-sig'))
            if (not isinstance(runtime, dict) or type(runtime.get('port')) is not int or not 1 <= runtime['port'] <= 65535
                or type(runtime.get('pid')) is not int or runtime['pid'] <= 0
                or not isinstance(runtime.get('version'), str) or type(runtime.get('frozen')) is not bool):
                return result
            result.update(port=runtime['port'], version=runtime['version'])
            result['evidence'].append('运行标记：' + str(marker.resolve()))
            health = self.health_reader(runtime['port'])
            required = {'/api/integration/task-table', '/api/task-table/row/materials'}
            if (isinstance(health, dict) and health.get('service') == 'minimal-api'
                and health.get('build_id') == 'plan011-20261009-r1'
                and health.get('version') == runtime['version'] and health.get('frozen') is runtime['frozen']
                and isinstance(health.get('endpoints'), list) and required.issubset(set(x for x in health['endpoints'] if isinstance(x, str)))):
                result.update(status='verified', eligible=True)
                result['evidence'].append('本机/api/health身份、版本与交接能力符合已核对协议；待用户选中保存。')
            else:
                result['evidence'].append('运行标记存在，但服务身份或交接能力未通过验证。')
        except (OSError, ValueError, http.client.HTTPException):
            result['evidence'].append('运行标记或本机只读健康检查不可用，不能作为已连接实例。')
        return result
