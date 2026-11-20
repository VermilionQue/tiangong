"""Import a reviewed staging ledger, retaining its exact workbook privately."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import finance


def archive_source(source, payload, review_id=None):
    source = Path(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != payload['metadata']['sha256'] or source.suffix.lower() != '.xlsx':
        raise ValueError('原表与核对结果不一致，未导入')
    folder = finance.BASE / 'finance/sources'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / (digest + '.xlsx')
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise ValueError('归档原表校验失败')
    if not target.exists():
        temp = folder / (digest + '.tmp')
        shutil.copyfile(source, temp); temp.chmod(0o600)
        if hashlib.sha256(temp.read_bytes()).hexdigest() != digest:
            raise ValueError('归档原表校验失败')
        os.replace(temp, target)
    # Archive the complete review before committing the ledger. An interrupted
    # write may leave only a temporary file, never a valid-looking partial file.
    if review_id is not None and (not isinstance(review_id, str) or not re.fullmatch(r'[0-9a-f]{64}', review_id)):
        raise ValueError('核对档案编号无效')
    review_name = digest + ('.'+review_id if review_id else '') + '.review.json'
    evidence = folder / review_name
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
    if evidence.exists() and evidence.read_bytes() != raw:
        raise ValueError('已有核对档案与本次内容不一致，未导入')
    if not evidence.exists():
        temp = evidence.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if temp.read_bytes() != raw:
            raise ValueError('核对档案写入校验失败')
        os.replace(temp, evidence)
    return digest


def import_file(source, prepared):
    payload = json.loads(Path(prepared).read_text(encoding='utf-8'))
    if payload['metadata']['count'] != len(payload['records']):
        raise ValueError('导入笔数与核对结果不一致')
    archive_source(source, payload)
    return finance.import_ledger(payload)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--prepared', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(import_file(args.source, args.prepared), ensure_ascii=False))
