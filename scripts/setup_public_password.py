#!/usr/bin/env python3
"""One-time localhost password form. A human types the password in their browser."""
import argparse
import html
import hmac
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from public_gateway import SecurityState, SECURITY


class SetupServer(HTTPServer):
    def __init__(self, state, port=8771):
        config = state.config()
        if not config.get('origin'):
            raise ValueError('请先配置天工录公网域名')
        if config.get('password'):
            raise ValueError('密码已经设置；此页面仅用于第一次设置')
        self.state = state
        self.route = '/setup/' + secrets.token_urlsafe(32)
        self.csrf_token = secrets.token_urlsafe(32)
        self.expires = time.monotonic() + 600
        self.used = False
        super().__init__(('127.0.0.1', port), SetupHandler)
        self.origin = 'http://127.0.0.1:' + str(self.server_port)

    def stop_async(self):
        threading.Thread(target=self.shutdown, daemon=True).start()

    def handle_error(self, request, client_address):
        pass


class SetupHandler(BaseHTTPRequestHandler):
    server_version = 'Tiangong'
    sys_version = ''

    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.page(code, '页面暂时无法打开。', form=False)

    def permitted(self):
        return (self.client_address[0] == '127.0.0.1'
                and self.headers.get_all('Host') == [self.server.origin[len('http://'):]]
                and self.path == self.server.route
                and time.monotonic() < self.server.expires
                and not self.server.used and not self.server.state.config().get('password'))

    def page(self, status=200, message='', form=True):
        nonce = secrets.token_urlsafe(18)
        content = ('<form method="post"><input type="hidden" name="csrf_token" value="'
                   + html.escape(self.server.csrf_token, quote=True)
                   + '''"><label>设置密码<input name="password" type="password" minlength="14" maxlength="256" autocomplete="new-password" required autofocus></label><label>再输入一次<input name="confirm" type="password" minlength="14" maxlength="256" autocomplete="new-password" required></label><button type="submit">保存密码</button></form>''') if form else ''
        raw = ('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>设置密码 · 天工录</title><style nonce="''' + nonce + '''">*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f7f8fb;color:#202126;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}main{width:min(440px,calc(100% - 36px));padding:36px;background:white;border:1px solid #e6eaf2;border-radius:24px}h1{font-size:28px;margin:0 0 14px}p{font-size:14px;color:#8a91a0;line-height:1.7}label{display:block;font-size:14px;margin-top:20px}input{display:block;width:100%;margin-top:8px;padding:14px;border:1px solid #dee3ed;border-radius:11px;font:inherit}input:focus{outline:3px solid #e8eeff;border-color:#6a87d2}button{width:100%;margin-top:26px;padding:15px;border:0;border-radius:12px;background:#597ccd;color:white;font:inherit;cursor:pointer}.message{color:#9b4355}small{display:block;margin-top:22px;color:#9ca1ad;font-size:12px;line-height:1.6}</style><main><h1>给自己的空间，加一把锁。</h1><p>为天工录设置一个登录密码。请使用至少 14 个字符，并妥善保存。</p><p class="message" role="alert">''' + html.escape(message) + '</p>' + content + '<small>设置成功后，此页面会自动关闭入口。以后可使用这个密码登录天工录。</small></main></html>').encode()
        self.send_response(status)
        for key, value in {
            'Content-Type': 'text/html; charset=utf-8', 'Content-Length': str(len(raw)),
            'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': f"default-src 'none'; style-src 'nonce-{nonce}'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            'Connection': 'close',
        }.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def do_GET(self):
        if not self.permitted():
            return self.page(404, '此设置页面已失效。', form=False)
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            return self.page(403, '请从本机打开设置页面。', form=False)
        self.page()

    def do_POST(self):
        if not self.permitted():
            return self.page(404, '此设置页面已失效。', form=False)
        origins = self.headers.get_all('Origin', [])
        # Embedded browsers can omit Origin or use an opaque (null) origin.
        # They still must present the independent secret from this exact form.
        origin_allowed = not origins or (len(origins) == 1 and origins[0] in (self.server.origin, 'null'))
        if (not origin_allowed
                or self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'same-site', 'none')
                or self.headers.get('Transfer-Encoding') is not None
                or len(self.headers.get_all('Content-Length', [])) != 1
                or self.headers.get_content_type() != 'application/x-www-form-urlencoded'):
            return self.page(403, '请在本机设置页面中提交。', form=False)
        try:
            size = int(self.headers['Content-Length'])
            if not 0 < size <= 8192:
                raise ValueError('填写的内容过长，请重新输入。')
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError('提交未完成，请重新输入。')
            try:
                values = parse_qs(body.decode('utf-8'), max_num_fields=3, strict_parsing=True)
            except (ValueError, UnicodeError):
                raise ValueError('提交格式有误，请重新输入。') from None
            csrf = values.get('csrf_token', [])
            if (len(csrf) != 1 or not hmac.compare_digest(csrf[0].encode('utf-8'), self.server.csrf_token.encode('ascii'))):
                return self.page(403, '设置页面已失效，请重新打开本机设置链接。', form=False)
            if set(values) != {'password', 'confirm', 'csrf_token'} or any(len(value) != 1 for value in values.values()):
                raise ValueError('请完整填写两次密码。')
            if values['password'][0] != values['confirm'][0]:
                raise ValueError('两次密码不同，请再检查一下。')
            self.server.state.set_password(values['password'][0])
            self.server.used = True
            try:
                self.page(200, '密码已设置。现在可以打开天工录，使用刚才的密码登录。', form=False)
            finally:
                self.server.stop_async()
        except (ValueError, UnicodeError) as error:
            self.page(400, str(error))
        except OSError:
            self.page(503, '暂时无法保存，请稍后重试。')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='天工录首次密码设置，仅在本机开放十分钟')
    parser.add_argument('--port', type=int, default=8771)
    args = parser.parse_args()
    try:
        server = SetupServer(SecurityState(SECURITY), args.port)
    except ValueError as error:
        parser.exit(1, str(error) + '\n')
    timer = threading.Timer(600, server.shutdown)
    timer.daemon = True
    timer.start()
    # Print the ephemeral local URL once so the human can open it; no HTTP logs.
    print(json.dumps({'setup_url': server.origin + server.route, 'expires_in_seconds': 600}), flush=True)
    try:
        server.serve_forever()
    finally:
        timer.cancel()
        server.server_close()
    print('首次密码设置入口已关闭。', flush=True)


if __name__ == '__main__':
    main()
