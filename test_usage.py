import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from usage import UsageEvent, aggregate, read_events, token_total


def parse(records):
    body = b"".join(json.dumps(r).encode() + b"\n" for r in records) + b'{"partial":'
    with patch.object(Path, "open", return_value=io.BytesIO(body)):
        return read_events(Path("fixture.jsonl"))


def record(kind, payload, time="2026-10-13T12:00:00Z"):
    return {"timestamp": time, "type": kind, "payload": payload}


class UsageTests(unittest.TestCase):
    def test_nested_windows_exclude_future_and_boundary(self):
        now = 3000000
        events = [UsageEvent(str(i), now - age, amount) for i, (age, amount) in enumerate([
            (1, 1), (3600, 2), (86400, 4), (7 * 86400, 8), (30 * 86400, 16), (-1, 32)
        ])]
        self.assertEqual(aggregate(events, now), {"hour": 1, "day": 3, "week": 7, "month": 15})

    def test_response_ids_deduplicate_copies_and_final_usage(self):
        events = [UsageEvent("r1", 10, 12), UsageEvent("r1", 10, 12), UsageEvent("r1", 10, 15)]
        self.assertEqual(aggregate(events, 20)["hour"], 15)

    def test_modern_records_do_not_add_cumulative_counters(self):
        events = parse([
            record("event_msg", {"type": "token_count", "info": {"total_token_usage": {"total_tokens": 400}}}),
            record("token_usage_record", {"response_id": "r1", "usage": {"total_tokens": 100}, "thread_token_usage": {"total_tokens": 400}}),
            record("token_usage_record", {"response_id": "r2", "usage": {"total_tokens": 200}, "turn_token_usage": {"total_tokens": 600}}),
            record("response_item", {"text": "token_usage_record", "usage": {"total_tokens": 999}}),
        ])
        self.assertEqual([e.tokens for e in events], [100, 200])

    def test_legacy_cumulative_deltas_repeats_and_reset(self):
        events = parse([
            record("event_msg", {"type": "token_count", "info": {"total_token_usage": {"total_tokens": total}}}, f"2026-10-13T12:00:0{i}Z")
            for i, total in enumerate([100, 100, 250, 20])
        ])
        self.assertEqual([e.tokens for e in events], [100, 150, 20])

    def test_cached_and_reasoning_tokens_are_not_added_twice(self):
        self.assertEqual(token_total({"input_tokens": 100, "cached_input_tokens": 80,
                                     "output_tokens": 20, "reasoning_output_tokens": 10}), 120)


if __name__ == "__main__":
    unittest.main()
