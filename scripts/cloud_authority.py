"""Keep local integrations on the single cloud dataset during a reversible trial.

Transport is SSH with a pinned host key; this is never an HTTP/public RPC route.
When cloud access fails, fail closed instead of writing to an old local copy.
"""

import runtime_config
import base64
import functools
import importlib
import json
from pathlib import Path
import subprocess
import sys

CONFIG = runtime_config.STATE_DIR / 'security/authority.json'
ALLOWED = {
    'cloud_reminders': ('state', 'publish', 'queue', 'pending', 'acknowledge'),
    'work_server': ('listing', 'save'),
    'todos': ('listing', 'save'),
    'notes': ('all_notes', 'save', 'change_deleted'),
    'wellness': ('state', 'reports', 'save_report', 'due_reminder', 'mark_reminded', 'next_assessment'),
    'finance': ('state', 'save'),
    'finance_hub': ('state', 'manage', 'payment_quote'),
    'receipts': ('state', 'history', 'save'),
    'people': ('state', 'save', 'archive'),
    'travel': ('state', 'listing', 'save'),
    'treasures': ('state', 'save', 'use'),
    'checkins': ('state', 'save'),
}


def config():
    try:
        value = json.loads(CONFIG.read_text())
    except FileNotFoundError:
        return {'mode': 'local'}
    if value.get('mode') not in ('local', 'frozen', 'cloud'):
        raise RuntimeError('资料服务配置无效，已停止写入')
    return value


def guard_local_storage():
    if config()['mode'] != 'local':
        raise RuntimeError('正式资料已切换或正在迁移，请通过天工录接口操作；本机旧副本禁止读写')


def encode(value):
    if isinstance(value, bytes):
        return {'__tiangong_bytes__': base64.b64encode(value).decode()}
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    raise TypeError('云端操作只接受资料值，不接受本机文件路径')


def call(module, name, args, kwargs, settings):
    import tenant_storage
    request = {'module': module, 'function': name, 'args': args, 'kwargs': kwargs,
               'identity': tenant_storage.current()}
    # The remote command is fixed, never constructed from user record content.
    command = ['ssh', '-F', settings['ssh_config'], settings.get('host', 'tiangong-cloud-api'),
               "runuser -u tiangong -- /srv/tiangong/venv/bin/python '/srv/tiangong/Library/Application Support/Tiangong/service/scripts/cloud_authority.py' --dispatch"]
    try:
        result = subprocess.run(command, input=json.dumps(request, ensure_ascii=False, default=encode),
                                text=True, capture_output=True, timeout=55)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('云端暂未响应，未改写本机旧资料；请先查询结果再重试') from exc
    if result.returncode:
        raise RuntimeError('云端连接失败，未改写本机旧资料；请先查询结果再重试')
    try:
        response = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError('云端回执不完整，请先查询结果再重试') from exc
    if not response.get('ok'):
        error = response.get('error', '云端操作未完成')
        kind = response.get('type')
        if kind == 'ValueError':
            raise ValueError(error)
        if kind == 'ConflictError':
            raise getattr(importlib.import_module(module), 'ConflictError', ValueError)(error)
        raise RuntimeError(error)
    value = response['value']
    if isinstance(value, dict) and set(value) == {'__tiangong_bytes__'}:
        return base64.b64decode(value['__tiangong_bytes__'])
    return value


def install(namespace, module):
    for name in ALLOWED[module]:
        original = namespace[name]
        def wrap(function, operation):
            @functools.wraps(function)
            def routed(*args, **kwargs):
                settings = config()
                if settings['mode'] == 'frozen':
                    raise RuntimeError('资料正在切换，请稍后再保存')
                if settings['mode'] == 'cloud':
                    return call(module, operation, args, kwargs, settings)
                return function(*args, **kwargs)
            return routed
        namespace[name] = wrap(original, name)


def dispatch(request):
    import tenant_storage
    module, name = request.get('module'), request.get('function')
    if module not in ALLOWED or name not in ALLOWED[module]:
        raise ValueError('不支持的云端操作')
    if not isinstance(request.get('args'), list) or not isinstance(request.get('kwargs'), dict):
        raise ValueError('云端操作参数无效')
    with tenant_storage.scope(request.get('identity')):
        return getattr(importlib.import_module(module), name)(*request['args'], **request['kwargs'])


if __name__ == '__main__':
    try:
        raw = sys.stdin.read(20 * 1024 * 1024 + 1)
        if len(raw) > 20 * 1024 * 1024:
            raise ValueError('资料超过传输限制')
        value = dispatch(json.loads(raw))
        reply = {'ok': True, 'value': value}
    except Exception as exc:
        reply = {'ok': False, 'type': type(exc).__name__, 'error': str(exc)}
    print(json.dumps(reply, ensure_ascii=False, default=encode))
