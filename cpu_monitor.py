#!/usr/bin/env python3
"""Sample Linux CPU every 5s; show overlapping 20s means for the last 5 minutes."""
from __future__ import annotations

import argparse
import json
import math
import selectors
import shlex
import subprocess
import threading
import time
from pathlib import Path

SAMPLE_SECONDS = 5
BUCKET_SECONDS = SAMPLE_SECONDS
WINDOW_SECONDS = 20
WINDOW_SAMPLES = WINDOW_SECONDS // SAMPLE_SECONDS
POINTS = 60
HISTORY_BUCKETS = POINTS + WINDOW_SAMPLES - 1

# Run in memory on the remote machine, with no packages or files to install.
# guest/guest_nice are already included in user/nice: sum only the first 8 fields.
REMOTE_SAMPLER = r'''
import json, os, time
boot = open('/proc/sys/kernel/random/boot_id').read().strip()
deadline = time.monotonic()
while True:
    with open('/proc/stat') as stream:
        fields = [int(x) for x in stream.readline().split()[1:9]]
    print(json.dumps(dict(total=sum(fields), idle=fields[3]+fields[4],
        monotonic=time.monotonic(), cpus=os.cpu_count(), boot=boot)), flush=True)
    deadline += 5
    time.sleep(max(0, deadline-time.monotonic()))
'''


def cpu_percent(previous, current):
    """Busy time, excluding idle and iowait; reject resets and missed intervals."""
    elapsed = current["monotonic"] - previous["monotonic"]
    total = current["total"] - previous["total"]
    idle = current["idle"] - previous["idle"]
    if (current["boot"] != previous["boot"] or current["cpus"] != previous["cpus"]
            or not 2.5 <= elapsed <= 7.5 or total <= 0 or not 0 <= idle <= total):
        return None
    return 100 * (total - idle) / total


