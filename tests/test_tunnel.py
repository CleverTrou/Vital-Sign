"""tunnel.sh and the start/stop scripts' handling of it, against a fake ssh.

The fake ssh on PATH records its arguments and PID, then either stays up
(a connected tunnel) or fails at once (an unreachable host). Nothing here
touches the network.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")

FAKE_SSH = textwrap.dedent("""\
    #!/bin/sh
    printf '%s\\n' "$@" > "$FAKE_SSH_ARGS"
    echo $$ >> "$FAKE_SSH_PIDS"
    if [ "$FAKE_SSH_MODE" = fail ]; then
      echo "ssh: connect to host example port 22: Operation timed out" >&2
      exit 255
    fi
    exec sleep 300
""")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill 0; ps shows it as state Z.
    state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout
    return bool(state.strip()) and not state.strip().startswith("Z")


def wait_for(predicate, timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


@unittest.skipIf(BASH is None or sys.platform == "win32", "needs bash")
class TunnelTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        bindir = self.tmp / "bin"
        bindir.mkdir()
        ssh = bindir / "ssh"
        ssh.write_text(FAKE_SSH)
        ssh.chmod(0o755)
        self.args_file = self.tmp / "ssh-args"
        self.pids_file = self.tmp / "ssh-pids"
        self.log = self.tmp / "bridge.log"
        self.env = {
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_SSH_ARGS": str(self.args_file),
            "FAKE_SSH_PIDS": str(self.pids_file),
            "FAKE_SSH_MODE": "stay",
            "VITALSIGN_TUNNEL_UP_AFTER": "1",
            "VITALSIGN_TUNNEL_RETRY_MIN": "1",
            "VITALSIGN_TUNNEL_RETRY_MAX": "1",
        }

    def log_text(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def ssh_pids(self) -> list[int]:
        return [int(p) for p in self.pids_file.read_text().split()] if self.pids_file.exists() else []

    def stop_all_fake_ssh(self):
        for pid in self.ssh_pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class TunnelScriptTest(TunnelTestCase):
    def start(self, host="user@example", port="8765"):
        proc = subprocess.Popen([BASH, str(ROOT / "tunnel.sh"), host, port, str(self.log)], env=self.env)
        self.addCleanup(self.stop_all_fake_ssh)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def test_opens_a_reverse_forward_without_prompting(self):
        proc = self.start()
        self.assertTrue(wait_for(lambda: "Tunnel open" in self.log_text()), self.log_text())
        args = self.args_file.read_text().split("\n")
        self.assertIn("-N", args)
        self.assertEqual(args[args.index("-R") + 1], "8765:localhost:8765")
        self.assertIn("BatchMode=yes", args)
        self.assertIn("ExitOnForwardFailure=yes", args)
        # The host comes after "--", so it can never be read as an option.
        self.assertEqual(args[args.index("--") + 1], "user@example")

        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=5), 0)
        (ssh_pid,) = self.ssh_pids()
        self.assertTrue(wait_for(lambda: not alive(ssh_pid), 5), "ssh outlived tunnel.sh")
        self.assertIn("closed", self.log_text())

    def test_a_host_that_stays_unreachable_is_logged_once(self):
        self.env["FAKE_SSH_MODE"] = "fail"
        self.env["VITALSIGN_TUNNEL_UP_AFTER"] = "30"
        proc = self.start()
        self.assertTrue(wait_for(lambda: len(self.ssh_pids()) >= 3, 15), "did not retry")
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
        log = self.log_text()
        self.assertEqual(log.count("failed:"), 1, log)
        self.assertIn("Operation timed out", log)
        self.assertNotIn("Tunnel open", log)

    def test_stops_promptly_while_waiting_to_retry(self):
        self.env["FAKE_SSH_MODE"] = "fail"
        self.env["VITALSIGN_TUNNEL_UP_AFTER"] = "30"
        self.env["VITALSIGN_TUNNEL_RETRY_MIN"] = "60"
        self.env["VITALSIGN_TUNNEL_RETRY_MAX"] = "60"
        proc = self.start()
        self.assertTrue(wait_for(lambda: "failed:" in self.log_text()))
        started = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
        self.assertLess(time.monotonic() - started, 3)

    def test_refuses_a_host_that_looks_like_an_option(self):
        proc = self.start(host="-oProxyCommand=touch /tmp/pwned")
        self.assertEqual(proc.wait(timeout=5), 2)
        self.assertFalse(self.args_file.exists(), "ssh was run")
        self.assertIn("ERROR", self.log_text())

    def test_refuses_a_port_that_is_not_a_number(self):
        proc = self.start(port="8765; id")
        self.assertEqual(proc.wait(timeout=5), 2)
        self.assertFalse(self.args_file.exists(), "ssh was run")


class StartStopScriptsTest(TunnelTestCase):
    """start_bridge.sh / stop_bridge.sh in a scratch copy with a stand-in bridge."""

    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo dir"  # a space, as in "Vital Sign"
        (self.repo / ".venv" / "bin").mkdir(parents=True)
        for name in ("start_bridge.sh", "stop_bridge.sh", "tunnel.sh"):
            shutil.copy(ROOT / name, self.repo / name)
        # Sleeps instead of opening Bluetooth; the scripts only care that
        # "<repo>/bridge.py" is in its command line.
        (self.repo / "bridge.py").write_text("import time\ntime.sleep(300)\n")
        (self.repo / ".venv" / "bin" / "python3").symlink_to(sys.executable)
        self.log = self.repo / "bridge.log"
        # Cleanups run last-registered first: stop_bridge.sh, then the fake
        # ssh, then (if stop_bridge.sh is the thing that's broken) a hard
        # kill of whatever its PID files still name, so a failing test
        # can't leave a tunnel.sh loop retrying in the background.
        self.addCleanup(self.kill_leftovers)
        self.addCleanup(self.stop_all_fake_ssh)
        self.addCleanup(self.run_script, "stop_bridge.sh")

    def kill_leftovers(self):
        for name in (".tunnel.pid", ".bridge.pid"):
            pid = self.pid(name)
            if pid and alive(pid):
                os.kill(pid, signal.SIGKILL)

    def run_script(self, name):
        subprocess.run([BASH, str(self.repo / name)], env=self.env, check=True, timeout=30)

    def pid(self, name) -> int | None:
        path = self.repo / name
        return int(path.read_text()) if path.exists() else None

    def configure(self, text):
        (self.repo / "config.sh").write_text(text)

    def test_no_tunnel_unless_configured(self):
        self.configure('SOURCES="demo"\n')
        self.run_script("start_bridge.sh")
        self.assertIsNotNone(self.pid(".bridge.pid"))
        self.assertIsNone(self.pid(".tunnel.pid"))

    def test_start_is_idempotent_and_stop_closes_everything(self):
        self.configure('SOURCES="demo"\nTUNNEL_HOST="user@example"\n')
        self.run_script("start_bridge.sh")
        bridge, tunnel = self.pid(".bridge.pid"), self.pid(".tunnel.pid")
        self.assertTrue(wait_for(lambda: "Tunnel open" in self.log_text()), self.log_text())

        self.run_script("start_bridge.sh")  # e.g. OBS automation re-checking
        self.assertEqual((self.pid(".bridge.pid"), self.pid(".tunnel.pid")), (bridge, tunnel))
        self.assertEqual(len(self.ssh_pids()), 1)

        self.run_script("stop_bridge.sh")
        (ssh_pid,) = self.ssh_pids()
        for pid in (bridge, tunnel, ssh_pid):
            self.assertTrue(wait_for(lambda: not alive(pid), 5), f"{pid} still running")
        self.assertIsNone(self.pid(".bridge.pid"))
        self.assertIsNone(self.pid(".tunnel.pid"))

    def test_adding_a_tunnel_host_needs_no_bridge_restart(self):
        self.configure('SOURCES="demo"\n')
        self.run_script("start_bridge.sh")
        bridge = self.pid(".bridge.pid")
        self.configure('SOURCES="demo"\nTUNNEL_HOST="user@example"\n')
        self.run_script("start_bridge.sh")
        self.assertEqual(self.pid(".bridge.pid"), bridge)
        self.assertIsNotNone(self.pid(".tunnel.pid"))

    def test_stop_closes_a_tunnel_whose_bridge_already_died(self):
        self.configure('SOURCES="demo"\nTUNNEL_HOST="user@example"\n')
        self.run_script("start_bridge.sh")
        bridge, tunnel = self.pid(".bridge.pid"), self.pid(".tunnel.pid")
        os.kill(bridge, signal.SIGKILL)
        self.assertTrue(wait_for(lambda: not alive(bridge), 5))
        self.run_script("stop_bridge.sh")
        self.assertTrue(wait_for(lambda: not alive(tunnel), 5))


if __name__ == "__main__":
    unittest.main()
