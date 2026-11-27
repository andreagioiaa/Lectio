"""Test del download di Pandoc per l'export Word dell'eseguibile."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from study_manager.services.pdf_tools import export, pandoc_setup
from study_manager.services.pdf_tools.pandoc_setup import (
    PANDOC_URL,
    PandocSetupCancelled,
    PandocSetupError,
    download_pandoc,
    extract_pandoc,
)


def make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, chunk_size: int):
        for start in range(0, len(self.payload), 4):
            yield self.payload[start:start + 4]


class PandocSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.target = self.base / "dati" / "pandoc.exe"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_url_points_to_official_windows_build(self) -> None:
        self.assertTrue(PANDOC_URL.startswith("https://github.com/jgm/pandoc/releases/download/"))
        self.assertTrue(PANDOC_URL.endswith("-windows-x86_64.zip"))

    def test_extract_only_pandoc_exe(self) -> None:
        archive = self.base / "p.zip"
        archive.write_bytes(make_zip({"pandoc-3.9/pandoc.exe": b"EXE", "pandoc-3.9/COPYRIGHT.txt": b"c"}))
        with mock.patch.object(pandoc_setup, "_check_version", return_value="pandoc 3.9"):
            path = extract_pandoc(archive, self.target)
        self.assertEqual(path.read_bytes(), b"EXE")
        self.assertEqual(sorted(p.name for p in self.target.parent.iterdir()), ["pandoc.exe"])

    def test_invalid_archives_are_rejected(self) -> None:
        archive = self.base / "p.zip"
        archive.write_bytes(make_zip({"readme.txt": b"x"}))
        with self.assertRaises(PandocSetupError):
            extract_pandoc(archive, self.target)
        archive.write_bytes(b"non uno zip")
        with self.assertRaises(PandocSetupError):
            extract_pandoc(archive, self.target)
        archive.write_bytes(make_zip({"pandoc.exe": b"EXE"}))
        with mock.patch.object(pandoc_setup, "_check_version", side_effect=PandocSetupError("no")):
            with self.assertRaises(PandocSetupError):
                extract_pandoc(archive, self.target)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.target.with_name("pandoc.exe.part").exists())

    def test_download_with_progress_and_cancel(self) -> None:
        payload = make_zip({"pandoc-3.9/pandoc.exe": b"EXE-BINARY"})
        progress: list[float] = []
        with mock.patch("requests.get", return_value=FakeResponse(payload)), \
                mock.patch.object(pandoc_setup, "_check_version", return_value="pandoc 3.9"):
            path = download_pandoc(lambda f, m: progress.append(f), target=self.target)
            self.assertEqual(path.read_bytes(), b"EXE-BINARY")
            self.assertEqual(progress[-1], 1.0)
            with self.assertRaises(PandocSetupCancelled):
                download_pandoc(cancelled=lambda: True, target=self.base / "altro" / "pandoc.exe")

    def test_downloaded_pandoc_in_data_dir_is_found(self) -> None:
        with mock.patch.dict(os.environ, {"LECTIO_DATA_DIR": str(self.base / "dati"), "LECTIO_PANDOC": ""}):
            exe = pandoc_setup.pandoc_target()
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_bytes(b"x")
            self.assertEqual(Path(export.find_pandoc()), exe)


if __name__ == "__main__":
    unittest.main()
