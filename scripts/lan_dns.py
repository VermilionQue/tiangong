"""Optional, single-name DoH for the owner's home network; never recursive.

The loopback gateway validates Host and trusted-proxy headers before dispatch.
Caddy must overwrite CF-Connecting-IP with the actual LAN peer address.
"""
import base64
import binascii
import html
import ipaddress
import plistlib
import re
import struct
import uuid
from urllib.parse import parse_qs, urlsplit

PREFIX = '/lan-dns/'
MAX_QUERY = 4096


def dns_answer(packet, hostname, address):
    if not 17 <= len(packet) <= MAX_QUERY:
        raise ValueError('Invalid DNS message size')
    ident, flags, qd, an, ns, ar = struct.unpack('!6H', packet[:12])
    if flags & 0xFA00 or qd != 1 or an or ns or ar > 1:
        raise ValueError('Only a standard single-question query is supported')
    pos, labels = 12, []
    while True:
        if pos >= len(packet):
            raise ValueError('Truncated DNS name')
        size = packet[pos]
        pos += 1
        if not size:
            break
        if size > 63 or pos + size > len(packet):
            raise ValueError('Invalid DNS label')
        labels.append(packet[pos:pos + size])
        pos += size
        if pos - 12 > 254:
            raise ValueError('DNS name is too long')
    if pos + 4 > len(packet):
        raise ValueError('Missing DNS question')
    kind, cls = struct.unpack('!2H', packet[pos:pos + 4])
    end = pos + 4
    # Accept one ordinary EDNS OPT record, including DNSSEC/padding options.
    # Do not echo options, compression pointers or DNSSEC assertions.
    if ar:
        if len(packet) < end + 11 or packet[end] != 0:
            raise ValueError('Invalid EDNS record')
        opt_type, _, _, size = struct.unpack('!HHIH', packet[end + 1:end + 11])
        if opt_type != 41 or end + 11 + size != len(packet):
            raise ValueError('Invalid EDNS length')
    elif end != len(packet):
        raise ValueError('Trailing DNS data')
    matches = b'.'.join(labels).lower() == hostname.encode('ascii').lower() and cls == 1
    answer = b''
    if matches and kind == 1:
        answer = b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 60, 4) + ipaddress.IPv4Address(address).packed
    # Known name: authoritative A or NODATA (including AAAA and HTTPS/SVCB).
    # Other names/classes: REFUSED; there is no upstream resolver.
    response_flags = 0x8000 | (flags & 0x0100) | (0x0400 if matches else 5)
    return struct.pack('!6H', ident, response_flags, 1, int(bool(answer)), 0, 0) + packet[12:end] + answer


def profile(origin, settings):
    hostname = urlsplit(origin).hostname
    identity = 'cloud.admin.tiangong.home-dns'
    payload = {
        'PayloadType': 'com.apple.dnsSettings.managed', 'PayloadVersion': 1,
        'PayloadIdentifier': identity + '.resolver',
        'PayloadUUID': str(uuid.uuid5(uuid.NAMESPACE_URL, origin + '/lan-dns/resolver')),
        'PayloadDisplayName': '天工录家庭直连',
        'DNSSettings': {
            'DNSProtocol': 'HTTPS', 'ServerURL': origin + PREFIX + 'query',
            'ServerAddresses': [settings['address']],
            'SupplementalMatchDomains': [hostname], 'AllowFailover': True,
        },
        'OnDemandRules': [
            {'Action': 'Connect', 'DNSServerAddressMatch': [settings['trigger_dns']]},
            {'Action': 'Disconnect'},
        ],
    }
    return plistlib.dumps({
        'PayloadType': 'Configuration', 'PayloadVersion': 1,
        'PayloadIdentifier': identity,
        'PayloadUUID': str(uuid.uuid5(uuid.NAMESPACE_URL, origin + '/lan-dns/profile')),
        'PayloadDisplayName': '天工录家庭直连', 'PayloadOrganization': '天工录',
        'PayloadDescription': '仅在网络 DNS 为 ' + settings['trigger_dns'] +
                              ' 时将天工录域名直连家中 iMac。其他域名不变；离开该网络后停用。可随时移除此描述文件。',
        'PayloadRemovalDisallowed': False, 'PayloadContent': [payload],
    })


