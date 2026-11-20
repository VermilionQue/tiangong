"""Build ordered CSS and JS bundles. Keep journal styles and work.js independent."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]


def build(root=ROOT):
    web = root / 'src/web'
    groups = json.loads((web / 'style-sources.json').read_text())
    page = web / 'index.html'
    html = page.read_text()
    outputs = []
    for group, names in groups.items():
        content = '\n'.join('/* ' + name + ' */\n' + (web / name).read_text()
                            for name in names).encode()
        digest = hashlib.sha256(content).hexdigest()[:16]
        name = f'styles-{group}.{digest}.css'
        (web / name).write_bytes(content)
        (web / name).chmod(0o644)
        html, count = re.subn(r'(?<=href=")styles-' + group + r'\.[0-9a-f]{16}\.css', name, html)
        if count != 1:
            raise ValueError('Missing bundle link: ' + group)
        outputs.append(name)
    for group, names in json.loads((web / 'script-sources.json').read_text()).items():
        content = '\n;\n'.join('/* ' + name + ' */\n' + (web / name).read_text()
                               for name in names).encode()
        digest = hashlib.sha256(content).hexdigest()[:16]
        name = f'scripts-{group}.{digest}.js'
        (web / name).write_bytes(content)
        (web / name).chmod(0o644)
        html, count = re.subn(r'(?<=data-app-src=")scripts-' + group + r'\.[0-9a-f]{16}\.js', name, html)
        if count != 1:
            raise ValueError('Missing script bundle: ' + group)
        outputs.append(name)
    page.write_text(html)
    return outputs


if __name__ == '__main__':
    print('\n'.join(build()))