class CpuHistory:
    """5s intervals; a 20s moving average every 5s. Missing intervals stay null."""
    def __init__(self, host, path=None):
        self.host, self.path = host, path
        self.buckets = {}
        self.previous = None
        self.anchor = None
        self.last_sample = 0
        self.last_covered = 0
        self.cpus = 0
        self.connected = False
        if path and path.exists():
            try:
                saved = json.loads(path.read_text())
                # v1 stored disjoint 20s means and cannot reconstruct overlap.
                if saved["host"] == host and saved["version"] == 2:
                    cutoff = int(time.time() // BUCKET_SECONDS) - HISTORY_BUCKETS
                    for key, entry in saved["buckets"].items():
                        key = int(key)
                        if (cutoff <= key < cutoff + HISTORY_BUCKETS and len(entry) == 2
                                and all(math.isfinite(x) for x in entry)
                                and 0 <= entry[1] <= BUCKET_SECONDS + .5 and 0 <= entry[0] <= 100 * entry[1]):
                            self.buckets[key] = entry
                    self.cpus = int(saved.get("cpus", 0))
            except (OSError, ValueError, KeyError, TypeError):
                pass

    def disconnect(self):
        self.previous = self.anchor = None
        self.connected = False

    def add(self, sample, received_at=None):
        now = time.time() if received_at is None else received_at
        for key in ("total", "idle", "monotonic", "cpus"):
            if not isinstance(sample[key], (int, float)) or not math.isfinite(sample[key]) or sample[key] < 0:
                raise ValueError("Invalid CPU counter")
        if not isinstance(sample["boot"], str) or not sample["boot"] or sample["cpus"] < 1:
            raise ValueError("Invalid CPU identity")
        if self.cpus and sample["cpus"] != self.cpus:
            # Old normalized percentages used a different number of CPUs.
            self.buckets.clear()
        if self.anchor is None:
            self.anchor = (now, sample["monotonic"])
        end = self.anchor[0] + sample["monotonic"] - self.anchor[1]
        if self.previous is not None:
            value = cpu_percent(self.previous, sample)
            start = end - (sample["monotonic"] - self.previous["monotonic"])
            if value is not None:
                while start < end - 1e-6:
                    bucket = int(start // BUCKET_SECONDS)
                    right = min(end, (bucket + 1) * BUCKET_SECONDS)
                    weighted, coverage = self.buckets.get(bucket, [0, 0])
                    self.buckets[bucket] = [weighted + value * (right - start), coverage + right - start]
                    start = right
            else:
                # A reboot or counter reset starts a fresh timing baseline.
                self.anchor = (now, sample["monotonic"])
        self.last_covered = self.anchor[0] + sample["monotonic"] - self.anchor[1]
        self.previous = sample
        self.last_sample, self.cpus, self.connected = now, int(sample["cpus"]), True
        cutoff = int(now // BUCKET_SECONDS) - HISTORY_BUCKETS
        self.buckets = {key: value for key, value in self.buckets.items() if cutoff <= key <= cutoff + HISTORY_BUCKETS}

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        online = self.connected and now - self.last_sample < 15
        # Wait for the sample covering the boundary before publishing a point.
        through = min(now, self.last_covered) if online else now
        end = int(through // BUCKET_SECONDS) * BUCKET_SECONDS
        values = []
        for right in range(end // BUCKET_SECONDS - POINTS + 1, end // BUCKET_SECONDS + 1):
            intervals = [self.buckets.get(key, [0, 0]) for key in range(right - WINDOW_SAMPLES, right)]
            weighted = sum(value[0] for value in intervals)
            coverage = sum(value[1] for value in intervals)
            # top-style scale: one fully busy logical CPU is 100%, not 100/N%.
            values.append(round(weighted / coverage * self.cpus * 10) if 19.5 <= coverage <= 20.5 else None)
        return dict(host=self.host, generated_at=int(now), end=end, step=SAMPLE_SECONDS,
                    window=WINDOW_SECONDS, values=values,
                    cpus=self.cpus, online=online,
                    last_sample=int(self.last_sample))

    def save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic replacement protects saved history from interruption.
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(dict(version=2, host=self.host, cpus=self.cpus, buckets=self.buckets)))
            temporary.replace(self.path)


class CpuMonitor:
    def __init__(self, host="dynamic", path=None):
        if not host or host.startswith("-") or len(host) > 100 or any(c.isspace() for c in host):
            raise ValueError("Invalid SSH host")
        self.history = CpuHistory(host, path)
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.process = None
        self.thread = threading.Thread(target=self._run, name="cpu-ssh", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def snapshot(self):
        with self.lock:
            return self.history.snapshot()

    def stop(self):
        self.stopped.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
        self.thread.join(timeout=5)

    def _run(self):
        while not self.stopped.is_set():
            process = None
            try:
                command = "python3 -u -c " + shlex.quote(REMOTE_SAMPLER)
                with self.lock:
                    if self.stopped.is_set():
                        return
                    process = subprocess.Popen(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                        "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2", self.history.host, command],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    self.process = process
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    received = time.monotonic()
                    while not self.stopped.is_set() and time.monotonic() - received < 16:
                        if not selector.select(timeout=1):
                            continue
                        line = process.stdout.readline(2048)
                        if not line:
                            break
                        sample = json.loads(line)
                        with self.lock:
                            self.history.add(sample)
                            self.history.save()
                        received = time.monotonic()
            except (OSError, ValueError, KeyError, TypeError):
                pass
            finally:
                if process:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    process.stdout.close()
                with self.lock:
                    self.history.disconnect()
                    self.process = None
            self.stopped.wait(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="dynamic")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "private/cpu-history.json")
    args = parser.parse_args()
    monitor = CpuMonitor(args.host, args.output).start()
    try:
        while True:
            print(json.dumps(monitor.snapshot()), flush=True)
            time.sleep(5)
    except KeyboardInterrupt:
        return 0
    finally:
        monitor.stop()


if __name__ == "__main__":
    raise SystemExit(main())
