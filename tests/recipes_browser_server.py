import ipaddress
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cloud_authority
import recipes
import tenant_storage
import work_server

tenant_storage.BASE = Path(sys.argv[1])
cloud_authority.CONFIG = tenant_storage.BASE / 'authority.json'
work_server.ROOT = Path(sys.argv[2])
work_server.nas_backup.request_backup = lambda: None
server = work_server.LocalWebServer(('127.0.0.1', 0), work_server.Handler)
server.allowed_hosts = {'127.0.0.1:' + str(server.server_port)}
server.allowed_networks = [ipaddress.ip_network('127.0.0.0/8')]
server.lan = False
print(server.server_port, flush=True)
server.serve_forever()
