"""Portable defaults shared by services and optional integrations."""
import os
from pathlib import Path

STATE_DIR = Path(os.environ.get('TIANGONG_STATE_DIR', str(Path.home() / '.local/share/tiangong'))).expanduser().resolve()
ADMIN_USERNAME = os.environ.get('TIANGONG_ADMIN_USERNAME', 'admin')
