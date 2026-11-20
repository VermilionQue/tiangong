"""Fail closed unless the HTML and current release assets agree at startup."""
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit

STYLE_BUNDLE = re.compile(r'/styles-(?:shell|middle|app|journal-todos|journal-notes)\.([0-9a-f]{16})\.css')
SCRIPT_BUNDLE = re.compile(r'/scripts-(?:core|modules|theme|install|work)\.([0-9a-f]{16})\.js')


class PageAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        ref = None
        if tag == 'script':
            ref = attrs.get('data-app-src') or attrs.get('src')
        elif tag == 'link' and attrs.get('rel') == 'stylesheet':
            ref = attrs.get('href')
        if ref:
            url = urlsplit(ref)
            if url.scheme or url.netloc or url.query or url.fragment:
                raise ValueError('Invalid release asset URL')
            self.paths.add('/' + url.path.lstrip('/'))


def published_assets(web):
    manifest = json.loads((web / 'release-assets.json').read_text())
    files = manifest.get('files')
    if manifest.get('version') != 1 or not isinstance(files, dict) or not 1 <= len(files) <= 32:
        raise ValueError('Invalid release manifest')
    paths = set()
    for name, digest in files.items():
        path = '/' + name
        match = STYLE_BUNDLE.fullmatch(path) or SCRIPT_BUNDLE.fullmatch(path)
        source = web / name
        if (not match or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or match[1] != digest[:16] or source.is_symlink()
                or hashlib.sha256(source.read_bytes()).hexdigest() != digest):
            raise ValueError('Invalid release asset')
        paths.add(path)
    page = PageAssets()
    page.feed((web / 'index.html').read_text())
    if paths != page.paths:
        raise ValueError('Release manifest does not match page')
    return frozenset(paths)
