import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.sync.realtime_listener import RealtimeListener
from app.wechat_reader.wx4 import crypto


class RealtimeListenerSnapshotTests(unittest.TestCase):
    def test_failed_query_closes_and_removes_snapshot(self):
        fd, snapshot_path = tempfile.mkstemp(suffix=".session.tmp")
        os.close(fd)
        Path(snapshot_path).write_bytes(b"not a sqlite database")
        listener = RealtimeListener()

        with patch(
            "app.sync.realtime_listener.decrypt_db_to_tempfile",
            return_value=snapshot_path,
        ):
            with self.assertRaises(sqlite3.DatabaseError):
                listener._snapshot_sessions("unused.db", b"unused")

        self.assertFalse(os.path.exists(snapshot_path))

    def test_tempfile_uses_configured_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(crypto, "decrypt_db_to_memory", return_value=b"payload"):
                snapshot_path = crypto.decrypt_db_to_tempfile(
                    "unused.db",
                    b"unused",
                    temp_dir=temp_dir,
                )
            try:
                self.assertEqual(Path(snapshot_path).parent, Path(temp_dir))
                self.assertEqual(Path(snapshot_path).read_bytes(), b"payload")
            finally:
                Path(snapshot_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
