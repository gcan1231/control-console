"""Export the fictional design preview to one offline HTML, without a server."""
from pathlib import Path
import argparse
import hashlib

ROOT = Path(__file__).resolve().parents[1]


def export(destination):
    html = (ROOT / 'demo/theme-preview.html').read_text(encoding='utf-8')
    css = (ROOT / 'demo/theme-preview.css').read_text(encoding='utf-8')
    fixtures = (ROOT / 'demo/data/theme-preview-fixtures.js').read_text(encoding='utf-8')
    javascript = (ROOT / 'demo/modules/theme-preview.js').read_text(encoding='utf-8')
    javascript = javascript.replace("import {THEMES, ACCOUNTS, PRODUCTS, HEADERS} from '../data/theme-preview-fixtures.js';", '')
    modules = []
    for filename, symbol in [('theme-selection.js', 'createSelectionViews'), ('theme-workflows.js', 'composeWorkflow')]:
        module = (ROOT / 'demo/modules' / filename).read_text(encoding='utf-8')
        modules.append(module.replace('export function ', 'function '))
        javascript = javascript.replace(f"import {{{symbol}}} from './{filename}';", '')
    script = (fixtures + '\n' + '\n'.join(modules) + '\n' + javascript).replace('</script', '<\\/script')
    html = html.replace('<link rel="icon" href="/favicon.svg">', '')
    html = html.replace('<link rel="stylesheet" href="/theme-preview.css">', '<style>\n' + css + '\n</style>')
    html = html.replace('<script type="module" src="/modules/theme-preview.js"></script>', '<script type="module">\n' + script + '\n</script>')
    html = html.replace('<a href="/">返回正式工作台</a>', '<span class="offline-label">离线选版 · 双击即可使用</span>')
    destination = destination.resolve()
    if not destination.is_relative_to((ROOT / 'dist').resolve()):
        raise ValueError('Output must be inside this workspace dist directory')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding='utf-8')
    print(str(destination))
    print('SHA256 ' + hashlib.sha256(destination.read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / '总控制台-五种风格预览.html')
    export(parser.parse_args().output)
