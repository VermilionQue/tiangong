"""Rebuild all browser bundles and their integrity manifest from checked-in sources."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
import build_styles

ROOT = Path(__file__).resolve().parents[1]

def build(root=ROOT):
    web = root / 'src/web'
    outputs = build_styles.build(root)
    html = (web / 'index.html').read_text()
    for group, source in [('journal-todos','todos-v20.css'), ('journal-notes','notes-v20.css')]:
        content = (web / source).read_bytes()
        name = 'styles-' + group + '.' + hashlib.sha256(content).hexdigest()[:16] + '.css'
        (web / name).write_bytes(content)
        html, count = re.subn(r'styles-' + group + r'\.[0-9a-f]{16}\.css', name, html)
        if count != 1:
            raise ValueError('Missing style: ' + group)
        outputs.append(name)
    for group in ['theme','install','work']:
        source = web / (group + '.js')
        name = 'scripts-' + group + '.' + hashlib.sha256(source.read_bytes()).hexdigest()[:16] + '.js'
        (web / name).write_bytes(source.read_bytes())
        html, count = re.subn(r'scripts-' + group + r'\.[0-9a-f]{16}\.js', name, html)
        if count != 1:
            raise ValueError('Missing script: ' + group)
        outputs.append(name)
    # Minify only build artifacts. Human-readable sources are always retained.
    for index, old in enumerate(outputs):
        if not old.endswith('.js'):
            continue
        result = subprocess.run(['node', str(root/'scripts/minify.cjs'), str(web/old)], capture_output=True, check=True)
        digest = hashlib.sha256(result.stdout).hexdigest()
        name = re.sub(r'\.[0-9a-f]{16}\.js$', '.' + digest[:16] + '.js', old)
        (web/name).write_bytes(result.stdout)
        html = html.replace(old, name)
        outputs[index] = name
    (web/'index.html').write_text(html)
    manifest = {'version':1, 'files':{name:hashlib.sha256((web/name).read_bytes()).hexdigest() for name in sorted(outputs)}}
    (web/'release-assets.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for path in web.iterdir():
        if re.fullmatch(r'(styles|scripts)-.+\.[0-9a-f]{16}\.(css|js)',path.name) and path.name not in outputs:
            path.unlink()
    import release_assets
    release_assets.published_assets(web)
    return outputs

if __name__ == '__main__':
    print('Built and validated', len(build()), 'bundles')
