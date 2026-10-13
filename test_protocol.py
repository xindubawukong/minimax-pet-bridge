import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bridge import PetConnection, encode_report, matching_ack


class ProtocolTests(unittest.TestCase):
    def test_encoding_and_ack_must_match_all_fields(self):
        report = {"windows": {key: {"tokens": i} for i, key in enumerate(("hour", "day", "week", "month"), 1)}}
        data, expected = encode_report(report, 1790000000)
        self.assertEqual(data, b"SYNC9 1790000000 1 2 3 4\n")
        self.assertTrue(matching_ack("SOSP9 ACK 1790000000 1 2 3 4 state=2 date=10月13日", expected))
        self.assertFalse(matching_ack("SOSP9 ACK 1790000000 1 2 3 5 state=2 date=10月13日", expected))
        self.assertFalse(matching_ack("ACK:20", expected))

    def test_invalid_values_are_not_sent(self):
        report = {"windows": {key: {"tokens": 0} for key in ("hour", "day", "week", "month")}}
        report["windows"]["hour"]["tokens"] = -1
        with self.assertRaises(ValueError):
            encode_report(report, 1790000000)

    def test_delayed_serial_fragments_preserve_ack_and_utf8(self):
        line = "SOSP9 ACK 1790000000 1 2 3 4 state=2 date=10月13日"
        raw = line.encode("utf-8")
        split = raw.index("月".encode("utf-8")) + 1
        connection = object.__new__(PetConnection)
        connection.pending = bytearray()
        connection.serial = SimpleNamespace(in_waiting=0, read=Mock(side_effect=[
            raw[:split], b"", raw[split:] + b"\r\nSOSP9 ERROR busy\n",
        ]))
        self.assertEqual(connection.readline(), "")
        self.assertEqual(connection.readline(), line)
        self.assertTrue(matching_ack(line, (1790000000, 1, 2, 3, 4)))
        self.assertEqual(connection.readline(), "SOSP9 ERROR busy")


if __name__ == "__main__":
    unittest.main()
