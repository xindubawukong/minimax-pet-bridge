#!/usr/bin/env python3
"""Display weekly GitHub contributor and repository rankings on the USB board."""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import zlib
from pathlib import Path

from bridge import PetConnection, detect_port
from github_stats import GithubReader, from_snapshot


def encode_dashboard(report):
    payload = {key: report[key] for key in ("org", "since", "until", "generated_at", "repo_count", "commit_count")}
    for column in ("contributors", "repositories"):
        payload[column] = report[column][:6]
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    if len(data) > 7800:
        raise ValueError("Dashboard payload exceeds firmware limit")
    checksum = f"{zlib.crc32(data):08x}"
    return b"GHD1 " + checksum.encode() + b" " + data + b"\n", checksum


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
        # The hardware USB FIFO can lose a burst while RGB DMA interrupts run.
        # Small paced writes keep the packet intact; CRC/ACK still verify delivery.
        for offset in range(0, len(data), 64):
            self.command(data[offset:offset + 64])
            time.sleep(0.01)
        expected = f"GHD1 ACK {checksum} rows={min(6,len(report['contributors']))}/{min(6,len(report['repositories']))}"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            line = self.readline()
            if line == expected:
                return line
            if line.startswith("GHD1 ERROR"):
                raise RuntimeError(line)
        raise TimeoutError("Dashboard delivery was not acknowledged")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", default="ucrparlay")
    parser.add_argument("--port")
    parser.add_argument("--interval", type=float, default=1800, help="GitHub refresh interval, seconds (default 1800; minimum 300)")
    parser.add_argument("--snapshot", type=Path, help="Read an exported commits.json without querying GitHub")
    parser.add_argument("--cache-dir", type=Path, default=Path(__file__).parent / "private/github-cache")
    parser.add_argument("--json", action="store_true", help="Print rankings without opening USB")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval < 300:
        parser.error("--interval must be at least 300 seconds")
    reader = GithubReader(args.org, args.cache_dir)
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
