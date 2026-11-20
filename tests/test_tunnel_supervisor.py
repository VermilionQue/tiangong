import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import tunnel_supervisor as tunnel


class ReadinessTests(unittest.TestCase):
    def test_default_grace_and_three_consecutive_failures(self):
        policy = tunnel.ReadinessPolicy()
        policy.child_started(100)
        self.assertEqual(policy.interval, 30)
        self.assertFalse(policy.observe(130, False))
        self.assertEqual(policy.failures, 0)
        self.assertFalse(policy.observe(160, False))
        self.assertFalse(policy.observe(190, False))
        self.assertTrue(policy.observe(220, False))

    def test_healthy_sample_resets_short_outage(self):
        policy = tunnel.ReadinessPolicy()
        self.assertFalse(policy.observe(60, False))
        self.assertFalse(policy.observe(90, False))
        self.assertFalse(policy.observe(120, True))
        self.assertFalse(policy.observe(150, False))
        self.assertFalse(policy.observe(180, False))
        self.assertTrue(policy.observe(210, False))

    def test_recovery_cooldown_survives_new_child_start(self):
        policy = tunnel.ReadinessPolicy()
        policy.recovered(120)
        policy.child_started(121)
        self.assertFalse(policy.observe(150, False))
        for now in range(181, 420, 30):
            self.assertFalse(policy.observe(now, False))
        self.assertTrue(policy.observe(420, False))

    def test_process_exit_backoff_caps_and_recovers_after_stable_run(self):
        backoff = tunnel.ExitBackoff()
        self.assertEqual([backoff.next_delay(1) for _ in range(8)],
                         [10, 20, 40, 80, 160, 300, 300, 300])
        self.assertEqual(backoff.next_delay(300), 10)
        self.assertEqual(backoff.next_delay(1), 20)

    def test_ready_requires_positive_connection_count_not_just_http_200(self):
        self.assertTrue(tunnel.ready_response(200, b'{"readyConnections": 2}'))
        for status, body in [(503, b'{"readyConnections": 0}'), (302, b'{}'),
                             (200, b'hello'), (200, b'{}'), (200, b'null'),
                             (200, b'[]'), (200, b'{"readyConnections": 0}'),
                             (200, b'{"readyConnections": true}'),
                             (200, b'{"readyConnections": "2"}'),
                             (200, b' ' * 4097)]:
            self.assertFalse(tunnel.ready_response(status, body))

    def test_probe_only_calls_fixed_loopback_and_closes_socket(self):
        with patch.object(tunnel.http.client, 'HTTPConnection') as factory:
            connection = factory.return_value
            response = connection.getresponse.return_value
            response.status = 200
            response.read.return_value = b'{"readyConnections": 1}'
            self.assertTrue(tunnel.probe_ready())
            factory.assert_called_once_with('127.0.0.1', 20241, timeout=3)
            connection.request.assert_called_once_with('GET', '/ready')
            response.read.assert_called_once_with(4097)
            connection.close.assert_called_once()
        with patch.object(tunnel.http.client, 'HTTPConnection') as factory:
            factory.return_value.request.side_effect = OSError('private detail')
            self.assertFalse(tunnel.probe_ready())
            factory.return_value.close.assert_called_once()


