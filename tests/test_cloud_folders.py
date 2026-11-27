"""Test del rilevamento automatico delle cartelle cloud (OneDrive, Google Drive, Dropbox)."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from study_manager import paths as app_paths
from study_manager.services import cloud_folders as cloud
from study_manager.services import sync_folder as sync


class CloudDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        # Nessun servizio "vero" del PC che esegue i test.
        self.patches = [
            mock.patch.object(cloud, "_onedrive_accounts", return_value=[]),
            mock.patch.object(cloud, "_google_drive_volumes", return_value=[]),
            mock.patch.object(cloud, "_registry_key_exists", return_value=False),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def _env(self, **extra: str) -> dict[str, str]:
        return {"APPDATA": str(self.base / "Roaming"), "LOCALAPPDATA": str(self.base / "Local"), **extra}

    def test_nothing_found(self) -> None:
        detection = cloud.detect_cloud_roots(self._env())
        self.assertEqual(detection.roots, [])
        self.assertEqual(detection.hints, [])

    def test_onedrive_accounts(self) -> None:
        personal = self.base / "OneDrive"
        work = self.base / "OneDrive - Uni"
        personal.mkdir()
        work.mkdir()
        accounts = [
            {"key": "Personal", "folder": str(personal), "display": "", "email": ""},
            {"key": "Business1", "folder": str(work), "display": "Universita'", "email": "a@uni.it"},
            {"key": "FileCoAuth", "folder": "", "display": "", "email": ""},
        ]
        with mock.patch.object(cloud, "_onedrive_accounts", return_value=accounts):
            detection = cloud.detect_cloud_roots(self._env())
        self.assertEqual([(r.service, r.label, r.path) for r in detection.roots], [
            (cloud.ONEDRIVE, "OneDrive personale", personal),
            (cloud.ONEDRIVE, "OneDrive - Universita'", work),
        ])
        self.assertFalse(detection.roots[0].streaming)

    def test_onedrive_installed_without_account_gives_hint(self) -> None:
        folder = self.base / "OneDrive"
        folder.mkdir()
        accounts = [{"key": "Personal", "folder": "", "display": "", "email": ""}]
        with mock.patch.object(cloud, "_onedrive_accounts", return_value=accounts):
            # chiavi come le restituisce os.environ su Windows o come le scriverebbe un utente
            for key in ("ONEDRIVE", "OneDrive"):
                detection = cloud.detect_cloud_roots(self._env(**{key: str(folder)}))
                self.assertEqual(detection.roots, [])
                self.assertEqual(len(detection.hints), 1)
                self.assertIn("OneDrive", detection.hints[0])

    def test_dropbox_info_json(self) -> None:
        personal = self.base / "Dropbox"
        business = self.base / "Dropbox (Studio)"
        personal.mkdir()
        business.mkdir()
        info = self.base / "Local" / "Dropbox" / "info.json"
        info.parent.mkdir(parents=True)
        info.write_text(json.dumps({"personal": {"path": str(personal)}, "business": {"path": str(business)}}),
                        encoding="utf-8")
        # stesso file anche in APPDATA: non va contato due volte
        copy = self.base / "Roaming" / "Dropbox" / "info.json"
        copy.parent.mkdir(parents=True)
        copy.write_text(info.read_text(encoding="utf-8"), encoding="utf-8")
        detection = cloud.detect_cloud_roots(self._env())
        self.assertEqual([(r.label, r.path) for r in detection.roots],
                         [("Dropbox", personal), ("Dropbox lavoro", business)])

    def test_google_drive_streaming_volume(self) -> None:
        volume = self.base / "G"
        (volume / "Il mio Drive").mkdir(parents=True)
        (volume / "Drive condivisi").mkdir()
        (volume / ".shortcut-targets-by-id").mkdir()
        with mock.patch.object(cloud, "_google_drive_volumes", return_value=[volume]):
            detection = cloud.detect_cloud_roots(self._env())
        self.assertEqual(len(detection.roots), 1)
        root = detection.roots[0]
        self.assertEqual((root.service, root.path, root.streaming), (cloud.GOOGLE_DRIVE, volume / "Il mio Drive", True))

    def test_find_my_drive_other_language_and_accounts(self) -> None:
        unknown = self.base / "H"
        (unknown / "Mitt Drive").mkdir(parents=True)
        (unknown / "Shared drives").mkdir()
        self.assertEqual(cloud.find_my_drive(unknown), unknown / "Mitt Drive")
        nested = self.base / "I"
        (nested / "mario@example.com" / "My Drive").mkdir(parents=True)
        self.assertEqual(cloud.find_my_drive(nested), nested / "mario@example.com" / "My Drive")
        ambiguous = self.base / "J"
        (ambiguous / "Uno").mkdir(parents=True)
        (ambiguous / "Due").mkdir()
        self.assertIsNone(cloud.find_my_drive(ambiguous))

    def test_google_drive_mirror_from_preferences(self) -> None:
        mirror = self.base / "Il mio Drive"
        mirror.mkdir()
        drivefs = self.base / "Local" / "Google" / "DriveFS"
        drivefs.mkdir(parents=True)
        connection = sqlite3.connect(drivefs / "root_preference_sqlite.db")
        connection.execute("CREATE TABLE roots (root_id INTEGER PRIMARY KEY, title TEXT, is_my_drive BOOL,"
                           " last_seen_absolute_path TEXT)")
        connection.execute("INSERT INTO roots VALUES (1, 'Documents', 0, ?)", (str(self.base),))
        connection.execute("INSERT INTO roots VALUES (2, 'Il mio Drive', 1, ?)", (str(mirror),))
        connection.commit()
        connection.close()
        detection = cloud.detect_cloud_roots(self._env())
        self.assertEqual([(r.label, r.path, r.streaming) for r in detection.roots],
                         [("Google Drive (mirroring)", mirror, False)])

    def test_google_drive_installed_not_running_gives_hint(self) -> None:
        with mock.patch.object(cloud, "_registry_key_exists", return_value=True):
            detection = cloud.detect_cloud_roots(self._env())
        self.assertEqual(detection.roots, [])
        self.assertTrue(any("Google Drive" in hint for hint in detection.hints))

    def test_failing_service_does_not_block_others(self) -> None:
        volume = self.base / "G"
        (volume / "My Drive").mkdir(parents=True)
        with mock.patch.object(cloud, "_onedrive_accounts", side_effect=RuntimeError("registro")), \
                mock.patch.object(cloud, "_google_drive_volumes", return_value=[volume]), \
                self.assertLogs(cloud.LOGGER, "WARNING"):
            detection = cloud.detect_cloud_roots(self._env())
        self.assertEqual([r.path for r in detection.roots], [volume / "My Drive"])


class PrepareTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.root = cloud.CloudRoot(cloud.GOOGLE_DRIVE, "Google Drive (G:)", self.base / "Il mio Drive", True)
        self.root.path.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_new_folder(self) -> None:
        target = cloud.prepare_target(self.root)
        self.assertEqual((target.folder, target.state), (self.root.path / "Lectio", "new"))
        self.assertFalse(target.folder.exists())  # prepare non crea nulla
        self.assertEqual(cloud.create_target(target), target.folder)
        self.assertTrue(target.folder.is_dir())
        # cartella vuota (es. tentativo precedente interrotto): si riusa
        self.assertEqual(cloud.prepare_target(self.root).state, "new")

    def test_existing_and_incomplete(self) -> None:
        folder = self.root.path / "Lectio dati"
        folder.mkdir()
        (folder / app_paths.SYNC_MARKER_NAME).write_text(json.dumps({"created_by": "PC-CASA"}), encoding="utf-8")
        target = cloud.prepare_target(self.root)
        self.assertEqual((target.folder, target.state, target.created_by), (folder, "incomplete", "PC-CASA"))
        (folder / app_paths.DB_FILENAME).write_bytes(b"")
        self.assertEqual(cloud.prepare_target(self.root).state, "existing")

    def test_user_folder_named_lectio_is_not_used(self) -> None:
        mine = self.root.path / "Lectio"
        mine.mkdir()
        (mine / "tesi.docx").write_bytes(b"x")
        target = cloud.prepare_target(self.root)
        self.assertEqual((target.folder.name, target.state), ("Lectio dati", "new"))
        for name in ("Lectio dati", "Lectio dati 2", "Lectio dati 3"):
            (self.root.path / name).mkdir()
            (self.root.path / name / "x.txt").write_bytes(b"x")
        with self.assertRaises(OSError):
            cloud.prepare_target(self.root)

    def test_keep_offline_only_where_supported(self) -> None:
        target = cloud.prepare_target(self.root)
        cloud.create_target(target)
        with mock.patch.object(cloud.subprocess, "run") as run:
            self.assertFalse(cloud.keep_offline(target))  # Google Drive: va fatto dall'utente
            run.assert_not_called()
            onedrive = cloud.Target(cloud.CloudRoot(cloud.ONEDRIVE, "OneDrive", self.root.path), target.folder, "new")
            run.return_value = mock.Mock(returncode=0, stdout="")
            self.assertEqual(cloud.keep_offline(onedrive), os.name == "nt")
            if os.name == "nt":
                self.assertEqual(run.call_args.args[0][:3], ["attrib", "+P", "-U"])


class AutoSetupFlowTests(unittest.TestCase):
    """Due PC con lo stesso Google Drive: il primo crea ``Lectio``, il secondo la riconosce."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.drive = self.base / "G" / "Il mio Drive"
        self.drive.mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"LECTIO_DATA_DIR": str(self.base / "pc1"),
                                                "LECTIO_SUBJECTS_DIR": "",
                                                "LECTIO_SOUNDS_DIR": str(self.base / "sounds")})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_first_and_second_pc(self) -> None:
        layout = app_paths.ensure_data_layout()
        sqlite3.connect(layout["db"]).close()
        root = cloud.CloudRoot(cloud.GOOGLE_DRIVE, "Google Drive (G:)", self.drive, streaming=True)
        target = cloud.prepare_target(root)
        cloud.create_target(target)
        current = {key: Path(layout[key]) for key in ("db", "subjects", "transcripts", "pdf_images")}
        sync.enable_sync(current, target.folder)
        self.assertTrue(sync.folder_has_data(self.drive / "Lectio"))
        self.assertEqual(app_paths.sync_folder(), self.drive / "Lectio")
        second = cloud.prepare_target(root)
        self.assertEqual((second.folder, second.state), (self.drive / "Lectio", "existing"))
        self.assertEqual(second.created_by, sync.machine_name())


if __name__ == "__main__":
    unittest.main()
