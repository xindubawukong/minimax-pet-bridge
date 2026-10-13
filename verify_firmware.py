#!/usr/bin/env python3
"""Verify the bundled firmware locally. Does not connect to or flash a device."""

import hashlib
import json
import sys
from pathlib import Path


def main() -> int:
    directory = Path(__file__).resolve().parent / "firmware"
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        filename = manifest["file"]
        if Path(filename).name != filename:
            raise ValueError("Firmware filename must refer to a file inside firmware/")
        data = (directory / filename).read_bytes()
        if len(data) != manifest["size_bytes"]:
            raise ValueError(f"Unexpected size: {len(data)} bytes")
        digest = hashlib.sha256(data).hexdigest()
        if digest != manifest["sha256"]:
            raise ValueError("SHA-256 mismatch")
        print(f"OK: {filename} ({len(data):,} bytes)")
        print(f"SHA-256: {digest}")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Firmware verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
