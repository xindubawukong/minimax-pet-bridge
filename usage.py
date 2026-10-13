"""Aggregate local Codex token records without exporting conversation content."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

WINDOWS = {"hour": 3600, "day": 86400, "week": 7 * 86400, "month": 30 * 86400}


def default_codex_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("A usage timestamp must include its timezone")
    return parsed.timestamp()


def integer(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


@dataclass(frozen=True)
class UsageEvent:
    key: str
    time: float
    tokens: int


def token_total(usage: dict) -> int:
    if type(usage.get("total_tokens")) is int:
        return integer(usage["total_tokens"])
    # Cached input is a subset of input; reasoning output is a subset of output.
    return integer(usage.get("input_tokens")) + integer(usage.get("output_tokens"))


def read_events(path: Path) -> list[UsageEvent]:
    modern: list[UsageEvent] = []
    legacy: list[UsageEvent] = []
    previous_total = 0
    with path.open("rb") as source:
        for line in source:
            # The JSONL files also contain chat content. Only candidate usage
            # lines are parsed, and only timestamps, IDs and totals are retained.
            if b'"token_usage_record"' not in line and b'"token_count"' not in line:
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                when = timestamp(record["timestamp"])
                if record.get("type") == "token_usage_record":
                    usage = payload.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    tokens = token_total(usage)
                    response = payload.get("response_id")
                    key = ("response:" + str(response)) if response else (
                        "record:" + json.dumps([
                            payload.get("session_id"), payload.get("thread_id"),
                            record.get("ordinal"), record["timestamp"], tokens,
                        ], separators=(",", ":"))
                    )
                    modern.append(UsageEvent(key, when, tokens))
                elif record.get("type") == "event_msg" and payload.get("type") == "token_count":
                    info = payload.get("info")
                    total = info.get("total_token_usage") if isinstance(info, dict) else None
                    if not isinstance(total, dict):
                        continue
                    cumulative = token_total(total)
                    delta = cumulative - previous_total if cumulative >= previous_total else cumulative
                    previous_total = cumulative
                    key = "legacy:" + json.dumps([record["timestamp"], total], sort_keys=True)
                    if delta:
                        legacy.append(UsageEvent(key, when, delta))
            except (ValueError, KeyError, TypeError, OverflowError):
                # A live file can end with a partial JSON line. Retry on refresh.
                continue
    # Current Codex writes both formats for the same work. Its per-response
    # usage records are authoritative; combining the two would double count.
    return modern if modern else legacy


def aggregate(events: list[UsageEvent], now: float) -> dict[str, int]:
    unique: dict[str, UsageEvent] = {}
    for event in events:
        previous = unique.get(event.key)
        if previous is None or event.tokens > previous.tokens:
            unique[event.key] = event
    totals = {name: 0 for name in WINDOWS}
    for event in unique.values():
        age = now - event.time
        for name, seconds in WINDOWS.items():
            if 0 <= age < seconds:
                totals[name] += event.tokens
    return totals


class UsageReader:
    def __init__(self, codex_dir: Path):
        self.codex_dir = codex_dir.expanduser()
        self.cache: dict[Path, tuple[tuple[int, int], list[UsageEvent]]] = {}

    def read(self, now: float | None = None) -> dict:
        if now is None:
            now = datetime.now(timezone.utc).timestamp()
        if not self.codex_dir.is_dir():
            raise FileNotFoundError(f"Codex data directory does not exist: {self.codex_dir}")
        paths = set()
        for folder in ("sessions", "archived_sessions"):
            paths.update((self.codex_dir / folder).rglob("*.jsonl"))
        if not paths:
            raise FileNotFoundError(f"No session JSONL files found under {self.codex_dir}")
        events: list[UsageEvent] = []
        warnings = []
        for path in sorted(paths):
            try:
                stat = path.stat()
                signature = (stat.st_size, stat.st_mtime_ns)
                cached = self.cache.get(path)
                if cached is None or cached[0] != signature:
                    self.cache[path] = (signature, read_events(path))
                events.extend(self.cache[path][1])
            except OSError as error:
                warnings.append(f"Unable to read {path.name}: {error.strerror}")
        self.cache = {p: v for p, v in self.cache.items() if p in paths}
        if not events:
            raise ValueError("No supported usage records found; refusing to report a false zero.")
        return {
            "updated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "windows": {name: {"tokens": total} for name, total in aggregate(events, now).items()},
            "files": len(paths),
            "warnings": warnings,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-dir", type=Path, default=default_codex_dir())
    args = parser.parse_args()
    print(json.dumps(UsageReader(args.codex_dir).read(), ensure_ascii=False, indent=2))
