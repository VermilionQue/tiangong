"""Private SQLite backup plus changed assets for a reversible host migration."""

import runtime_config
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile

FOLDERS = ('service', 'fonts', 'notes', 'finance', 'reports', 'private-images', 'users')
SECURITY = ('gateway.json', 'gateway.sqlite3', 'upstream-secret', 'tunnel.yml', 'tunnel-credentials.json')
DATA = ('daily-quotes.json', 'training-cache.json')


def files(base):
    paths = list(base.glob('*.sqlite3')) + [base / name for name in DATA]
    paths += [base / 'security' / name for name in SECURITY]
    for folder in FOLDERS:
        paths += list((base / folder).rglob('*'))
    return sorted({p for p in paths if p.is_file() and not p.is_symlink()
                   and '__pycache__' not in p.parts and not p.name.endswith(('-wal', '-shm', '.pyc'))})


def snapshot(base, output, previous=None):
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    before = {r['path']: r['sha256'] for r in previous.get('files', [])} if previous else {}
    staging = output / 'data'
    records = []
    for src in files(base):
        relative = src.relative_to(base)
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        database = src.suffix == '.sqlite3'
        if not database and before.get(str(relative)) == digest:
            continue
        dest = staging / relative
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if database:
            with sqlite3.connect(src.as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(dest) as target:
                source.backup(target)
                if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Database validation failed: ' + str(relative))
            digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        else:
            shutil.copy2(src, dest)
        dest.chmod(0o600)
        records.append({'path': str(relative), 'sha256': digest, 'bytes': dest.stat().st_size, 'sqlite': database})
    manifest = {'created': datetime.now(timezone.utc).isoformat(), 'files': records, 'incremental': bool(previous)}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with tarfile.open(output / 'data.tar.gz', 'w:gz') as archive:
        archive.add(staging, arcname='Tiangong')
    return {'files': len(records), 'databases': sum(r['sqlite'] for r in records),
            'archive_bytes': (output / 'data.tar.gz').stat().st_size}


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, default=runtime_config.STATE_DIR)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--previous', type=Path)
    args = parser.parse_args()
    previous = json.loads(args.previous.read_text()) if args.previous else None
    print(json.dumps(snapshot(args.base, args.output, previous)))
