#!/usr/bin/env python3
"""Build the 4.3C-BOX dashboard on macOS; download pinned tools into ignored build/."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
CLI_VERSION = "1.5.1"
CORE_VERSION = "3.1.1"
CTAGS_COMMIT = "abc8fca7499f44c725122881cd380a88c37abe0e"
FQBN = "esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,FlashSize=16M,PSRAM=opi,PartitionScheme=huge_app,FlashMode=qio,LoopCore=0"


def run(*command, cwd=ROOT):
    subprocess.run([str(x) for x in command], cwd=cwd, check=True)


def main():
    if platform.system() != "Darwin":
        raise SystemExit("This bootstrap currently supports macOS.")
    tools = BUILD / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    cli = tools / "arduino-cli"
    if not cli.exists():
        arch = "ARM64" if platform.machine() == "arm64" else "64bit"
        filename = f"arduino-cli_{CLI_VERSION}_macOS_{arch}.tar.gz"
        archive = tools / filename
        base = f"https://github.com/arduino/arduino-cli/releases/download/v{CLI_VERSION}"
        checksums = tools / f"{CLI_VERSION}-checksums.txt"
        run("curl", "-fLsS", f"{base}/{filename}", "-o", archive)
        run("curl", "-fLsS", f"{base}/{checksums.name}", "-o", checksums)
        expected = next(line.split()[0] for line in checksums.read_text().splitlines() if line.split()[-1] == filename)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Arduino CLI checksum mismatch")
        with tarfile.open(archive) as package:
            cli.write_bytes(package.extractfile("arduino-cli").read())
        cli.chmod(0o755)
    config = BUILD / "arduino-cli.yaml"
    data, user = BUILD / "arduino-data", BUILD / "arduino-user"
    config.write_text(json.dumps({
        "board_manager": {"additional_urls": ["https://espressif.github.io/arduino-esp32/package_esp32_index.json"]},
        "directories": {"data": str(data), "user": str(user), "downloads": str(BUILD / "arduino-downloads")},
    }, indent=2))
    options = ("--config-file", config)
    if not (data / "packages/esp32/hardware/esp32" / CORE_VERSION).is_dir():
        run(cli, "core", "update-index", *options)
        run(cli, "core", "install", f"esp32:esp32@{CORE_VERSION}", *options)
    for library, version in (("lvgl", "8.4.0"), ("ArduinoJson", "7.4.2")):
        props = user / "libraries" / library / "library.properties"
        if not props.exists() or f"version={version}" not in props.read_text():
            run(cli, "lib", "install", f"{library}@{version}", *options)
    extra = []
    if platform.machine() == "arm64":
        ctags = BUILD / "arduino-ctags"
        if not ctags.exists():
            run("git", "clone", "--depth", "1", "--branch", "5.8-arduino11", "https://github.com/arduino/ctags.git", ctags)
        revision = subprocess.check_output(["git", "-C", str(ctags), "rev-parse", "HEAD"], text=True).strip()
        if revision != CTAGS_COMMIT:
            raise RuntimeError("Unexpected Arduino ctags revision")
        if not (ctags / "ctags").exists():
            # Old reserved macro names conflict with modern macOS SDK headers.
            for path in ctags.iterdir():
                if path.suffix in (".c", ".h"):
                    source = path.read_bytes()
                    patched = source.replace(b"__unused__", b"CTAGS_UNUSED").replace(b"__printf__", b"CTAGS_PRINTF")
                    if patched != source:
                        path.write_bytes(patched)
            run("./configure", cwd=ctags)
            run("make", "-j6", cwd=ctags)
        extra = ["--build-property", f"runtime.tools.ctags.path={ctags}"]
    sketch = ROOT / "firmware/github_dashboard"
    includes = f'-DLV_CONF_INCLUDE_SIMPLE -I"{sketch}"'
    run(cli, "compile", *options, "--fqbn", FQBN, *extra,
        "--build-property", f"compiler.c.extra_flags={includes}",
        "--build-property", f"compiler.cpp.extra_flags={includes}",
        "--build-path", BUILD / "github_dashboard", "--output-dir", BUILD / "github_dashboard-bin",
        "--jobs", "6", sketch)
    print("Built firmware:", BUILD / "github_dashboard-bin")


if __name__ == "__main__":
    main()
