"""Run shipped unit tests with private state isolated from the developer account."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='tiangong-tests-') as state:
        env = dict(os.environ, TIANGONG_STATE_DIR=state, PYTHONPATH=str(ROOT/'scripts'), TIANGONG_ADMIN_USERNAME='admin')
        result = subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_*.py',*sys.argv[1:]], cwd=ROOT, env=env)
        raise SystemExit(result.returncode)
