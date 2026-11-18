"""Decisions of the laptop-side monitor and the cost model, with the API and ssh replaced.

    python -m unittest discover -s infra/gpu/vast
"""

from argparse import Namespace
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import time
import unittest
from unittest import mock

import vast


def offer(**fields):
    base = {"id": 1, "gpu_name": "RTX 3090", "compute_cap": 860, "dph_total": 0.4, "total_flops": 35.0,
            "cpu_cores_effective": 16, "inet_down": 1000, "inet_down_cost": 0.004, "reliability2": 1.0}
    return {**base, **fields}


class Remote:
    """A fake instance: what `ssh` returns for the commands the monitor sends."""

    def __init__(self, final: str | None = None):
        self.final = final
        self.commands = []

    def ssh(self, target, command, timeout=120, stdout=subprocess.PIPE):
        self.commands.append(command)
        if stdout is not subprocess.PIPE:
            stdout.write(b"archive-bytes")
            return subprocess.CompletedProcess([], 0, b"", b"")
        if command.startswith("tail"):
            text = "== srv-01 (12:00:00)\n" + (self.final + "\n" if self.final else "")
            return subprocess.CompletedProcess([], 0, text.encode(), b"")
        if command.startswith("ls -1t"):
            return subprocess.CompletedProcess([], 0, b"/root/x/var/results-1.tar.gz\n", b"")
        return subprocess.CompletedProcess([], 0, b"", b"")


class MonitorTests(unittest.TestCase):
    def run_monitor(self, remote, instance, balance=4.0, reachable=True, **overrides):
        args = Namespace(instance=7, out=self.out, max_hours=6.0, reserve=0.3, poll_s=0, unreachable_min=30)
        for key, value in overrides.items():
            setattr(args, key, value)
        destroyed = []
        with mock.patch.object(vast, "show", side_effect=instance), \
                mock.patch.object(vast, "credit", return_value=balance), \
                mock.patch.object(vast, "reachable", return_value=("h", 22) if reachable else None), \
                mock.patch.object(vast, "ssh", side_effect=remote.ssh), \
                mock.patch.object(vast, "destroy", side_effect=lambda i, j=None: destroyed.append(i) or True), \
                mock.patch.object(vast.time, "sleep"):
            code = vast.command_monitor(args)
        return code, destroyed

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.out = self.directory.name

    def tearDown(self):
        self.directory.cleanup()

    def running(self, start=None):
        return {"actual_status": "running", "dph_total": 0.4, "start_date": start or time.time()}

    def test_guard_finished_copies_the_archive_then_destroys(self):
        remote = Remote(final='{"reason": "success", "elapsed_s": 10}')
        code, destroyed = self.run_monitor(remote, [self.running()])
        self.assertEqual((code, destroyed), (0, [7]))
        self.assertEqual((Path(self.out) / "results-1.tar.gz").read_bytes(), b"archive-bytes")
        self.assertTrue((Path(self.out) / "snapshot.tar.gz").exists())
        self.assertIn("ending instance: success", (Path(self.out) / "monitor.log").read_text())

    def test_keeps_watching_while_the_pipeline_runs(self):
        remote = Remote()
        code, destroyed = self.run_monitor(remote, [self.running(), self.running(), None])
        self.assertEqual((code, destroyed), (0, []))

    def test_credit_below_the_reserve_destroys(self):
        code, destroyed = self.run_monitor(Remote(), [self.running()], balance=0.2)
        self.assertEqual(destroyed, [7])

    def test_unreachable_destroys(self):
        code, destroyed = self.run_monitor(Remote(), [self.running()], reachable=False, unreachable_min=-1)
        self.assertEqual(destroyed, [7])
        self.assertIn("unreachable", (Path(self.out) / "monitor.log").read_text())

    def test_overdue_instance_destroys(self):
        old = time.time() - 7 * 3600
        code, destroyed = self.run_monitor(Remote(), [self.running(start=old)])
        self.assertEqual(destroyed, [7])

    def test_vanished_instance_ends_without_destroying(self):
        code, destroyed = self.run_monitor(Remote(), [None])
        self.assertEqual((code, destroyed), (0, []))


class ShowTests(unittest.TestCase):
    def test_destroyed_instance_is_absent(self):
        for reply in ({"instances": None}, None, {}):
            with self.subTest(reply=reply), mock.patch.object(vast, "call", return_value=reply):
                self.assertIsNone(vast.show(7))

    def test_live_instance_is_returned(self):
        with mock.patch.object(vast, "call", return_value={"instances": {"id": 7, "cur_state": "running"}}):
            self.assertEqual(vast.show(7)["id"], 7)


class OnstartTests(unittest.TestCase):
    def test_script_authorizes_the_key_once(self):
        script = vast.onstart_script("ssh-ed25519 AAAAexample user")
        self.assertTrue(script.startswith("#!/bin/bash\n"))
        self.assertIn("grep -qxF 'ssh-ed25519 AAAAexample user' /root/.ssh/authorized_keys", script)

    def test_rejects_anything_but_one_public_key(self):
        for text in ("SHA256:jPM+UVAO user", "ssh-ed25519 AAAA'; rm -rf / '", "ssh-ed25519 A\nssh-rsa B"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                vast.onstart_script(text)


class EstimateTests(unittest.TestCase):
    def test_reference_machine_takes_the_profile_hours(self):
        hours = vast.estimate(offer(inet_down=1e9), "A")["hours"]
        profile = vast.PROFILES["A"]
        self.assertAlmostEqual(hours, profile["fixed_h"] + profile["gpu_h"] + profile["cpu_h"], places=2)

    def test_more_cores_and_flops_cost_less_time(self):
        slow = vast.estimate(offer(cpu_cores_effective=8, total_flops=20), "B")
        fast = vast.estimate(offer(cpu_cores_effective=64, total_flops=80), "B")
        self.assertLess(fast["hours"], slow["hours"])

    def test_unreliable_machines_cost_more(self):
        self.assertGreater(vast.estimate(offer(reliability2=0.8), "A")["cost"], vast.estimate(offer(), "A")["cost"])


if __name__ == "__main__":
    unittest.main()
