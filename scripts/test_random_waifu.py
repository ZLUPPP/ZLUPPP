"""Offline regression checks for API outages: python -m unittest discover -s scripts."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import random_waifu


class OutageTests(unittest.TestCase):
    def test_outages_preserve_existing_assets(self):
        for failing_api in ("fetch_quote", "select_portrait"):
            with self.subTest(api=failing_api), tempfile.TemporaryDirectory() as directory:
                assets = Path(directory)
                original = {
                    "random-waifu.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/jpeg;base64,/9j/"/></svg>',
                    "random-waifu.jpg": b"existing portrait",
                    "random-waifu.json": b'{}',
                }
                for name, data in original.items():
                    (assets / name).write_bytes(data)
                with patch("sys.argv", ["random_waifu.py", "--assets-dir", directory]), \
                        patch.object(random_waifu, "fetch_quote", return_value={}), \
                        patch.object(random_waifu, failing_api, side_effect=RuntimeError("DNS unavailable")):
                    self.assertEqual(random_waifu.main(), 0)
                self.assertEqual({p.name: p.read_bytes() for p in assets.iterdir()}, original)

    def test_outage_without_usable_card_still_fails(self):
        for svg in (None, "invalid XML", '<svg xmlns="http://www.w3.org/2000/svg"/>'):
            with self.subTest(svg=svg), tempfile.TemporaryDirectory() as directory:
                if svg is not None:
                    (Path(directory) / "random-waifu.svg").write_text(svg)
                with patch("sys.argv", ["random_waifu.py", "--assets-dir", directory]), \
                        patch.object(random_waifu, "fetch_quote", side_effect=RuntimeError("DNS unavailable")):
                    with self.assertRaisesRegex(RuntimeError, "DNS unavailable"):
                        random_waifu.main()


if __name__ == "__main__":
    unittest.main()
