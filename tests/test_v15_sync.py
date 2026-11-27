"""Test della v1.5 (fase 5): cartella dati sincronizzata tra piu' PC."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from study_manager import paths as app_paths
from study_manager.database import Database
from study_manager.services import sync_folder as sync
from study_manager.services.pdf_tools.images import images_root


def close_db(db: Database) -> None:
    for connection in list(getattr(db, "_connections", [])):
        connection.close()


class SyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.local = self.base / "pc1"
        self.cloud = self.base / "OneDrive" / "Lectio"
        self.cloud.mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"LECTIO_DATA_DIR": str(self.local), "LECTIO_SUBJECTS_DIR": "",
                                                "LECTIO_SOUNDS_DIR": str(self.base / "sounds")})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _local_data(self) -> dict[str, Path]:
        layout = app_paths.ensure_data_layout()
        db = Database(layout["db"])
        db.set_path_roots(subjects=layout["subjects"], data=layout["shared"])
        pdf = layout["subjects"] / "Analisi" / "Slide" / "l1.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b"%PDF")
        db.add_material("L1", str(pdf), "Analisi", 1)
        (layout["pdf_images"] / "cap").mkdir(parents=True)
        (layout["pdf_images"] / "cap" / "img.png").write_bytes(b"png")
        (layout["transcripts"] / "t.txt").write_text("x", encoding="utf-8")
        close_db(db)
        return layout

    def test_layout_without_and_with_sync(self) -> None:
        layout = app_paths.ensure_data_layout()
        self.assertFalse(layout["synced"])
        self.assertEqual(layout["db"], self.local / "study_manager.db")
        app_paths.set_sync_folder(self.cloud)
        layout = app_paths.ensure_data_layout()
        self.assertFalse(layout["synced"])  # cartella senza marcatore: non ancora pronta
        self.assertEqual(layout["sync_folder"], self.cloud)
        (self.cloud / app_paths.SYNC_MARKER_NAME).write_text("{}", encoding="utf-8")
        layout = app_paths.ensure_data_layout()
        self.assertTrue(layout["synced"])
        self.assertEqual(layout["db"], self.cloud / "study_manager.db")
        self.assertEqual(layout["subjects"], self.cloud / "Materie")
        self.assertEqual(layout["models"], self.local / "models")  # resta locale
        self.assertEqual(images_root(), self.cloud / "pdf_images")

    def test_enable_copies_and_paths_still_resolve(self) -> None:
        layout = self._local_data()
        current = {k: layout[k] for k in ("db", "subjects", "transcripts", "pdf_images")}
        result = sync.enable_sync(current, self.cloud)
        self.assertGreaterEqual(result["files"], 3)
        self.assertTrue((self.cloud / "Materie" / "Analisi" / "Slide" / "l1.pdf").is_file())
        self.assertTrue((self.cloud / "pdf_images" / "cap" / "img.png").is_file())
        self.assertTrue(layout["db"].is_file())  # l'originale resta
        new = app_paths.ensure_data_layout()
        self.assertTrue(new["synced"])
        db = Database(new["db"], journal_mode="DELETE")
        try:
            db.set_path_roots(subjects=new["subjects"], data=new["shared"])
            material = db.list_materials_for_subject("Analisi")[0]
            self.assertEqual(Path(material["file_path"]), self.cloud / "Materie" / "Analisi" / "Slide" / "l1.pdf")
            mode = db._connection().execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode.lower(), "delete")
        finally:
            close_db(db)
        self.assertFalse(Path(str(new["db"]) + "-wal").exists())

    def test_second_pc_uses_existing_and_refuses_overwrite(self) -> None:
        layout = self._local_data()
        current = {k: layout[k] for k in ("db", "subjects", "transcripts", "pdf_images")}
        sync.enable_sync(current, self.cloud)
        app_paths.set_sync_folder(None)
        with self.assertRaises(sync.SyncError):
            sync.enable_sync(current, self.cloud)
        summary = sync.enable_sync(current, self.cloud, use_existing=True)
        self.assertTrue(summary["used_existing"])
        with self.assertRaises(sync.SyncError):
            sync.enable_sync(current, layout["subjects"] / "Analisi")  # dentro Materie
        with self.assertRaises(sync.SyncError):
            sync.enable_sync(current, self.base / "non-esiste")

    def test_disable_brings_data_back(self) -> None:
        layout = self._local_data()
        current = {k: layout[k] for k in ("db", "subjects", "transcripts", "pdf_images")}
        sync.enable_sync(current, self.cloud)
        synced = app_paths.ensure_data_layout()
        db = Database(synced["db"], journal_mode="DELETE")
        db.set_path_roots(subjects=synced["subjects"], data=synced["shared"])
        new_pdf = synced["subjects"] / "Analisi" / "Slide" / "l2.pdf"
        new_pdf.write_bytes(b"%PDF2")
        db.add_material("L2", str(new_pdf), "Analisi", 1)
        close_db(db)
        sync.disable_sync({k: synced[k] for k in ("db", "subjects", "transcripts", "pdf_images")}, self.local)
        back = app_paths.ensure_data_layout()
        self.assertFalse(back["synced"])
        self.assertIsNone(back["sync_folder"])
        self.assertTrue((back["subjects"] / "Analisi" / "Slide" / "l2.pdf").is_file())
        connection = sqlite3.connect(back["db"])
        try:
            rows = connection.execute("SELECT title FROM materials ORDER BY title").fetchall()
        finally:
            connection.close()
        self.assertEqual([r[0] for r in rows], ["L1", "L2"])
        self.assertTrue(list(self.local.glob("study_manager-prima-della-sync-*.db")))

    def test_lock_between_pcs(self) -> None:
        now = datetime(2026, 11, 27, 10, 0, 0)
        sync.write_lock(self.cloud, now)
        self.assertIsNone(sync.lock_conflict(self.cloud, now))  # e' di questo PC
        lock = json.loads((self.cloud / sync.LOCK_NAME).read_text(encoding="utf-8"))
        lock["pc"] = "PC-UNIVERSITA"
        (self.cloud / sync.LOCK_NAME).write_text(json.dumps(lock), encoding="utf-8")
        conflict = sync.lock_conflict(self.cloud, now + timedelta(minutes=2))
        self.assertEqual(conflict["pc"], "PC-UNIVERSITA")
        self.assertIsNone(sync.lock_conflict(self.cloud, now + timedelta(minutes=30)))  # segnale vecchio
        sync.release_lock(self.cloud)  # non e' nostro: resta
        self.assertTrue((self.cloud / sync.LOCK_NAME).exists())
        sync.write_lock(self.cloud, now)
        sync.release_lock(self.cloud)
        self.assertFalse((self.cloud / sync.LOCK_NAME).exists())

    def test_conflict_copies_and_snapshots(self) -> None:
        (self.cloud / "study_manager.db").write_bytes(b"")
        (self.cloud / "study_manager-PC2.db").write_bytes(b"")
        self.assertEqual([p.name for p in sync.conflict_copies(self.cloud)], ["study_manager-PC2.db"])
        db = Database(self.base / "snap.sqlite")
        try:
            snapshot_paths = {"data": self.base, "transcripts": self.base / "tr", "snapshots": self.cloud / "Snapshot"}
            snapshot_paths["transcripts"].mkdir()
            for index in range(7):
                path = sync.write_snapshot(db, snapshot_paths, keep=3)
                os.utime(path, (1_000_000 + index, 1_000_000 + index))
                path.rename(path.with_name(f"lectio-{index:02d}.zip"))
            remaining = sorted(p.name for p in (self.cloud / "Snapshot").glob("lectio-*.zip"))
            self.assertLessEqual(len(remaining), 4)
        finally:
            close_db(db)


if __name__ == "__main__":
    unittest.main()
