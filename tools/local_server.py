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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from control_console.importers import ImportFailure, MAX_BYTES, inspect_workbook
from control_console.store import ConsoleStore
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
            self.json_response(200,{'ok':True,'version':1,'token':self.server.token,'capabilities':['local-import','state-storage']})
        elif path == '/api/state':
            self.json_response(200,self.server.store.state())
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
            if path in ('/api/inspect','/api/import/preview','/api/import/commit'):
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
                elif path.endswith('/preview'):
                    result = self.server.store.preview_import(body.get('kind'),filename,content,body.get('options'))
                else:
                    result = self.server.store.commit_import(body.get('kind'),filename,content,body.get('options'))
            elif path == '/api/settings/aliases':
                result = self.server.store.set_aliases(body.get('aliases'))
            elif path == '/api/tasks/review':
                result = self.server.store.review_task(body.get('taskId'),body.get('note',''),body.get('duplicateConfirmed'))
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
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
