#!/usr/bin/env python3
"""Save the board's rendered RGB565 framebuffer as a PNG (no extra packages)."""
import argparse
import struct
import time
import zlib
from pathlib import Path

from bridge import detect_port
from github_bridge import DashboardConnection


def capture(connection):
    connection.command(b"dashboard.capture\n")
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        line = connection.readline()
        if line.startswith("GHD1 ERROR"):
            raise RuntimeError(line)
        if line.startswith("GHD1 FRAME "):
            _, _, width, height, size = line.split()
            width, height, size = int(width), int(height), int(size)
            if (width, height, size) != (800, 480, 768000):
                raise ValueError("Unexpected framebuffer geometry")
            break
    else:
        raise TimeoutError("No framebuffer header")
    raw = bytearray(connection.pending)
    connection.pending.clear()
    while len(raw) < size and time.monotonic() < deadline:
        raw.extend(connection.serial.read(min(4096, size - len(raw))))
    if len(raw) != size:
        raise TimeoutError(f"Incomplete framebuffer: {len(raw)}/{size} bytes")
    while time.monotonic() < deadline:
        if connection.readline() == "GHD1 FRAME END":
            break
    else:
        raise TimeoutError("No framebuffer trailer")
    rgb = bytearray()
    for (pixel,) in struct.iter_unpack("<H", raw):
        r, g, b = (pixel >> 11) & 31, (pixel >> 5) & 63, pixel & 31
        rgb.extend(((r * 255 + 15) // 31, (g * 255 + 31) // 63, (b * 255 + 15) // 31))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    scan = b"".join(b"\0" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(scan)) + chunk(b"IEND", b""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port")
    parser.add_argument("--output", type=Path, default=Path("private/github-dashboard-device.png"))
    args = parser.parse_args()
    connection = DashboardConnection(args.port or detect_port())
    try:
        print(connection.handshake(), flush=True)
        png = capture(connection)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(png)
        print(args.output.resolve(), flush=True)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
