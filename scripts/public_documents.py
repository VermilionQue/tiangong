"""Serve only reviewed, hash-verified customer documents from a fixed manifest.

This module never imports the private application or reads its data directory.
Every path component is opened without following symlinks. A manifest is the
publication boundary; merely copying another file into the directory does not
publish it.
"""
import hashlib
import json
import os
import re
import stat
import threading
from pathlib import Path


CONTENT_TYPES = {
    '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8',
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.gif': 'image/gif', '.webp': 'image/webp', '.avif': 'image/avif',
    '.ico': 'image/x-icon', '.pdf': 'application/pdf',
    '.woff': 'font/woff', '.woff2': 'font/woff2',
}
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024


class PublicationError(ValueError):
    """Publication is incomplete or failed validation; no file may be sent."""


def valid_name(name):
    if (not isinstance(name, str) or not name or len(name) > 1024
            or any(ord(char) < 32 or ord(char) == 127 for char in name)
            or any(char in name for char in ('\\', '%', '?', '#'))):
        return False
    parts = name.split('/')
    return (all(part and not part.startswith('.') and part != 'manifest.json' for part in parts)
            and Path(name).suffix.lower() in CONTENT_TYPES)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublicationError('公开文件清单存在重复项')
        result[key] = value
    return result


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class PublicDocuments:
    def __init__(self, root):
        # Do not resolve(): silently following a publication symlink would
        # defeat the explicit no-symlink boundary below.
        self.root = Path(root).absolute()
        self.lock = threading.Lock()
        self.manifest_signature = None
        self.files = {}
        self.verified = {}

    def _open(self, name):
        """Return a regular-file stream, using descriptor-relative nofollow opens."""
        directory = os.open(self.root.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in self.root.parts[1:] + tuple(name.split('/')[:-1]):
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=directory)
                os.close(directory)
                directory = following
            descriptor = os.open(name.split('/')[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise PublicationError('公开资源必须为普通文件')
                return os.fdopen(descriptor, 'rb')
            except Exception:
                os.close(descriptor)
                raise
        finally:
            os.close(directory)

    def manifest(self):
        with self._open('manifest.json') as stream:
            info = os.fstat(stream.fileno())
            if info.st_size > MAX_MANIFEST_BYTES:
                raise PublicationError('公开文件清单过大')
            version = signature(info)
            with self.lock:
                if self.manifest_signature == version:
                    return dict(self.files)
                try:
                    data = json.loads(stream.read(MAX_MANIFEST_BYTES + 1), object_pairs_hook=unique_object)
                except (ValueError, UnicodeError) as error:
                    raise PublicationError('公开文件清单无效') from error
                files = data.get('files') if isinstance(data, dict) else None
                if (not isinstance(files, dict) or not 1 <= len(files) <= 4096
                        or 'index.html' not in files
                        or any(not valid_name(name) or not isinstance(digest, str)
                               or not re.fullmatch('[0-9a-f]{64}', digest)
                               for name, digest in files.items())):
                    raise PublicationError('公开文件清单无效')
                if version != signature(os.fstat(stream.fileno())):
                    raise PublicationError('公开文件清单正在更新')
                self.files = dict(files)
                self.manifest_signature = version
                self.verified.clear()
                return dict(files)

    def open_verified(self, name, files=None):
        files = self.manifest() if files is None else files
        if not valid_name(name) or name not in files:
            raise FileNotFoundError(name)
        stream = self._open(name)
        try:
            info = os.fstat(stream.fileno())
            if info.st_size > MAX_FILE_BYTES:
                raise PublicationError('公开资源过大')
            version = signature(info)
            digest = files[name]
            with self.lock:
                if self.verified.get(name) != (version, digest):
                    actual = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(128 * 1024), b''):
                        actual.update(chunk)
                    if (actual.hexdigest() != digest
                            or version != signature(os.fstat(stream.fileno()))):
                        raise PublicationError('公开资源与已确认清单不一致')
                    self.verified[name] = (version, digest)
            stream.seek(0)
            return stream, info.st_size, '"' + digest + '"', CONTENT_TYPES[Path(name).suffix.lower()]
        except Exception:
            stream.close()
            raise


def byte_range(value, size):
    """Parse exactly one byte range. Invalid/unsatisfiable ranges raise ValueError."""
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
    if not match or not any(match.groups()) or size == 0:
        raise ValueError('无效范围')
    first, last = match.groups()
    if not first:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError('无效范围')
        return max(0, size - suffix), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise ValueError('无效范围')
    return start, end
