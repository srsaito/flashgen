import os
import tempfile
import unittest
from unittest.mock import patch

import flashgen


class ReadSecretTests(unittest.TestCase):
    def test_prefers_file_over_env(self):
        with tempfile.NamedTemporaryFile("w", suffix="_secret", delete=False) as fh:
            fh.write("from-file\n")  # trailing newline is stripped
            path = fh.name
        try:
            with patch.dict(
                os.environ,
                {"DEMO_KEY": "from-env", "DEMO_KEY_FILE": path},
                clear=False,
            ):
                self.assertEqual(flashgen.read_secret("DEMO_KEY"), "from-file")
        finally:
            os.unlink(path)

    def test_falls_back_to_env_when_no_file_set(self):
        with patch.dict(os.environ, {"DEMO_KEY": "from-env"}, clear=False):
            os.environ.pop("DEMO_KEY_FILE", None)
            self.assertEqual(flashgen.read_secret("DEMO_KEY"), "from-env")

    def test_falls_back_to_env_when_file_unreadable(self):
        with patch.dict(
            os.environ,
            {"DEMO_KEY": "from-env", "DEMO_KEY_FILE": "/nonexistent/secret"},
            clear=False,
        ):
            self.assertEqual(flashgen.read_secret("DEMO_KEY"), "from-env")

    def test_missing_everywhere_returns_empty_string(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DEMO_KEY", None)
            os.environ.pop("DEMO_KEY_FILE", None)
            self.assertEqual(flashgen.read_secret("DEMO_KEY"), "")


if __name__ == "__main__":
    unittest.main()
