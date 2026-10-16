"""Entry point for the self-contained Windows folder. No dependency downloads."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

APP = Path(__file__).resolve().parent.parent
PACKAGE = APP.parent


def check_runtime(full=False):
    import ctypes
    import sqlite3
    import ssl
    import PIL.Image
    import greenlet
    import playwright
    from playwright.async_api import async_playwright

    driver = Path(playwright.__file__).parent / 'driver' / 'node.exe'
    if not driver.is_file():
        raise RuntimeError('包内浏览器驱动缺失，请重新完整解压程序包。')
    # Never let a developer-machine setting redirect the bundled driver.
    os.environ['PLAYWRIGHT_NODEJS_PATH'] = str(driver)
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(PACKAGE / '_unused-browser-cache')
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

    async def driver_check():
        # Starts the bundled transport only; no browser is launched or attached.
        async with async_playwright() as transport:
            assert transport.chromium.name == 'chromium'
    asyncio.run(driver_check())
    if full:
        manifest = json.loads((PACKAGE / 'package-manifest.json').read_text(encoding='utf-8'))
        for row in manifest['files']:
            path = (PACKAGE / row['path']).resolve()
            if not path.is_relative_to(PACKAGE.resolve()) or not path.is_file():
                raise RuntimeError('程序包缺少文件：' + row['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
                raise RuntimeError('程序包文件校验失败：' + row['path'])
    return {'ok': True, 'python': sys.version.split()[0], 'isolated': bool(sys.flags.isolated),
            'driver': 'bundled', 'fullIntegrityCheck': full}


def main():
    parser = argparse.ArgumentParser(description='总控制台便携版，无需安装开发环境')
    parser.add_argument('--port', type=int, default=8861)
    parser.add_argument('--runtime-dir', type=Path, default=PACKAGE / 'data')
    parser.add_argument('--check', action='store_true', help='检查包完整性与内置驱动，不连接账号')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        result = check_runtime(full=args.check)
    except Exception as error:
        print('启动检查失败：' + str(error), file=sys.stderr, flush=True)
        print('请完整解压到有写入权限的目录；不要单独复制启动文件。', file=sys.stderr)
        return 1
    if args.check:
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    from tools.local_server import main as serve
    sys.argv = [sys.argv[0], '--port', str(args.port), '--runtime-dir', str(args.runtime_dir)]
    if not args.no_browser:
        sys.argv.append('--open-browser')
    try:
        serve()
    except SystemExit as error:
        if error.code:
            print('总控启动失败：请检查端口占用、data目录写入权限和磁盘空间；现有程序不会被结束。', file=sys.stderr)
        return int(error.code or 0)
    except OSError:
        print('无法启动：请检查程序及data目录写入权限和磁盘空间。', file=sys.stderr)
        return 1
    except sqlite3.Error:
        print('本地数据库无法打开：请检查data目录权限、磁盘空间及备份。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