class ChildLifecycleTests(unittest.TestCase):
    def wait_until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail('isolated child condition timed out')

    def supervisor(self, probe, command=None, factory=None):
        children = []
        logs = []
        def popen(cmd, **kwargs):
            process = subprocess.Popen(factory(cmd) if factory else cmd, **kwargs)
            children.append(process)
            return process
        policy = tunnel.ReadinessPolicy(interval=0.04, startup_grace=0.08,
                                        restart_cooldown=0.5)
        supervisor = tunnel.TunnelSupervisor(
            command or [sys.executable, '-c', 'import time;time.sleep(30)'],
            policy=policy, backoff=tunnel.ExitBackoff(initial=0.04, maximum=0.1),
            probe=probe, popen=popen, logger=logs.append, stop_grace=0.2)
        errors = []
        def run():
            try:
                supervisor.run()
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        def cleanup():
            supervisor.stop.set()
            thread.join(5)
            for child in children:
                tunnel.stop_child(child, 0.1)
            self.assertFalse(thread.is_alive(), 'supervisor did not stop')
            self.assertFalse(errors)
        self.addCleanup(cleanup)
        return supervisor, children, logs, thread

    def test_failed_readiness_restarts_only_own_child_and_reaps_it(self):
        samples = [False] * 4
        supervisor, children, logs, thread = self.supervisor(
            lambda: samples.pop(0) if samples else True)
        self.wait_until(lambda: len(children) == 2)
        self.assertIsNotNone(children[0].poll())
        self.assertIsNone(children[1].poll())
        self.assertEqual(len(logs), 1)
        self.assertIn('readiness_unavailable', logs[0])
        supervisor.stop.set()
        thread.join(3)
        self.assertTrue(all(child.poll() is not None for child in children))

    def test_healthy_and_short_blip_are_silent_without_restart(self):
        samples = [True, False, False, True, True, True, True, True]
        supervisor, children, logs, thread = self.supervisor(
            lambda: samples.pop(0) if samples else True)
        self.wait_until(lambda: not samples)
        self.assertEqual(len(children), 1)
        self.assertEqual(logs, [])
        supervisor.stop.set()
        thread.join(3)
        self.assertIsNotNone(children[0].poll())

    def test_unexpected_exit_restarts_child_after_backoff(self):
        launches = []
        def command_factory(command):
            launches.append(time.monotonic())
            return ([sys.executable, '-c', 'raise SystemExit(7)']
                    if len(launches) == 1 else command)
        supervisor, children, logs, thread = self.supervisor(
            lambda: True, factory=command_factory)
        self.wait_until(lambda: len(children) == 2)
        self.assertEqual(children[0].returncode, 7)
        self.assertGreaterEqual(launches[1] - launches[0], 0.04)
        self.assertIn('child_exited code=7', logs[0])
        supervisor.stop.set()
        thread.join(3)
        self.assertTrue(all(child.poll() is not None for child in children))

    def test_start_failure_logs_no_exception_secret_and_stops_during_backoff(self):
        stop = threading.Event()
        logs = []
        def fail(*args, **kwargs):
            raise OSError('credential-must-not-appear')
        def capture(message):
            logs.append(message)
            stop.set()
        supervisor = tunnel.TunnelSupervisor(['/not-a-command', 'private-token'],
                                             stop=stop, popen=fail, logger=capture)
        self.assertEqual(supervisor.run(), 0)
        self.assertEqual(logs, ['child_start_failed retry_in_seconds=10'])

    def test_stubborn_child_is_killed_and_reaped(self):
        with tempfile.TemporaryDirectory() as temp:
            ready = Path(temp) / 'ready'
            code = ('import signal,time,pathlib;'
                    'signal.signal(signal.SIGTERM,signal.SIG_IGN);'
                    'pathlib.Path(' + repr(str(ready)) + ').touch();time.sleep(30)')
            child = subprocess.Popen([sys.executable, '-c', code])
            try:
                self.wait_until(ready.exists)
                tunnel.stop_child(child, grace=0.05)
                self.assertEqual(child.returncode, -signal.SIGKILL)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child.pid, 0)
            finally:
                tunnel.stop_child(child, grace=0.05)

    def test_real_cli_sigterm_reaps_child_and_preserves_cloudflared_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            binary = folder / 'fake-cloudflared'
            evidence = folder / 'child.json'
            config = folder / 'private-config.yml'
            binary.write_text('#!' + sys.executable + '\n' +
                              'import json,os,pathlib,sys,time\n' +
                              'pathlib.Path(' + repr(str(evidence)) + ').write_text('\
                              'json.dumps({"pid":os.getpid(),"args":sys.argv[1:]}))\n' +
                              'time.sleep(30)\n')
            binary.chmod(0o700)
            process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/tunnel_supervisor.py'),
                                        '--binary', str(binary), '--config', str(config)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            child_pid = None
            try:
                self.wait_until(lambda: evidence.exists() and evidence.stat().st_size > 0)
                data = json.loads(evidence.read_text())
                child_pid = data['pid']
                self.assertEqual(data['args'], ['--no-autoupdate', 'tunnel', '--config',
                                               str(config), 'run'])
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0)
                self.assertEqual(stdout, b'')
                self.assertEqual(stderr, b'')
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=5)
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


if __name__ == '__main__':
    unittest.main()
