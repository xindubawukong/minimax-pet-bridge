#!/usr/bin/env python3
"""Send local Codex token totals to the SOSP desktop pet over USB."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

from usage import UsageReader, default_codex_dir

ACK = re.compile(r"^SOSP9 ACK (\d+) (\d+) (\d+) (\d+) (\d+) state=(\d+) date=(.*)$")


def encode_report(report: dict, unix_time: int) -> tuple[bytes, tuple[int, ...]]:
    values = (unix_time, *(report["windows"][name]["tokens"] for name in ("hour", "day", "week", "month")))
    if any(type(v) is not int or v < 0 or v > 2**64 - 1 for v in values):
        raise ValueError("Time and usage must be unsigned 64-bit integers")
    return ("SYNC9 " + " ".join(map(str, values)) + "\n").encode("ascii"), values


def matching_ack(line: str, expected: tuple[int, ...]) -> bool:
    match = ACK.fullmatch(line)
    return match is not None and tuple(map(int, match.groups()[:5])) == expected


def detect_port() -> str:
    from serial.tools import list_ports
    ports = [p for p in list_ports.comports() if (p.vid, p.pid) == (0x303A, 0x1001)]
    if len(ports) != 1:
        raise RuntimeError(f"Found {len(ports)} Espressif USB ports; connect the pet or specify --port.")
    return ports[0].device


class PetConnection:
    def __init__(self, port: str):
        import serial
        self.serial = serial.Serial(port=None, baudrate=115200, timeout=0.25,
                                    write_timeout=3, exclusive=True)
        # USB Serial/JTAG needs DTR asserted; leave RTS inactive to avoid reset.
        self.serial.dtr = True
        self.serial.rts = False
        self.serial.port = port
        self.serial.open()
        self.pending = bytearray()

    def close(self):
        self.serial.close()

    def command(self, data: bytes):
        self.serial.write(data)
        self.serial.flush()

    def readline(self) -> str:
        # A serial timeout can occur in the middle of a line. Preserve that
        # fragment so a delayed or split ACK still matches the sent report.
        while True:
            end = self.pending.find(b"\n")
            if end >= 0:
                line = bytes(self.pending[:end])
                del self.pending[:end + 1]
                return line.decode("utf-8", errors="replace").strip()
            chunk = self.serial.read(max(1, min(self.serial.in_waiting, 4096)))
            if not chunk:
                return ""
            self.pending.extend(chunk)
            if len(self.pending) > 16384:
                raise RuntimeError("Oversized serial message; reconnecting.")

    def handshake(self, timeout: float = 60) -> str:
        deadline = time.monotonic() + timeout
        next_query = 0.0
        while time.monotonic() < deadline:
            if time.monotonic() >= next_query:
                self.command(b"sosp.hello\n")
                next_query = time.monotonic() + 2
            line = self.readline()
            if line.startswith("SOSP9 HELLO "):
                return line
        raise TimeoutError(
            "No SOSP9 handshake. Close other serial monitors; if the screen still works, "
            "unplug USB for 5 seconds and reconnect."
        )

    def send(self, report: dict) -> str:
        data, expected = encode_report(report, int(time.time()))
        self.command(data)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            line = self.readline()
            if matching_ack(line, expected):
                return line
            if line.startswith("SOSP9 ERROR"):
                raise RuntimeError(line)
        raise TimeoutError("No matching acknowledgment received; delivery is unconfirmed.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="USB serial port; auto-detected if omitted")
    parser.add_argument("--codex-dir", type=Path, default=default_codex_dir(),
                        help="Codex data directory (default: CODEX_HOME or ~/.codex)")
    parser.add_argument("--interval", type=float, default=10, help="Refresh interval in seconds (default: 10)")
    parser.add_argument("--once", action="store_true", help="Send once, confirm the ACK, then exit")
    parser.add_argument("--json", action="store_true", help="Print statistics without connecting to the pet")
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval < 1:
        parser.error("--interval must be at least one second")
    reader = UsageReader(args.codex_dir)
    if args.json:
        try:
            report = reader.read()
        except (OSError, ValueError) as error:
            print(f"Statistics failed: {error}", file=sys.stderr)
            return 1
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report["warnings"]:
            print("Incomplete statistics: " + "; ".join(report["warnings"]), file=sys.stderr)
            return 1
        return 0
    connection = None
    try:
        print("MiniMax Pet USB bridge — Ctrl-C to stop. First statistics scan may take a few seconds.", flush=True)
        while True:
            try:
                if connection is None:
                    port = args.port or detect_port()
                    print(f"Connecting to {port}...", flush=True)
                    connection = PetConnection(port)
                    print(connection.handshake(), flush=True)
                report = reader.read()
                if report["warnings"]:
                    raise RuntimeError("Incomplete statistics: " + "; ".join(report["warnings"]))
                ack = connection.send(report)
                totals = report["windows"]
                print(time.strftime("%H:%M:%S"), "Confirmed:",
                      " / ".join(f"{key} {totals[key]['tokens'] / 1_000_000:.2f}M" for key in ("hour", "day", "week", "month")),
                      flush=True)
                if args.once:
                    print(ack, flush=True)
                    return 0
                time.sleep(args.interval)
            except (OSError, RuntimeError, TimeoutError, ValueError) as error:
                print(f"Sync failed: {error}", file=sys.stderr, flush=True)
                if connection is not None:
                    connection.close()
                    connection = None
                if args.once:
                    return 1
                time.sleep(min(args.interval, 10))
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        return 0
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
