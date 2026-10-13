import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from bridge import main
from usage import default_codex_dir


class CliTests(unittest.TestCase):
    def test_existing_codex_home_and_empty_fallback(self):
        with patch.dict(os.environ, {"CODEX_HOME": "/tmp/custom-codex"}):
            self.assertEqual(default_codex_dir(), Path("/tmp/custom-codex"))
        with patch.dict(os.environ, {"CODEX_HOME": ""}):
            self.assertEqual(default_codex_dir(), Path.home() / ".codex")

    def test_explicit_directory_overrides_environment_without_usb(self):
        report = {"windows": {}, "warnings": []}
        output = io.StringIO()
        with patch.dict(os.environ, {"CODEX_HOME": "/tmp/environment-codex"}), \
             patch("sys.argv", ["bridge.py", "--json", "--codex-dir", "/tmp/explicit-codex"]), \
             patch("bridge.UsageReader") as reader, \
             patch("bridge.PetConnection") as connection, redirect_stdout(output):
            reader.return_value.read.return_value = report
            self.assertEqual(main(), 0)
            reader.assert_called_once_with(Path("/tmp/explicit-codex"))
            connection.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), report)

    def test_missing_logs_return_error_not_zero_statistics(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.argv", ["bridge.py", "--json"]), \
             patch("bridge.UsageReader.read", side_effect=FileNotFoundError("No session files")), \
             redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(main(), 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("No session files", errors.getvalue())

    def test_incomplete_logs_return_nonzero_status(self):
        with patch("sys.argv", ["bridge.py", "--json"]), \
             patch("bridge.UsageReader.read", return_value={"warnings": ["Unreadable file"]}), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(), 1)


if __name__ == "__main__":
    unittest.main()
