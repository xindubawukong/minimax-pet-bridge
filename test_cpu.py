import json
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from cpu_monitor import CpuHistory, CpuMonitor, cpu_percent
from github_bridge import DashboardConnection, encode_cpu, run_live


def counter(monotonic, total, idle, boot="boot", cpus=192):
    return dict(monotonic=monotonic, total=total, idle=idle, boot=boot, cpus=cpus)


class CpuTests(unittest.TestCase):
    def test_cpu_delta_and_idle_wait(self):
        before = counter(0, 1000, 500)
        self.assertEqual(cpu_percent(before, counter(5, 2000, 1250)), 25)
        self.assertIsNone(cpu_percent(before, counter(5, 20, 10)))
        self.assertIsNone(cpu_percent(before, counter(5, 2000, 400)))
        self.assertIsNone(cpu_percent(before, counter(5, 2000, 1250, boot="reboot")))
        self.assertIsNone(cpu_percent(before, counter(10, 2000, 1250)))

    def test_twenty_second_means_advance_five_seconds_and_overlap_fifteen(self):
        history = CpuHistory("dynamic")
        total, idle = 1000, 500
        history.add(counter(0, total, idle), 1000)
        for i, percent in enumerate([0, 25, 50, 100, 50, 75], 1):
            total += 1000
            idle += 1000 - percent * 10
            history.add(counter(i * 5, total, idle), 1000 + i * 5)
            if i < 4:
                self.assertTrue(all(v is None for v in history.snapshot(1000 + i * 5)["values"]))
            else:
                snapshot = history.snapshot(1000 + i * 5)
                self.assertEqual(snapshot["values"][-1], [84000, 108000, 132000][i - 4])
                self.assertEqual(snapshot["end"], 1000 + i * 5)
        snapshot = history.snapshot(1030)
        self.assertEqual(snapshot["values"], [None] * 57 + [84000, 108000, 132000])
        self.assertEqual((snapshot["step"], snapshot["window"]), (5, 20))
        self.assertTrue(snapshot["online"])
        self.assertFalse(history.snapshot(1046)["online"])

    def test_startup_partial_bucket_and_rolling_expiration(self):
        history = CpuHistory("dynamic")
        for i in range(7):
            history.add(counter(i * 5, 1000 + i * 1000, 500 + i * 500), 1005 + i * 5)
        self.assertTrue(all(v is None for v in history.snapshot(1020)["values"]))
        for i in range(7, 128):
            history.add(counter(i * 5, 1000 + i * 1000, 500 + i * 500), 1005 + i * 5)
        snapshot = history.snapshot(1640)
        self.assertEqual(snapshot["values"], [96000] * 60)
        self.assertLessEqual(len(history.buckets), 64)

    def test_disconnect_creates_gap_instead_of_zero_or_interpolation(self):
        history = CpuHistory("dynamic")
        history.add(counter(0, 1000, 500), 1000)
        history.add(counter(5, 2000, 1000), 1005)
        history.disconnect()
        history.add(counter(0, 5000, 2500), 1015)
        history.add(counter(5, 6000, 3000), 1020)
        self.assertIsNone(history.snapshot(1020)["values"][-1])
        for i in range(2, 6):
            history.add(counter(i * 5, 5000 + i * 1000, 2500 + i * 500), 1015 + i * 5)
        self.assertEqual(history.snapshot(1040)["values"][-3:], [None, 96000, 96000])

    def test_wall_clock_boundary_waits_for_last_sample_without_flashing_gap(self):
        history = CpuHistory("dynamic")
        for i in range(8):
            history.add(counter(i * 5, 1000 + i * 1000, 500 + i * 500), 1000 + i * 5)
        # Wall clock has crossed 1040, but the last received interval ends 1035.
        self.assertEqual(history.snapshot(1041)["end"], 1035)
        self.assertEqual(history.snapshot(1041)["values"][-1], 96000)
        history.add(counter(40, 9000, 4500), 1042)
        self.assertEqual(history.snapshot(1042)["end"], 1040)
        self.assertEqual(history.snapshot(1042)["values"][-1], 96000)

    def test_saved_history_does_not_claim_live_connection(self):
        path = Mock()
        path.exists.return_value = True
        path.read_text.return_value = json.dumps(dict(version=2, host="dynamic", cpus=192,
                                                      buckets={str(i): [250, 5] for i in range(200, 204)}))
        with patch("cpu_monitor.time.time", return_value=1020):
            history = CpuHistory("dynamic", path)
            snapshot = history.snapshot()
        self.assertFalse(snapshot["online"])
        self.assertEqual(snapshot["values"][-1], 96000)

    def test_old_twenty_second_cache_cannot_fabricate_sliding_history(self):
        path = Mock()
        path.exists.return_value = True
        path.read_text.return_value = json.dumps(dict(version=1, host="dynamic", cpus=192,
                                                      buckets={"50": [1000, 20]}))
        with patch("cpu_monitor.time.time", return_value=1020):
            snapshot = CpuHistory("dynamic", path).snapshot()
        self.assertEqual(snapshot["values"], [None] * 60)

    def test_cpu_checksum_and_exact_ack(self):
        report = CpuHistory("dynamic").snapshot(1020)
        frame, checksum = encode_cpu(report)
        prefix, received, data = frame.rstrip(b"\n").split(b" ", 2)
        self.assertEqual(prefix, b"CPU1")
        self.assertEqual(received.decode(), f"{zlib.crc32(data):08x}")
        connection = object.__new__(DashboardConnection)
        connection.command = Mock()
        connection.readline = Mock(side_effect=["CPU1 ACK deadbeef", f"CPU1 ACK {checksum}"])
        self.assertEqual(connection.send_cpu(report), f"CPU1 ACK {checksum}")

    def test_ssh_host_cannot_inject_options(self):
        for host in ["-oProxyCommand=evil", "dynamic extra", ""]:
            with self.assertRaises(ValueError):
                CpuMonitor(host)

    def test_cpu_updates_during_pending_github_fetch_and_ctrl_c_closes_all(self):
        cache = MagicMock()
        cache.__truediv__.return_value.read_text.side_effect = FileNotFoundError
        args = SimpleNamespace(cache_dir=cache, org="lab", no_cpu=False, cpu_host="dynamic",
                               snapshot=None, interval=1800, port="test-port")
        monitor, connection, refresh = Mock(), Mock(), Mock()
        monitor.start.return_value = monitor
        monitor.snapshot.return_value = CpuHistory("dynamic").snapshot(1020)
        connection.handshake.return_value = "GHD1 HELLO cpu=2 pages=2"
        refresh.poll.return_value = None
        refresh.pid = 12345
        with patch("github_bridge.CpuMonitor", return_value=monitor), \
             patch("github_bridge.DashboardConnection", return_value=connection), \
             patch("github_bridge.subprocess.Popen", return_value=refresh), \
             patch("github_bridge.os.killpg") as kill, \
             patch("github_bridge.time.monotonic", side_effect=[0, 6]), \
             patch("github_bridge.time.sleep", side_effect=[None, KeyboardInterrupt]), \
             patch("builtins.print"):
            self.assertEqual(run_live(args), 0)
        self.assertEqual(connection.send_cpu.call_count, 2)
        monitor.stop.assert_called_once()
        connection.close.assert_called_once()
        kill.assert_called_once_with(12345, 15)


if __name__ == "__main__":
    unittest.main()
