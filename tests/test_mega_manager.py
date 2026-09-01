import unittest
import tempfile
import os
from pathlib import Path

from src.db import Database
from src.mega_manager import MegaManager
from src.video_utils import VideoInfo

class TestMegaManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.downloads_dir = Path(self.temp_dir.name)

        # Create dummy video files
        (self.downloads_dir / "V1.mp4").write_text("dummy")
        (self.downloads_dir / "V2.mp4").write_text("dummy")
        (self.downloads_dir / "V10.mp4").write_text("dummy")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_mega_scan_and_sorting(self):
        mm = MegaManager("https://mega.nz/folder/test#key", str(self.downloads_dir))
        items = mm.scan_sequence_items()

        self.assertEqual(list(items.keys()), [1, 2, 10])
        self.assertEqual(items[1].sequence_key, "V1")
        self.assertEqual(items[2].sequence_key, "V2")
        self.assertEqual(items[10].sequence_key, "V10")

        # Test sequence progression
        uploaded_set = set()
        next_item = mm.get_next_unposted_item(uploaded_set)
        self.assertEqual(next_item.sequence_key, "V1")

        uploaded_set.add("V1")
        next_item = mm.get_next_unposted_item(uploaded_set)
        self.assertEqual(next_item.sequence_key, "V2")

        uploaded_set.add("V2")
        next_item = mm.get_next_unposted_item(uploaded_set)
        self.assertEqual(next_item.sequence_key, "V10")

    def test_auto_metadata_generation(self):
        mm = MegaManager("https://mega.nz/folder/test#key", str(self.downloads_dir))
        meta_long = mm.generate_auto_metadata("V1", 1, is_short=False)
        self.assertIn("Episode 1", meta_long["title"])
        self.assertIn("scifi", meta_long["tags"])

        meta_short = mm.generate_auto_metadata("V2", 2, is_short=True)
        self.assertIn("Part 2", meta_short["title"])
        self.assertTrue(meta_short["isShort"])


if __name__ == "__main__":
    unittest.main()
