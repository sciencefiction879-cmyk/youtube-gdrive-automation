import unittest
import tempfile
import os
from pathlib import Path

from src.db import Database
from src.drive_manager import DriveManager, DriveSequenceItem
from src.video_utils import VideoInfo

class TestSequenceAndMatching(unittest.TestCase):
    def test_extract_sequence_number(self):
        # Create a dummy DriveManager instance without network calls
        dm = object.__new__(DriveManager)

        test_cases = [
            ("V1.mp4", 1),
            ("v1.mp4", 1),
            ("V2.json", 2),
            ("v03.png", 3),
            ("V10.mp4", 10),
            ("V100_thumb.jpg", 100),
            ("Video4.mov", 4),
            ("video_5.mkv", 5),
            ("v_6.json", 6),
            ("random_file.txt", None),
            ("thumbnail.png", None)
        ]

        for filename, expected in test_cases:
            with self.subTest(filename=filename):
                res = dm._extract_sequence_number(filename)
                self.assertEqual(res, expected, f"Failed for {filename}")

    def test_natural_sorting_and_matching(self):
        # Simulate drive files returned
        mock_files = [
            {"id": "id_v10_vid", "name": "V10.mp4", "mimeType": "video/mp4"},
            {"id": "id_v1_vid", "name": "V1.mp4", "mimeType": "video/mp4"},
            {"id": "id_v1_json", "name": "V1.json", "mimeType": "application/json"},
            {"id": "id_v1_thumb", "name": "V1.jpg", "mimeType": "image/jpeg"},
            {"id": "id_v2_vid", "name": "V2.mp4", "mimeType": "video/mp4"},
            {"id": "id_v2_json", "name": "V2.json", "mimeType": "application/json"},
            {"id": "id_v10_json", "name": "V10.json", "mimeType": "application/json"},
        ]

        dm = object.__new__(DriveManager)
        dm.folder_id = "test_folder"
        dm._list_all_files_in_folder_recursive = lambda folder: mock_files

        items = dm.scan_sequence_items()

        # Keys should be sorted numerically: 1, 2, 10
        self.assertEqual(list(items.keys()), [1, 2, 10])
        
        # Check V1 bundle
        self.assertTrue(items[1].has_video)
        self.assertTrue(items[1].has_metadata)
        self.assertTrue(items[1].has_thumbnail)
        self.assertEqual(items[1].sequence_key, "V1")

        # Check V2 bundle
        self.assertTrue(items[2].has_video)
        self.assertTrue(items[2].has_metadata)
        self.assertFalse(items[2].has_thumbnail)  # No thumbnail

        # Check V10 bundle
        self.assertTrue(items[10].has_video)
        self.assertTrue(items[10].has_metadata)

        # Test sequence progression:
        uploaded_set = set()
        next_item = dm.get_next_unposted_item(uploaded_set)
        self.assertEqual(next_item.sequence_key, "V1")

        uploaded_set.add("V1")
        next_item = dm.get_next_unposted_item(uploaded_set)
        self.assertEqual(next_item.sequence_key, "V2")

        uploaded_set.add("V2")
        next_item = dm.get_next_unposted_item(uploaded_set)
        self.assertEqual(next_item.sequence_key, "V10")


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_state.db")
        self.db = Database(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_database_lifecycle(self):
        self.assertEqual(len(self.db.get_uploaded_keys()), 0)
        self.assertFalse(self.db.is_uploaded("V1"))

        # Record V1
        self.db.record_upload(
            sequence_key="V1",
            sequence_num=1,
            youtube_id="abc12345",
            youtube_url="https://youtu.be/abc12345",
            title="Video 1 Test",
            video_type="longform",
            has_thumbnail=True
        )

        self.assertTrue(self.db.is_uploaded("V1"))
        self.assertIn("V1", self.db.get_uploaded_keys())
        self.assertEqual(self.db.get_latest_uploaded_number(), 1)

        # Record V2
        self.db.record_upload(
            sequence_key="V2",
            sequence_num=2,
            youtube_id="xyz67890",
            youtube_url="https://youtu.be/xyz67890",
            title="Video 2 Short",
            video_type="short",
            has_thumbnail=False
        )

        self.assertEqual(self.db.get_latest_uploaded_number(), 2)
        self.assertEqual(self.db.get_uploaded_keys(), {"V1", "V2"})

    def test_slot_run_guard(self):
        today = "2026-09-01"
        self.assertFalse(self.db.slot_already_ran_today(today, "slot1"))

        self.db.record_run(today, "slot1", "V1", "success", "Uploaded OK")
        self.assertTrue(self.db.slot_already_ran_today(today, "slot1"))
        self.assertFalse(self.db.slot_already_ran_today(today, "slot2"))


class TestVideoClassification(unittest.TestCase):
    def test_short_vs_longform(self):
        # Vertical Short: 1080x1920, 60s -> Short
        v_short = VideoInfo(duration=60.0, width=1080, height=1920, has_audio=True)
        self.assertTrue(v_short.is_vertical)
        self.assertTrue(v_short.is_short(max_shorts_duration=180))

        # Horizontal Video: 1920x1080, 60s -> Longform
        v_horizontal = VideoInfo(duration=60.0, width=1920, height=1080, has_audio=True)
        self.assertFalse(v_horizontal.is_vertical)
        self.assertFalse(v_horizontal.is_short(max_shorts_duration=180))

        # Vertical but long: 1080x1920, 300s -> Longform
        v_long_vertical = VideoInfo(duration=300.0, width=1080, height=1920, has_audio=True)
        self.assertTrue(v_long_vertical.is_vertical)
        self.assertFalse(v_long_vertical.is_short(max_shorts_duration=180))


if __name__ == "__main__":
    unittest.main()
