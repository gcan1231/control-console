"""Loopback-only import service. No online documents or business executors."""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import secrets
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from control_console.importers import ImportFailure, MAX_BYTES, inspect_workbook
from control_console.store import ConsoleStore
from control_console.materials import MaterialService
from control_console.editing import EditingService
from control_console.workbench import WorkbenchService
from control_console.account_capture import AccountCaptureService
from control_console.selection import SelectionService
from control_console.logs import LogService
from tools.preview_server import resolve_request


class LocalHandler(BaseHTTPRequestHandler):
    server_version = 'LocalConsole/1'

    def json_response(self, status, value):
        content = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(content)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(content)

    def allowed_host(self):
        host = self.headers.get('Host','').lower()
        port = self.server.server_port
        allowed = {f'127.0.0.1:{port}',f'localhost:{port}'}
        if port == 80:
            allowed.update({'127.0.0.1','localhost'})
        if host not in allowed:
            self.json_response(403, {'ok':False,'error':{'code':'host','message':'仅接受本机服务地址。','details':[]}})
            return False
        return True

    def do_GET(self):
        if not self.allowed_host():
            return
        path = urlsplit(self.path).path
        if path == '/api/health':
            self.json_response(200,{'ok':True,'version':1,'token':self.server.token,'capabilities':['local-import','state-storage','material-handoff','editing-handoff','account-workbench','merchant-locks','local-reference-watch','account-capture','daily-selection','log-center']})
        elif path in ('/api/logs/state','/api/logs/summary'):
            try:
                query={k:v[0] for k,v in parse_qs(urlsplit(self.path).query).items()}
                result=self.server.logs.summary([query['taskId']] if query.get('taskId') else None) if path.endswith('/summary') else self.server.logs.state(query)
                self.json_response(200,result)
            except (ImportFailure,ValueError,TypeError) as error:
                self.json_response(422,{'ok':False,'error':error.as_dict() if isinstance(error,ImportFailure) else {'code':'logs_query','message':'请检查日志筛选条件。','details':[]}})
            except (OSError,sqlite3.Error):
                self.json_response(500,{'ok':False,'error':{'code':'logs_storage','message':'日志副本暂不可读，请检查运行目录。','details':[]}})
        elif path.startswith('/api/selection/'):
            try:
                query={k:v[0] for k,v in parse_qs(urlsplit(self.path).query).items()}
                if path == '/api/selection/state':
                    self.json_response(200,self.server.selection.state())
                elif path == '/api/selection/products':
                    query['showOther']=query.get('showOther')=='1'
                    query['page']=int(query.get('page','1'))
                    self.json_response(200,self.server.selection.catalog(query))
                elif path == '/api/selection/product':
                    self.json_response(200,self.server.selection.product(query.get('productId')))
                elif path == '/api/selection/export':
                    content=self.server.selection.export(query)
                    self.send_response(200)
                    self.send_header('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                    self.send_header('Content-Disposition','attachment; filename="daily-tasks.xlsx"')
                    self.send_header('Content-Length',str(len(content)))
                    self.send_header('Cache-Control','no-store')
                    self.send_header('X-Content-Type-Options','nosniff')
                    self.end_headers()
                    self.wfile.write(content)
                else:
                    self.json_response(404,{'ok':False,'error':{'code':'route','message':'接口不存在。','details':[]}})
            except ImportFailure as error:
                self.json_response(422,{'ok':False,'error':error.as_dict()})
            except (ValueError,TypeError,KeyError):
                self.json_response(422,{'ok':False,'error':{'code':'selection_query','message':'选品筛选参数不合法，请重新选择日期或账号。','details':[]}})
            except (OSError,sqlite3.Error):
                self.json_response(500,{'ok':False,'error':{'code':'storage','message':'本地选品数据暂时不可读，请检查运行目录后重试。','details':[]}})
        elif path == '/api/state':
            state=self.server.store.state()
            material=self.server.material.state()
            for task in state['tasks']:
                task['material']=material['materialTasks'].get(task['id'])
            state.update(material)
            editing=self.server.editing.state()
            for task in state['tasks']:
                task['editing']=editing['editingTasks'].get(task['id'])
            state.update(editing)
            state['accountCaptures']=self.server.accounts.list_drafts()
            self.json_response(200,self.server.workbench.enrich(state))
        elif path == '/api/account-captures/draft':
            try:
                query=parse_qs(urlsplit(self.path).query)
                self.json_response(200,self.server.accounts.draft(query.get('captureId',[None])[0]))
            except ImportFailure as error:
                self.json_response(422,{'ok':False,'error':error.as_dict()})
        elif path == '/api/account-captures/image':
            try:
                query=parse_qs(urlsplit(self.path).query)
                data,mime=self.server.accounts.image(query.get('captureId',[None])[0])
                self.send_response(200)
                self.send_header('Content-Type',mime)
                self.send_header('Content-Length',str(len(data)))
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers()
                self.wfile.write(data)
            except (ImportFailure,OSError,ValueError):
                self.json_response(404,{'ok':False,'error':{'code':'capture_image','message':'原始截图暂不可读。','details':[]}})
        elif path == '/api/products/image':
            query=parse_qs(urlsplit(self.path).query)
            try:
                data,mime=self.server.workbench.image(query.get('sourceId',[None])[0],query.get('member',[None])[0])
                self.send_response(200)
                self.send_header('Content-Type',mime)
                self.send_header('Content-Length',str(len(data)))
                self.send_header('Cache-Control','private, max-age=3600')
                self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers()
                self.wfile.write(data)
            except (ImportFailure,OSError,ValueError):
                self.json_response(404,{'ok':False,'error':{'code':'image','message':'该来源版本图片暂不可用。','details':[]}})
        elif path.startswith('/api/'):
            self.json_response(404,{'ok':False,'error':{'code':'route','message':'接口不存在。','details':[]}})
        else:
            self.static_file()

    def do_HEAD(self):
        if self.allowed_host():
            self.static_file()

    def static_file(self):
        try:
            target = resolve_request(self.path)
            if target is None or not target.is_file():
                self.send_error(404,'Static file not found')
                return
            content = target.read_bytes()
            content_type = {'.js':'text/javascript','.mjs':'text/javascript'}.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            self.send_response(200)
            self.send_header('Content-Type',content_type + ('; charset=utf-8' if content_type.startswith('text/') or content_type=='application/json' else ''))
            self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(content)
        except (ValueError,UnicodeError,OSError):
            self.send_error(404,'Static file not found')

    def do_POST(self):
        if not self.allowed_host():
            return
        origin = self.headers.get('Origin')
        if origin is not None and origin != 'http://' + self.headers.get('Host',''):
            self.json_response(403,{'ok':False,'error':{'code':'origin','message':'写请求必须来自本机页面同源。','details':[]}})
            return
        if not secrets.compare_digest(self.headers.get('X-Console-Token',''),self.server.token):
            self.json_response(403,{'ok':False,'error':{'code':'token','message':'请从本机页面连接服务后重试。','details':[]}})
            return
        try:
            if not self.headers.get('Content-Type','').startswith('application/json'):
                raise ImportFailure('content_type','接口仅接受JSON请求。')
            length = int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 32 * 1024 * 1024:
                self.json_response(413,{'ok':False,'error':{'code':'body_size','message':'请求超过本地导入大小限制。','details':[]}})
                return
            body = json.loads(self.rfile.read(length))
            if not isinstance(body,dict):
                raise ImportFailure('body','请求必须为JSON对象。')
            path = urlsplit(self.path).path
            if path in ('/api/inspect','/api/import/preview','/api/import/commit','/api/tasks/import/preview','/api/tasks/import/commit'):
                filename,encoded = body.get('filename'),body.get('content')
                if not isinstance(filename,str) or not filename.lower().endswith('.xlsx'):
                    raise ImportFailure('file_type','首版只接受.xlsx文件。')
                if not isinstance(encoded,str) or len(encoded)>MAX_BYTES*4//3+4:
                    raise ImportFailure('file_size','文件编码或大小不合法。')
                try:
                    content = base64.b64decode(encoded,validate=True)
                except (binascii.Error,ValueError) as error:
                    raise ImportFailure('base64','文件内容编码不合法。') from error
                if path == '/api/inspect':
                    result = inspect_workbook(content)
                elif path == '/api/tasks/import/preview':
                    result = self.server.workbench.preview_tasks(filename,content,body.get('options'))
                elif path == '/api/tasks/import/commit':
                    result = self.server.workbench.commit_tasks(filename,content,body.get('options'),body.get('revisionToken'))
                elif path.endswith('/preview'):
                    result = self.server.store.preview_import(body.get('kind'),filename,content,body.get('options'))
                else:
                    result = self.server.store.commit_import(body.get('kind'),filename,content,body.get('options'))
            elif path == '/api/logs/configure':
                result = self.server.logs.configure(body)
            elif path == '/api/logs/poll':
                result = self.server.logs.poll()
            elif path == '/api/reference/register':
                result = self.server.workbench.register(body.get('kind'),body.get('path'),body.get('options'))
            elif path.startswith('/api/selection/'):
                operation={'add':'add','update':'update','remove':'remove','target':'target','preset':'preset','apply-preset':'apply_preset','preview':'preview','generate':'generate'}.get(path.removeprefix('/api/selection/'))
                if operation is None:
                    self.json_response(404,{'ok':False,'error':{'code':'route','message':'接口不存在。','details':[]}})
                    return
                for field in ('accountId','productId','draftId','presetId','id','editId','token'):
                    if field in body and (not isinstance(body[field],str) or not body[field] or len(body[field])>1000):
                        raise ImportFailure('selection_request','请选择有效的账号、来源记录或草稿，再重试。')
                result=getattr(self.server.selection,operation)(body)
            elif path == '/api/reference/refresh':
                result = self.server.workbench.refresh_sources(force=True)
            elif path == '/api/accounts/verify':
                result = self.server.workbench.account_profiles(body.get('profiles'),body.get('confirmed'))
            elif path == '/api/account-captures/preview':
                result = self.server.accounts.preview(body)
            elif path == '/api/account-captures/confirm':
                result = self.server.accounts.confirm(body)
            elif path == '/api/editing/verify-batch':
                result = self.server.editing.verify_batch(body.get('taskIds'),body.get('kind'),body.get('confirmed'),body.get('note',''))
            elif path == '/api/settings/aliases':
                result = self.server.store.set_aliases(body.get('aliases'))
            elif path == '/api/tasks/review':
                result = self.server.store.review_task(body.get('taskId'),body.get('note',''),body.get('duplicateConfirmed'))
            elif path == '/api/material/settings':
                result = self.server.material.settings(body.get('port'),body.get('database'))
            elif path == '/api/material/prepare':
                result = self.server.material.prepare(body.get('taskIds'))
            elif path == '/api/material/send':
                result = self.server.material.send(body.get('packageId'))
            elif path == '/api/material/collect':
                result = self.server.material.collect(body.get('packageId'))
            elif path == '/api/material/confirm':
                result = self.server.material.confirm(body.get('taskId'),body.get('note'),body.get('confirmed'))
            elif path == '/api/material/retry':
                result = self.server.material.retry(body.get('taskId'),body.get('note'))
            elif path == '/api/editing/prepare':
                result = self.server.editing.prepare(body.get('taskIds'))
            elif path == '/api/editing/gui':
                result = self.server.editing.gui(body.get('packageId'),body)
            elif path == '/api/editing/collect':
                result = self.server.editing.collect(body.get('packageId'))
            elif path == '/api/editing/verify':
                result = self.server.editing.verify(body.get('taskId'),body.get('kind'),body.get('note'),body.get('confirmed'))
            elif path == '/api/editing/retry':
                result = self.server.editing.retry(body.get('taskId'),body.get('note'))
            else:
                self.json_response(404,{'ok':False,'error':{'code':'route','message':'接口不存在。','details':[]}})
                return
            self.json_response(200,result)
        except ImportFailure as error:
            self.json_response(422,{'ok':False,'error':error.as_dict()})
        except (json.JSONDecodeError,UnicodeError,ValueError,TypeError,KeyError) as error:
            self.json_response(422,{'ok':False,'error':{'code':'invalid_request','message':'请求或工作簿内容不合法，请检查文件及选项。','details':[]}})
        except (OSError,sqlite3.Error):
            self.json_response(500,{'ok':False,'error':{'code':'storage','message':'本地存储不可写，请检查runtime目录权限和磁盘空间。','details':[]}})


def create_server(port, runtime_dir):
    server = ThreadingHTTPServer(('127.0.0.1',port),LocalHandler)
    try:
        server.store = ConsoleStore(runtime_dir)
        server.material = MaterialService(server.store)
        server.editing = EditingService(server.store,server.material)
        server.workbench = WorkbenchService(server.store)
        server.accounts = AccountCaptureService(server.store,server.workbench)
        server.selection = SelectionService(server.store,server.workbench)
        server.logs = LogService(runtime_dir, trusted_task_ids=lambda: {t['id'] for t in server.store.state()['tasks']})
        server.token = secrets.token_urlsafe(32)
        return server
    except Exception:
        server.server_close()
        raise


def main():
    parser = argparse.ArgumentParser(description='Local task import and SQLite workspace')
    parser.add_argument('--port',type=int,default=8861)
    parser.add_argument('--runtime-dir',type=Path,default=ROOT/'runtime')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    try:
        server = create_server(args.port,args.runtime_dir)
    except OSError as error:
        parser.exit(1,f'Cannot start local service: {error}\nExisting processes are left untouched.\n')
    print(f'Local workspace: http://127.0.0.1:{args.port}/',flush=True)
    print('Online documents remain read-only. No business software is executed.',flush=True)
    stop=threading.Event()
    def monitor():
        while not stop.wait(10):
            try:
                server.workbench.refresh_sources()
                material=server.material.state()
                pending={row['packageId'] for row in material['materialTasks'].values() if row['state'] in ('prepared','sent','review','waiting','outputting','retry','reported')}
                for package in material['materialPackages']:
                    if package['state']=='sent' and package['id'] in pending:
                        server.material.collect(package['id'])
            except (ImportFailure,OSError,sqlite3.Error,ValueError):
                # Source status records its own error. Failed package results
                # are visible to the operator; never start or retry software.
                continue
    watcher=threading.Thread(target=monitor,name='local-reference-watch',daemon=True)
    watcher.start()
    def monitor_logs():
        while not stop.wait(3):
            try:
                server.logs.poll()
            except (ImportFailure,OSError,sqlite3.Error,ValueError):
                # Log collection cannot initiate work or change business results.
                continue
    threading.Thread(target=monitor_logs,name='local-log-watch',daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
