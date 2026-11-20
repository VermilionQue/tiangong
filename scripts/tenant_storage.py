"""Request-scoped private storage. Owner CLI paths remain backward compatible.

Never assign module globals while serving a request: ThreadingHTTPServer serves
different accounts concurrently. DeferredPath resolves under a ContextVar each
time it is used, including derived paths declared at module import time.
"""

import runtime_config
import contextlib
import contextvars
import hashlib
import hmac
import os
import re
import time
from pathlib import Path

BASE = runtime_config.STATE_DIR
_identity = contextvars.ContextVar('tiangong_identity', default='owner')
IDS = re.compile(r'(?:owner|demo|u[0-9a-f]{32})')


def current():
    return _identity.get()


@contextlib.contextmanager
def scope(user_id):
    if not isinstance(user_id, str) or not IDS.fullmatch(user_id):
        raise ValueError('账号无效')
    token = _identity.set(user_id)
    try:
        yield
    finally:
        _identity.reset(token)


def root():
    import cloud_authority
    cloud_authority.guard_local_storage()
    identity = current()
    result = BASE if identity == 'owner' else BASE / 'users' / identity
    if identity != 'owner' and ((BASE / 'users').is_symlink() or result.is_symlink()):
        raise ValueError('资料目录无效')
    return result


class DeferredPath(os.PathLike):
    def __init__(self, relative=''):
        self.relative = Path(relative)
        if self.relative.is_absolute() or '..' in self.relative.parts:
            raise ValueError('无效的资料路径')

    def __fspath__(self):
        return str(root() / self.relative)

    def __str__(self):
        return self.__fspath__()

    def __truediv__(self, child):
        return DeferredPath(self.relative / child)

    def __getattr__(self, name):
        return getattr(root() / self.relative, name)


def path(relative=''):
    return DeferredPath(relative)


def signature(secret, identity, method, target, stamp):
    payload = '\n'.join((identity, method, target, str(stamp))).encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def headers(secret, identity, method, target, now=None):
    stamp = str(int(time.time() if now is None else now))
    return {'X-Tiangong-Account': identity, 'X-Tiangong-Time': stamp,
            'X-Tiangong-Signature': signature(secret, identity, method, target, stamp)}


def verified(headers, secret, method, target, now=None):
    values = [headers.get_all(k, []) for k in
              ('X-Tiangong-Account', 'X-Tiangong-Time', 'X-Tiangong-Signature')]
    if any(len(v) != 1 for v in values):
        raise ValueError('请通过登录入口访问')
    identity, stamp, supplied = (v[0] for v in values)
    if (not IDS.fullmatch(identity) or not re.fullmatch(r'[0-9]{10,11}', stamp)
            or abs((time.time() if now is None else now) - int(stamp)) > 30
            or not hmac.compare_digest(signature(secret, identity, method, target, stamp), supplied)):
        raise ValueError('请通过登录入口访问')
    return identity
