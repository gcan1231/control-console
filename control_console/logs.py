"""Read-only, bounded local log ingestion into an isolated SQLite copy."""
from __future__ import annotations
import codecs
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pathlib import PureWindowsPath
from .importers import ImportFailure


class LogService:
    MAX_BYTES = 256 * 1024
    MAX_LINES = 2000
    MAX_FILES = 32
    LEVELS = ('DEBUG', 'INFO', 'WARN', 'ERROR', 'CRITICAL', 'FATAL', 'UNKNOWN')

    def __init__(self, runtime_dir, trusted_task_ids=None):
        self.root = Path(runtime_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'log-center.sqlite3'
        self.lock = threading.RLock()
        self.trusted_task_ids = trusted_task_ids or (lambda: set())
        with self._db() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, body TEXT); CREATE TABLE IF NOT EXISTS cursors(id TEXT PRIMARY KEY, body TEXT); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, body TEXT, retained TEXT);')
            db.execute("CREATE INDEX IF NOT EXISTS events_task_id ON events(json_extract(body,'$.taskId'))")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _log_file_name(path):
        """Allow explicit text-log extensions and their numbered rotations only."""
        return re.search(r'\.(?:log|txt)(?:\.\d+)?$', Path(path).name, re.I) is not None

    def configure(self, body):
        if not isinstance(body, dict):
            raise ImportFailure('log_config', '日志配置必须是对象')
        with self.lock, self._db() as db:
            sid = body.get('id') or uuid.uuid4().hex
            if not isinstance(sid, str) or len(sid) > 100:
                raise ImportFailure('log_config', '来源ID无效')
            for field, limit in (('id', 100), ('software', 20), ('path', 4096), ('encoding', 20), ('rotationGlob', 255), ('label', 200)):
                if field in body and (not isinstance(body[field], str) or len(body[field]) > limit or '\x00' in body[field]):
                    raise ImportFailure('log_config', f'{field}须为不含空字符且不超过{limit}字的文本')
            if body.get('delete') is True:
                db.execute('DELETE FROM sources WHERE id=?', (sid,))
                return {'ok': True, 'id': sid}
            software = body.get('software')
            path = body.get('path', '')
            encoding = body.get('encoding', 'auto')
            pattern = body.get('rotationGlob', '')
            if software not in ('material', 'editing', 'publish') or encoding not in ('utf-8', 'utf-16', 'auto'):
                raise ImportFailure('log_config', '软件或编码无效')
            try:
                windows = PureWindowsPath(path)
                local_path = not path.startswith(('\\\\', '//')) and (not windows.drive or re.fullmatch(r'[A-Za-z]:', windows.drive) is not None)
                valid_path = isinstance(path, str) and '\x00' not in path and local_path and Path(path).is_absolute() and '://' not in path and self._log_file_name(path) and (not Path(path).exists() or Path(path).is_file())
            except (OSError, ValueError):
                valid_path = False
            if not valid_path:
                raise ImportFailure('log_path', '仅允许人工确认的本机绝对.log/.txt日志路径及其数字轮转文件；不支持网络或设备路径')
            if not isinstance(pattern, str) or any(x in pattern for x in ('..', '**', '/', '\\', ':')):
                raise ImportFailure('log_rotation', '轮转模式必须是同目录文件名')
            if not isinstance(body.get('enabled', False), bool) or not isinstance(body.get('label', software), str):
                raise ImportFailure('log_config', '名称或启用参数无效')
            if not body.get('label', software).strip():
                raise ImportFailure('log_config', '日志来源名称不能为空')
            source = dict(id=sid, software=software, label=body.get('label', software)[:200], path=str(Path(path)), encoding=encoding, rotationGlob=pattern, enabled=body.get('enabled', False), status='waiting' if body.get('enabled', False) else 'disabled', error='', lastReadAt=None)
            db.execute('INSERT OR REPLACE INTO sources VALUES (?,?)', (sid, json.dumps(source)))
        return {'ok': True, 'source': source}

    def poll(self):
        with self.lock, self._db() as db:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            db.execute('DELETE FROM events WHERE retained < ?', (cutoff,))
            budget = self.MAX_BYTES * 4
            trusted = set(self.trusted_task_ids())
            for sid, raw in db.execute('SELECT id,body FROM sources').fetchall():
                source = json.loads(raw)
                if not source['enabled']:
                    continue
                source.update(status='ok', error='')
                try:
                    base = Path(source['path'])
                    files = [base]
                    if source['rotationGlob']:
                        files = []
                        # Iteration stops at the file cap; no recursive traversal.
                        for item in base.parent.glob(source['rotationGlob']):
                            if self._log_file_name(item) and item.is_file():
                                files.append(item)
                                if len(files) > self.MAX_FILES:
                                    raise ValueError('轮转文件超过读取上限，请缩小模式')
                        if base not in files:
                            files.append(base)
                    for file in files:
                        if budget <= 0:
                            source.update(status='limited', error='单轮读取预算已用完，下一轮继续')
                            break
                        used, limited = self._read(db, source, file, min(budget, self.MAX_BYTES), cutoff, trusted)
                        budget -= used
                        if limited:
                            source.update(status='limited', error='达到每文件字节或行数上限，下一轮继续；超长未完整行暂不导入')
                    source['lastReadAt'] = self._now()
                except (OSError, ValueError, UnicodeError) as exc:
                    source.update(status='error', error=str(exc)[:500])
                db.execute('UPDATE sources SET body=? WHERE id=?', (json.dumps(source), sid))
        return self.state()

    def _read(self, db, source, file, budget, cutoff, trusted):
        stat = file.stat()
        if not file.is_file():
            raise ValueError('日志来源必须是常规文件')
        key = f"{source['id']}:{stat.st_dev}:{stat.st_ino}"
        row = db.execute('SELECT body FROM cursors WHERE id=?', (key,)).fetchone()
        cursor = json.loads(row[0]) if row else dict(offset=0, line=0, head='', headSize=0)
        with file.open('rb') as handle:
            previous_head = handle.read(cursor['headSize'])
            reset = stat.st_size < cursor['offset'] or hashlib.sha256(previous_head).hexdigest() != cursor.get('head', '')
            if cursor.get('tailSize'):
                handle.seek(max(0, cursor['offset'] - cursor['tailSize']))
                reset = reset or hashlib.sha256(handle.read(cursor['tailSize'])).hexdigest() != cursor.get('tail')
            # Metadata-only touches are not rebuilds. Small completed files can
            # be compared in full within the same fixed byte bound. Larger
            # files use bounded head/tail fingerprints; middle-only rewrites
            # outside those fingerprints cannot be detected without more IO.
            if cursor.get('wholeSize') == stat.st_size and stat.st_size <= self.MAX_BYTES:
                handle.seek(0)
                reset = reset or hashlib.sha256(handle.read(self.MAX_BYTES)).hexdigest() != cursor.get('wholeHash')
            if reset:
                cursor = dict(offset=0, line=0, head='', headSize=0)
            handle.seek(0)
            marker = handle.read(3)
            encoding = source['encoding']
            bom = 0
            if marker.startswith(codecs.BOM_UTF16_LE):
                encoding, bom = 'utf-16-le', 2
            elif marker.startswith(codecs.BOM_UTF16_BE):
                encoding, bom = 'utf-16-be', 2
            elif encoding == 'utf-16':
                raise ValueError('UTF-16日志缺少BOM，无法确认字节序')
            else:
                encoding = 'utf-8'
                bom = 3 if marker.startswith(codecs.BOM_UTF8) else 0
            cursor['offset'] = max(cursor['offset'], bom)
            handle.seek(cursor['offset'])
            data = handle.read(budget)
            decoder = codecs.getincrementaldecoder(encoding)(errors='strict')
            decoded = decoder.decode(data, final=False)
            lines = decoded.splitlines(keepends=True)
            consumed = 0
            count = 0
            now = self._now()
            for line in lines:
                if not line.endswith('\n') or count >= self.MAX_LINES:
                    break
                consumed += len(line.encode(encoding))
                count += 1
                cursor['line'] += 1
                message = line.rstrip('\r\n')
                stamp = re.search(r'\b(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?)', message)
                event_time = None
                if stamp:
                    try:
                        parsed = datetime.fromisoformat(stamp[1].replace('Z', '+00:00'))
                        event_time = parsed.astimezone(timezone.utc).isoformat()
                    except ValueError:
                        pass
                retained = event_time or datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
                if retained < cutoff:
                    continue
                level = re.search(r'\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\b', message, re.I)
                task = re.search(r'\btaskId[=:]\s*["\x27]?([\w-]+)', message)
                task_id = task[1] if task and task[1] in trusted else None
                event = dict(software=source['software'], sourceId=source['id'], file=str(file), line=cursor['line'], time=event_time, observedAt=now, level=(level[1].upper().replace('WARNING', 'WARN') if level else 'UNKNOWN'), message=message, taskId=task_id, association='task' if task_id else 'software')
                db.execute('INSERT INTO events(body,retained) VALUES (?,?)', (json.dumps(event), min(retained, now)))
            cursor['offset'] += consumed
            cursor['headSize'] = min(stat.st_size, 256)
            handle.seek(0)
            cursor['head'] = hashlib.sha256(handle.read(cursor['headSize'])).hexdigest()
            cursor['tailSize'] = min(cursor['offset'], 128)
            handle.seek(cursor['offset'] - cursor['tailSize'])
            cursor['tail'] = hashlib.sha256(handle.read(cursor['tailSize'])).hexdigest()
            cursor['mtime'] = stat.st_mtime_ns
            if stat.st_size == cursor['offset'] and stat.st_size <= self.MAX_BYTES:
                handle.seek(0)
                cursor['wholeSize'] = stat.st_size
                cursor['wholeHash'] = hashlib.sha256(handle.read(self.MAX_BYTES)).hexdigest()
            else:
                cursor.pop('wholeSize', None)
                cursor.pop('wholeHash', None)
            db.execute('INSERT OR REPLACE INTO cursors VALUES (?,?)', (key, json.dumps(cursor)))
            return len(data), cursor['offset'] < stat.st_size

    def state(self, query=None):
        query = {} if query is None else dict(query) if isinstance(query, dict) else query
        if not isinstance(query, dict):
            raise ImportFailure('log_query', '日志筛选必须是对象')
        for field in ('software', 'level', 'taskId', 'search'):
            if field in query and not isinstance(query[field], str):
                raise ImportFailure('log_query', '日志筛选必须是文本')
        if query.get('software') and query['software'] not in ('material', 'editing', 'publish'):
            raise ImportFailure('log_query', '软件筛选无效')
        if query.get('level'):
            query['level'] = query['level'].upper()
            if query['level'] not in self.LEVELS:
                raise ImportFailure('log_query', '日志级别筛选无效')
        try:
            page = int(query.get('page', 1))
            if page < 1 or page > 1000000:
                raise ValueError()
        except (TypeError, ValueError):
            raise ImportFailure('log_query', '页码必须是正整数')
        with self.lock, self._db() as db:
            sources = [json.loads(x[0]) for x in db.execute('SELECT body FROM sources')]
            clauses, params = [], []
            for field in ('software', 'level', 'taskId'):
                if query.get(field):
                    clauses.append(f"json_extract(body,'$.{field}')=?")
                    params.append(query[field])
            if query.get('search'):
                clauses.append("instr(lower(json_extract(body,'$.message')),lower(?))>0")
                params.append(str(query['search'])[:500])
            where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
            total = db.execute('SELECT count(*) FROM events' + where, params).fetchone()[0]
            events = [dict(json.loads(raw), id=eid) for eid, raw in db.execute('SELECT id,body FROM events' + where + ' ORDER BY id DESC LIMIT 50 OFFSET ?', params + [(page - 1) * 50])]
        return dict(ok=True, sources=sources, events=events, total=total, page=page, pageSize=50, updatedAt=self._now())

    def summary(self, task_ids=None):
        trusted = set(self.trusted_task_ids())
        selected = trusted if task_ids is None else trusted.intersection(task_ids)
        tasks = {}
        if not selected:
            return dict(ok=True, tasks=tasks)
        with self.lock, self._db() as db:
            # Query only selected trusted tasks; software-only rows never enter Python.
            # One query per task also avoids SQLite's bind-variable limit for large sets.
            for tid in selected:
                for eid, raw in db.execute("SELECT id,body FROM events WHERE json_extract(body,'$.taskId')=? ORDER BY id", (tid,)):
                    event = dict(json.loads(raw), id=eid)
                    record = tasks.setdefault(tid, dict(count=0, lastEvent=None))
                    record['count'] += 1
                    record['lastEvent'] = event
        return dict(ok=True, tasks=tasks)
