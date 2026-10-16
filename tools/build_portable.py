"""Build a relocatable Windows x64 folder from a known Python + pinned wheels.

Build-time only. Does not download, install or execute business software.
Refuses to overwrite an existing destination; runtime/business data is never copied.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ('local_server.py', 'preview_server.py', 'publisher_worker.py',
         'portable_launcher.py', 'account_ocr.ps1')
STDLIB_EXCLUDE = {'site-packages', '__pycache__', 'test', 'tests', 'ensurepip',
                  'idlelib', 'tkinter', 'turtledemo', 'venv'}


def copy(source, target):
    if source.is_symlink():
        raise RuntimeError('Refusing symbolic link: ' + str(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(destination):
    destination = destination.resolve()
    if os.name != 'nt' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('Build with Windows x64 Python')
    if destination.exists() or Path(str(destination) + '.zip').exists():
        raise RuntimeError('Destination already exists; choose a new version directory')
    if not destination.is_relative_to((ROOT / 'dist').resolve()):
        raise RuntimeError('Build destination must be inside this workspace dist directory')
    base = Path(sys.base_prefix)
    requirements = []
    for line in (ROOT / 'requirements-runtime.txt').read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.startswith('#'):
            name, version = line.strip().split('==')
            dist = metadata.distribution(name)
            if dist.version != version:
                raise RuntimeError(f'{name}: require {version}, found {dist.version}')
            requirements.append((name, version, dist))
    destination.mkdir(parents=True)
    runtime = destination / '_runtime'
    runtime.mkdir()
    # No venv launcher: copy the real CPython executable and its matching DLLs.
    for filename in ('python.exe', 'pythonw.exe', 'python3.dll',
                     f'python{sys.version_info.major}{sys.version_info.minor}.dll',
                     'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'):
        copy(base / filename, runtime / filename)
    for path in sorted((base / 'DLLs').iterdir()):
        if path.suffix.lower() in ('.pyd', '.dll') and '_test' not in path.name:
            copy(path, runtime / path.name)
    library_name = f'python{sys.version_info.major}{sys.version_info.minor}.zip'
    with zipfile.ZipFile(runtime / library_name, 'w', zipfile.ZIP_DEFLATED) as library:
        for path in sorted((base / 'Lib').rglob('*')):
            relative = path.relative_to(base / 'Lib')
            if path.is_file() and not set(relative.parts) & STDLIB_EXCLUDE and path.suffix in ('.py', '.txt', '.pem', '.json'):
                library.write(path, relative.as_posix())
    # No import site: no registry, PATH, PYTHONHOME, PYTHONPATH or user-site fallback.
    (runtime / library_name.replace('.zip', '._pth')).write_text(
        library_name + '\n.\nsite-packages\n../app\n', encoding='ascii')
    for name, version, dist in requirements:
        for relative in dist.files or []:
            if ('..' in relative.parts or set(relative.parts) & {'__pycache__', 'tests', 'test'}
                    or relative.suffix == '.pyc' or relative.name == 'direct_url.json'):
                continue
            source = Path(dist.locate_file(relative))
            if source.is_file():
                copy(source, runtime / 'site-packages' / relative)
    app = destination / 'app'
    tracked = subprocess.check_output(['git', 'ls-files', '-z', '--', 'control_console', 'demo'], cwd=ROOT)
    for relative in tracked.decode('utf-8').split('\0'):
        if not relative:
            continue
        path = ROOT / relative
        if path.is_file() and '__pycache__' not in path.parts and path.suffix.lower() in ('.py', '.html', '.js', '.mjs', '.css', '.json', '.svg', '.png', '.woff', '.woff2', '.ico', '.jpg', '.webp'):
            copy(path, app / relative)
    for name in TOOLS:
        copy(ROOT / 'tools' / name, app / 'tools' / name)
    for folder, filename in (('architecture-console-20261013-142225', 'architecture.html'),
                             ('workflow-console-20261013-142225', 'workflow.html')):
        source = ROOT / '.archify' / folder / filename
        copy(source, app / source.relative_to(ROOT))
    for name in ('使用说明-便携版.md',):
        copy(ROOT / 'docs' / name, destination / name)
    command = ('@echo off\r\nsetlocal\r\nchcp 65001 >nul\r\n'
               'if not exist "%~dp0_runtime\\python.exe" (\r\n'
               '  echo Runtime missing. Extract the complete ZIP before starting.\r\n'
               '  pause\r\n  exit /b 1\r\n)\r\n'
               '"%~dp0_runtime\\python.exe" -I -B -X utf8 "%~dp0app\\tools\\portable_launcher.py" %*\r\n'
               'set "console_exit=%errorlevel%"\r\n'
               'if not "%console_exit%"=="0" pause\r\nexit /b %console_exit%\r\n')
    (destination / 'Start.cmd').write_bytes(command.encode('ascii'))
    (destination / '启动总控制台.cmd').write_bytes(command.encode('ascii'))
    # A file allowlist and hashes enable offline integrity checking after relocation.
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    manifest = {'format': 1, 'platform': 'Windows-x64', 'python': platform.python_version(),
                'revision': revision, 'workingTreeDirty': dirty,
                'dependencies': {name: version for name, version, _ in requirements},
                'files': [{'path': p.relative_to(destination).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p)}
                          for p in sorted(destination.rglob('*')) if p.is_file()]}
    (destination / 'package-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = Path(str(destination) + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(destination.rglob('*')):
            if path.is_file():
                zipped.write(path, path.relative_to(destination.parent).as_posix())
    checksum = digest(archive)
    archive.with_suffix('.zip.sha256').write_text(checksum + '  ' + archive.name + '\n', encoding='ascii')
    print(json.dumps({'folder': str(destination), 'archive': str(archive), 'bytes': archive.stat().st_size,
                      'sha256': checksum, 'files': len(manifest['files'])}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'ControlConsole-0.2.0-win-x64')
    args = parser.parse_args()
    build(args.output)