def handle(handler, data, path, query, client_key):
    settings = data.get('lan_dns')
    if not isinstance(settings, dict) or not settings.get('enabled'):
        return handler.reply(404, {'error': '页面不存在'})
    origin = data['origin'].rstrip('/')
    hostname = urlsplit(origin).hostname

    def send(raw, media, extra=None):
        handler.headers_out(200, media, len(raw), extra)
        if handler.command != 'HEAD':
            handler.wfile.write(raw)

    if path in (PREFIX, PREFIX + 'tiangong.mobileconfig'):
        if handler.command not in ('GET', 'HEAD') or query:
            return handler.reply(400, {'error': '请求格式无效'})
        if path.endswith('.mobileconfig'):
            return send(profile(origin, settings), 'application/x-apple-aspen-config',
                        {'Content-Disposition': 'attachment; filename="tiangong-home.mobileconfig"'})
        page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>天工录 · 家庭直连</title>
<body><main><h1>天工录 · 家庭直连</h1>
<p>让家中的 iPhone、iPad 直接访问 iMac 上的天工录。</p>
<ol><li>家庭 Wi-Fi 的「配置 DNS」保持手动，仅填 <strong>TRIGGER</strong>。</li>
<li>用 Safari <a href="tiangong.mobileconfig">下载家庭直连描述文件</a>。</li>
<li>打开「设置 → 已下载描述文件」，查看名称「天工录家庭直连」并安装。若未显示，可在「通用 → VPN 与设备管理」找到。</li>
<li>重新连接一次家庭 Wi-Fi，然后<a href="ORIGIN/">打开天工录</a>。</li></ol>
<p>此文件仅设置天工录的加密 DNS，不安装证书、不管理设备。只有网络 DNS 为 TRIGGER 时启用，离家后停用。其他网站的 DNS 不变。</p>
<p>描述文件未签名，设置中会显示「未签名」。可在「VPN 与设备管理」中移除「天工录家庭直连」以撤销。</p>
<p>本机 HTTP 通路已验证；手机安装和实际打开效果仍待你确认。</p></main></body></html>'''
        return send(page.replace('TRIGGER', html.escape(settings['trigger_dns']))
                    .replace('ORIGIN', html.escape(origin, quote=True)).encode(), 'text/html; charset=utf-8')

    if path != PREFIX + 'query':
        return handler.reply(404, {'error': '页面不存在'})
    # Only the configured private subnet may use this non-recursive endpoint.
    if data.get('trusted_proxy') != 'cloudflare-tunnel' or client_key is None:
        return handler.reply(403, {'error': '此入口仅限家庭网络'})
    try:
        peer = ipaddress.ip_address(handler.headers['CF-Connecting-IP'].strip())
        if isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped:
            peer = peer.ipv4_mapped
        if peer not in ipaddress.ip_network(settings['network']):
            raise ValueError('Outside LAN')
    except (ValueError, TypeError):
        return handler.reply(403, {'error': '此入口仅限家庭网络'})
    retry = handler.server.state.rate('lan-dns:' + client_key, 180, 60)
    if retry:
        return handler.reply(429, {'error': '查询过于频繁'}, {'Retry-After': str(retry)})
    try:
        if handler.command == 'GET':
            args = parse_qs(query, strict_parsing=True, max_num_fields=1)
            if set(args) != {'dns'} or len(args['dns']) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]+', args['dns'][0]):
                raise ValueError('Invalid DoH query')
            encoded = args['dns'][0]
            packet = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
        elif handler.command == 'POST' and not query:
            if handler.headers.get_content_type() != 'application/dns-message':
                return handler.reply(415, {'error': '需要 DNS 消息格式'})
            length = int(handler.headers.get('Content-Length', '0'))
            if not 17 <= length <= MAX_QUERY:
                raise ValueError('Invalid body size')
            packet = handler.rfile.read(length)
        else:
            return handler.reply(405, {'error': '仅支持 GET 和 POST'}, {'Allow': 'GET, POST'})
        raw = dns_answer(packet, hostname, settings['address'])
    except (ValueError, UnicodeError, binascii.Error):
        return handler.reply(400, {'error': 'DNS 请求格式无效'})
    return send(raw, 'application/dns-message')
