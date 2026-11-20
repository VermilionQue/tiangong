"""DNS wire-format, scoped Apple profile, and gateway isolation checks."""
import base64
import http.client
import json
import plistlib
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import lan_dns
import public_gateway as gateway

HOST = 'journal.example.com'
ORIGIN = 'https://' + HOST
SETTINGS = {'enabled': True, 'address': '192.168.1.100',
            'network': '192.168.1.0/24', 'trigger_dns': '192.168.1.254'}


def question(name=HOST, kind=1, edns=False):
    wire = b''.join(bytes([len(label)]) + label.encode() for label in name.split('.')) + b'\0'
    packet = struct.pack('!6H', 1234, 0x0120, 1, 0, 0, int(edns)) + wire + struct.pack('!2H', kind, 1)
    if edns:
        packet += b'\0' + struct.pack('!HHIH', 41, 1232, 0x8000, 0)
    return packet


class WireTests(unittest.TestCase):
    def test_a_edns_and_mixed_case(self):
        answer = lan_dns.dns_answer(question(HOST.upper(), edns=True), HOST, SETTINGS['address'])
        ident, flags, qd, an, ns, ar = struct.unpack('!6H', answer[:12])
        self.assertEqual((ident, qd, an, ns, ar), (1234, 1, 1, 0, 0))
        self.assertEqual(flags, 0x8500)  # Authoritative, no false DNSSEC assertion.
        self.assertEqual(answer[-4:], bytes([192, 168, 1, 100]))

    def test_no_ipv6_or_https_record_can_send_client_to_public_origin(self):
        for kind in (28, 64, 65):
            answer = lan_dns.dns_answer(question(kind=kind), HOST, SETTINGS['address'])
            self.assertEqual(struct.unpack('!6H', answer[:12])[3], 0)
            self.assertEqual(answer[3] & 15, 0)

    def test_other_domains_are_refused_without_recursion(self):
        for name in ('example.com', 'private.' + HOST, HOST + '.evil.example'):
            answer = lan_dns.dns_answer(question(name), HOST, SETTINGS['address'])
            self.assertEqual(answer[3] & 15, 5)
            self.assertEqual(struct.unpack('!6H', answer[:12])[3], 0)

    def test_malformed_messages_are_rejected(self):
        valid = question()
        cases = [b'', valid[:13], valid[:-1], valid + b'x', valid[:12] + b'\xc0\x0c\0\1\0\1',
                 valid[:4] + b'\0\2' + valid[6:], valid[:2] + b'\x81\0' + valid[4:],
                 question(edns=True)[:-1], b'x' * 4097]
        for packet in cases:
            with self.subTest(packet=packet[:16]), self.assertRaises(ValueError):
                lan_dns.dns_answer(packet, HOST, SETTINGS['address'])

    def test_profile_only_handles_this_domain_on_selected_network(self):
        profile = plistlib.loads(lan_dns.profile(ORIGIN, SETTINGS))
        self.assertFalse(profile['PayloadRemovalDisallowed'])
        self.assertEqual(len(profile['PayloadContent']), 1)
        resolver = profile['PayloadContent'][0]
        self.assertEqual(resolver['PayloadType'], 'com.apple.dnsSettings.managed')
        self.assertEqual(resolver['OnDemandRules'], [
            {'Action': 'Connect', 'DNSServerAddressMatch': ['192.168.1.254']},
            {'Action': 'Disconnect'},
        ])
        dns = resolver['DNSSettings']
        self.assertEqual(dns['SupplementalMatchDomains'], [HOST])
        self.assertEqual(dns['ServerAddresses'], ['192.168.1.100'])
        self.assertEqual(dns['ServerURL'], ORIGIN + '/lan-dns/query')
        self.assertTrue(dns['AllowFailover'])


class GatewayDNSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.state = gateway.SecurityState(cls.folder.name)
        cls.state.configure(ORIGIN, trusted_proxy='cloudflare-tunnel')
        cls.state.set_password('temporary-test-password-never-production')
        data = cls.state.config()
        data['lan_dns'] = SETTINGS
        cls.state.config_path.write_text(json.dumps(data))
        cls.server = gateway.GatewayServer(('127.0.0.1', 0), cls.state, 1)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.folder.cleanup()

    def request(self, path, method='GET', body=None, peer='192.168.1.50', **headers):
        values = {'Host': HOST, 'CF-Connecting-IP': peer, **headers}
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body, values)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_get_and_post_return_dns_only(self):
        query = question(edns=True)
        encoded = base64.urlsafe_b64encode(query).rstrip(b'=').decode()
        get = self.request('/lan-dns/query?dns=' + encoded)
        post = self.request('/lan-dns/query', 'POST', query, **{'Content-Type': 'application/dns-message'})
        for status, headers, raw in (get, post):
            self.assertEqual(status, 200)
            self.assertEqual(headers['Content-Type'], 'application/dns-message')
            self.assertEqual(raw[-4:], bytes([192, 168, 1, 100]))
            self.assertNotIn('Set-Cookie', headers)

    def test_public_clients_cannot_use_resolver(self):
        for peer in ('8.8.8.8', '192.168.20.1', '::1', 'invalid'):
            self.assertEqual(self.request('/lan-dns/query?dns=AAAA', peer=peer)[0], 403)

    def test_bad_http_inputs_are_rejected(self):
        for query in ('', '?dns=bad!', '?dns=AAAA&dns=BBBB', '?dns=AAAA&other=1'):
            self.assertEqual(self.request('/lan-dns/query' + query)[0], 400)
        self.assertEqual(self.request('/lan-dns/query', 'POST', b'{}', **{'Content-Type': 'application/json'})[0], 415)
        self.assertEqual(self.request('/lan-dns/query', 'POST', b'x' * 4097, **{'Content-Type': 'application/dns-message'})[0], 400)
        self.assertEqual(self.request('/lan-dns/query', 'HEAD')[0], 405)

    def test_public_profile_has_no_authentication_or_management_payload(self):
        status, headers, raw = self.request('/lan-dns/tiangong.mobileconfig', peer='8.8.8.8')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'application/x-apple-aspen-config')
        profile = plistlib.loads(raw)
        self.assertEqual([p['PayloadType'] for p in profile['PayloadContent']], ['com.apple.dnsSettings.managed'])
        self.assertNotIn('Set-Cookie', headers)
        self.assertEqual(self.request('/lan-dns/tiangong.mobileconfig', 'POST', b'{}')[0], 400)

    def test_private_app_still_requires_authentication(self):
        for path in ('/api/todos', '/api/finance', '/lan-dns/../api/todos'):
            self.assertIn(self.request(path)[0], (400, 401))
        self.assertEqual(self.request('/lan-dns/unknown')[0], 404)
        self.assertEqual(self.request('/lan-dns/', peer='8.8.8.8')[0], 200)


if __name__ == '__main__':
    unittest.main()
