#!/usr/bin/env python3
"""Keep this service's cloudflared child connected without adding a scheduler.

Only the child's loopback readiness endpoint is read. No account data,
credentials, proxy settings, or public endpoints are inspected. Healthy checks
are silent; launchd continues to own this one existing tunnel service.
"""
import argparse
from dataclasses import dataclass
import http.client
import json
import signal
import subprocess
import sys
import threading
import time
from typing import Optional


READY_HOST = '127.0.0.1'
READY_PORT = 20241
READY_PATH = '/ready'


def ready_response(status, body):
    """A successful page alone is not proof of an active tunnel connection."""
    if status != 200 or len(body) > 4096:
        return False
    try:
        payload = json.loads(body)
        count = payload.get('readyConnections')
        return type(count) is int and count > 0
    except (ValueError, TypeError, AttributeError):
        return False


def probe_ready():
    # HTTPConnection avoids HTTP_PROXY/ALL_PROXY and never follows redirects.
    connection = http.client.HTTPConnection(READY_HOST, READY_PORT, timeout=3)
    try:
        connection.request('GET', READY_PATH)
        response = connection.getresponse()
        return ready_response(response.status, response.read(4097))
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


@dataclass
class ReadinessPolicy:
    interval: float = 30
    startup_grace: float = 60
    failure_limit: int = 3
    restart_cooldown: float = 300
    started_at: float = 0
    failures: int = 0
    last_recovery_at: Optional[float] = None

    def child_started(self, now):
        self.started_at = now
        self.failures = 0

    def observe(self, now, healthy):
        if healthy or now - self.started_at < self.startup_grace:
            self.failures = 0
            return False
        self.failures += 1
        cooled = (self.last_recovery_at is None or
                  now - self.last_recovery_at >= self.restart_cooldown)
        return self.failures >= self.failure_limit and cooled

    def recovered(self, now):
        self.last_recovery_at = now
        self.child_started(now)


@dataclass
class ExitBackoff:
    initial: float = 10
    maximum: float = 300
    stable_after: float = 300
    failures: int = 0

    def next_delay(self, lifetime):
        if lifetime >= self.stable_after:
            self.failures = 0
        delay = min(self.maximum, self.initial * (2 ** min(self.failures, 10)))
        self.failures += 1
        return delay


def stop_child(child, grace=10):
    """Terminate and reap only the Popen child owned by this supervisor."""
    if child is None:
        return
    if child.poll() is None:
        try:
            child.terminate()
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try:
                child.kill()
            except ProcessLookupError:
                pass
            child.wait()
    else:
        child.wait()


def log_event(message):
    # Never log the command, environment, configuration, or exception contents.
    stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    print(stamp + ' tunnel-supervisor ' + message, file=sys.stderr, flush=True)


class TunnelSupervisor:
    def __init__(self, command, *, policy=None, backoff=None, stop=None,
                 probe=probe_ready, clock=time.monotonic, popen=subprocess.Popen,
                 logger=log_event, stop_grace=10):
        self.command = list(command)
        self.policy = policy or ReadinessPolicy()
        self.backoff = backoff or ExitBackoff()
        self.stop = stop or threading.Event()
        self.probe = probe
        self.clock = clock
        self.popen = popen
        self.log = logger
        self.stop_grace = stop_grace
        self.child = None

    def run(self):
        try:
            while not self.stop.is_set():
                try:
                    self.child = self.popen(self.command, stdin=subprocess.DEVNULL,
                                            close_fds=True)
                except OSError:
                    delay = self.backoff.next_delay(0)
                    self.log('child_start_failed retry_in_seconds=' + str(delay))
                    self.stop.wait(delay)
                    continue
                started = self.clock()
                self.policy.child_started(started)
                next_check = started + self.policy.interval
                restart_for_health = False
                while not self.stop.is_set():
                    result = self.child.poll()
                    if result is not None:
                        self.child.wait()
                        self.child = None
                        delay = self.backoff.next_delay(self.clock() - started)
                        self.log('child_exited code=' + str(result) +
                                 ' retry_in_seconds=' + str(delay))
                        self.stop.wait(delay)
                        break
                    now = self.clock()
                    if now >= next_check:
                        healthy = self.probe()
                        now = self.clock()
                        next_check = now + self.policy.interval
                        if self.stop.is_set():
                            break
                        if self.policy.observe(now, healthy):
                            self.log('readiness_unavailable restarting_owned_child')
                            stop_child(self.child, self.stop_grace)
                            self.child = None
                            self.policy.recovered(self.clock())
                            restart_for_health = True
                            break
                    # A short interruptible wait also detects child exits promptly.
                    self.stop.wait(min(1, max(0, next_check - self.clock())))
                if self.stop.is_set():
                    break
                if restart_for_health:
                    continue
        finally:
            stop_child(self.child, self.stop_grace)
            self.child = None
        return 0


def main():
    parser = argparse.ArgumentParser(description='Supervise the existing tunnel child')
    parser.add_argument('--binary', required=True)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    stop = threading.Event()

    def shutdown(_signum, _frame):
        stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, shutdown)
    command = [args.binary, '--no-autoupdate', 'tunnel', '--config', args.config, 'run']
    return TunnelSupervisor(command, stop=stop).run()


if __name__ == '__main__':
    sys.exit(main())
