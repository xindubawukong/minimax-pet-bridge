#!/usr/bin/env python3
"""Display weekly GitHub rankings and a live SSH CPU chart on the USB board."""
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import time
import zlib
from pathlib import Path

from bridge import PetConnection, detect_port
from github_stats import GithubReader, from_snapshot
from cpu_monitor import CpuMonitor, POINTS


def encode_dashboard(report):
    payload = {key: report[key] for key in ("org", "since", "until", "generated_at", "repo_count", "commit_count")}
    for column in ("contributors", "repositories"):
        payload[column] = report[column][:6]
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    if len(data) > 7800:
        raise ValueError("Dashboard payload exceeds firmware limit")
    checksum = f"{zlib.crc32(data):08x}"
    return b"GHD1 " + checksum.encode() + b" " + data + b"\n", checksum


def encode_cpu(report):
    data = json.dumps(report, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(data) > 7800:
        raise ValueError("CPU payload exceeds firmware limit")
    checksum = f"{zlib.crc32(data):08x}"
    return b"CPU1 " + checksum.encode() + b" " + data + b"\n", checksum


class DashboardConnection(PetConnection):
    def handshake(self, timeout=15):
        deadline, next_query = time.monotonic() + timeout, 0
        while time.monotonic() < deadline:
            if time.monotonic() >= next_query:
                self.command(b"dashboard.hello\n")
                next_query = time.monotonic() + 1
            line = self.readline()
            if line.startswith("GHD1 HELLO "):
                return line
            if line.startswith("SOSP9 HELLO"):
                raise RuntimeError("The original octopus firmware is installed; flash github_dashboard first")
        raise TimeoutError("No GHD1 handshake; check firmware and close other serial programs")

    def send(self, report):
        data, checksum = encode_dashboard(report)
        expected = f"GHD1 ACK {checksum} rows={min(6,len(report['contributors']))}/{min(6,len(report['repositories']))}"
        return self._send_frame(data, expected, "GHD1")

    def send_cpu(self, report):
        data, checksum = encode_cpu(report)
        return self._send_frame(data, f"CPU1 ACK {checksum}", "CPU1")

    def _send_frame(self, data, expected, prefix):
        for attempt in range(3):
            try:
                return self._send_attempt(data, expected, prefix)
            except (TimeoutError, RuntimeError) as error:
                if attempt == 2 or (isinstance(error, RuntimeError) and "ERROR checksum" not in str(error)):
                    raise

    def _send_attempt(self, data, expected, prefix):
        # The hardware USB FIFO can lose a burst while RGB DMA interrupts run.
        # Resynchronize an interrupted line, then send below its sustained rate.
        # Repeating a verified snapshot is safe if only the ACK was lost.
        self.command(b"\n")
        time.sleep(0.03)
        for offset in range(0, len(data), 32):
            self.command(data[offset:offset + 32])
            time.sleep(0.03)
        deadline = time.monotonic() + 10
        last_line = "(no response)"
        while time.monotonic() < deadline:
            line = self.readline()
            if line:
                last_line = line
            if line == expected:
                return line
            if line.startswith(prefix + " ERROR"):
                raise RuntimeError(line)
        raise TimeoutError(f"{prefix} delivery was not acknowledged; last response: {last_line}")


def run_live(args):
    """One USB owner; independent SSH sampler and interruptible GitHub process."""
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    latest = args.cache_dir / f"{args.org}-latest.json"
    output = args.cache_dir / f"{args.org}-refresh.json"
    errors = args.cache_dir / f"{args.org}-refresh.log"
    report, connection, refresh, monitor = None, None, None, None
    next_refresh = next_usb = next_cpu = 0
    dirty = False
    cpu_log_state = None
    cpu_sent_state = None
    cpu_heartbeat = 0
    try:
        if not args.no_cpu:
            monitor = CpuMonitor(args.cpu_host, args.cache_dir.parent / "cpu-history.json").start()
        try:
            report = json.loads(latest.read_text())
            if report["org"].casefold() != args.org.casefold():
                report = None
            else:
                encode_dashboard(report)
                dirty = True
        except (OSError, ValueError, KeyError, TypeError):
            report = None
        print(f"GitHub: every {args.interval:g}s; CPU: {args.cpu_host if monitor else 'disabled'} / sample 5s / sliding mean 20s / history 5min. Ctrl-C to stop.", flush=True)
        while True:
            now = time.monotonic()
            if refresh is None and now >= next_refresh:
                command = [sys.executable, str(Path(__file__).resolve()), "--json", "--org", args.org,
                           "--cache-dir", str(args.cache_dir)]
                if args.snapshot:
                    command += ["--snapshot", str(args.snapshot)]
                with output.open("w") as stdout, errors.open("a") as stderr:
                    refresh = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True)
                print("Refreshing GitHub in background...", flush=True)
            if refresh is not None and refresh.poll() is not None:
                try:
                    if refresh.returncode:
                        raise RuntimeError(f"fetch exited {refresh.returncode}; see {errors}")
                    updated = json.loads(output.read_text())
                    encode_dashboard(updated)
                    temporary = latest.with_suffix(".tmp")
                    temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2))
                    temporary.replace(latest)
                    report, dirty = updated, True
                    next_refresh = now + args.interval
                    print(f"GitHub ready: {report['commit_count']} commits / {report['repo_count']} repositories", flush=True)
                except (OSError, ValueError, KeyError, RuntimeError) as error:
                    print(f"GitHub refresh failed: {error}", file=sys.stderr, flush=True)
                    next_refresh = now + 300
                refresh = None
            if now >= next_usb:
                try:
                    if connection is None:
                        connection = DashboardConnection(args.port or detect_port())
                        hello = connection.handshake()
                        print(hello, flush=True)
                        if monitor and "cpu=2" not in hello:
                            raise RuntimeError("The 5-minute sliding CPU page requires the new firmware; build and flash it first (or use --no-cpu)")
                        dirty, next_cpu, cpu_sent_state = report is not None, 0, None
                    if dirty and report is not None:
                        print(connection.send(report), flush=True)
                        dirty = False
                    if monitor and now >= next_cpu:
                        snapshot = monitor.snapshot()
                        sample_state = (snapshot["end"], snapshot["last_sample"], snapshot["online"])
                        if sample_state != cpu_sent_state or now >= cpu_heartbeat:
                            connection.send_cpu(snapshot)
                            cpu_sent_state = sample_state
                            cpu_heartbeat = now + 5
                        state = (snapshot["end"] // 20, snapshot["online"])
                        if state != cpu_log_state:
                            value = snapshot["values"][-1]
                            current = "collecting..." if value is None else f"{value / 10:.1f}% ({value / 1000:.2f} CPUs)"
                            points = sum(value is not None for value in snapshot["values"])
                            print(f"CPU {args.cpu_host}: {'online' if snapshot['online'] else 'offline'} / {current} / {points}/{POINTS} sliding averages", flush=True)
                            cpu_log_state = state
                        # Follow new SSH samples promptly rather than letting a
                        # second independent 5s timer drift past their arrival.
                        next_cpu = now + .25
                except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as error:
                    print(f"USB sync failed: {error}", file=sys.stderr, flush=True)
                    if connection is not None:
                        connection.close()
                        connection = None
                    next_usb = now + 5
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopped (USB and CPU SSH sampler closed).", flush=True)
        return 0
    finally:
        if connection is not None:
            connection.close()
        if monitor:
            monitor.stop()
        if refresh is not None and refresh.poll() is None:
            try:
                os.killpg(refresh.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                refresh.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(refresh.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                refresh.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", default="ucrparlay")
    parser.add_argument("--port")
    parser.add_argument("--interval", type=float, default=1800, help="GitHub refresh interval, seconds (default 1800; minimum 300)")
    parser.add_argument("--snapshot", type=Path, help="Read an exported commits.json without querying GitHub")
    parser.add_argument("--cache-dir", type=Path, default=Path(__file__).parent / "private/github-cache")
    parser.add_argument("--json", action="store_true", help="Print rankings without opening USB")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--cpu-host", default="dynamic", help="Linux SSH host/alias (default dynamic)")
    parser.add_argument("--no-cpu", action="store_true", help="Only update the GitHub leaderboard")
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval < 300:
        parser.error("--interval must be at least 300 seconds")
    # Validate the organization before using it in cache paths in either mode.
    reader = GithubReader(args.org, args.cache_dir)
    if not args.once and not args.json:
        return run_live(args)
    connection = None
    report = None
    next_refresh = 0
    try:
        while True:
            updated = False
            if time.monotonic() >= next_refresh:
                try:
                    # Never replace good data with a partial refresh.
                    report = from_snapshot(args.snapshot, args.org) if args.snapshot else reader.read()
                    updated = True
                    next_refresh = time.monotonic() + args.interval
                    if args.json:
                        print(json.dumps(report, ensure_ascii=False, indent=2))
                        return 0
                    args.cache_dir.mkdir(parents=True, exist_ok=True)
                    (args.cache_dir / f"{args.org}-latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
                except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as error:
                    print(f"GitHub refresh failed: {error}", file=sys.stderr, flush=True)
                    if args.once or args.json:
                        return 1
                    next_refresh = time.monotonic() + 300
            if report is None:
                time.sleep(1)
                continue
            try:
                if connection is None:
                    connection = DashboardConnection(args.port or detect_port())
                    print(connection.handshake(), flush=True)
                    updated = True
                if updated:
                    print(connection.send(report), flush=True)
                    print(f"Confirmed: {report['commit_count']} non-merge commits across {report['repo_count']} repositories", flush=True)
                    if args.once:
                        return 0
                time.sleep(1)
            except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as error:
                print(f"GitHub dashboard failed: {error}", file=sys.stderr, flush=True)
                if connection is not None:
                    connection.close()
                    connection = None
                if args.once or args.json:
                    return 1
                time.sleep(5)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        return 0
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
