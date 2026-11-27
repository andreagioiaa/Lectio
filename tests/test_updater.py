"""Test degli aggiornamenti: confronto versioni, manifest, download e sostituzione dell'exe."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from study_manager.config import APP_VERSION, MIN_SUPPORTED_VERSION
from study_manager.database import Database
from study_manager.services import updater


ROOT = Path(__file__).resolve().parent.parent
URL = updater.ALLOWED_DOWNLOAD_PREFIX + "v0.2.0/Lectio.exe"


def manifest(latest: str = "0.2.0", min_version: str = "0.1.0", payload: bytes = b"exe", **extra) -> dict:
    data = {
        "latest": latest,
        "min_version": min_version,
        "url": URL,
        "page": "https://github.com/andreagioiaa/Lectio/releases/tag/v" + latest,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "notes": "Novita'",
    }
    data.update(extra)
    return data


class VersionTests(unittest.TestCase):
    def test_parse_version(self) -> None:
        self.assertEqual(updater.parse_version("v0.1.5"), (0, 1, 5))
        self.assertEqual(updater.parse_version("10.0.12"), (10, 0, 12))
        for bad in ("", "1.2", "1.2.3.4", "a.b.c", None, "1.-2.3"):
            self.assertIsNone(updater.parse_version(bad))

    def test_numeric_not_lexical_order(self) -> None:
        info = updater.evaluate(manifest(latest="0.1.10"), current="0.1.9")
        self.assertIsNotNone(info)

    def test_config_versions_are_consistent(self) -> None:
        self.assertIsNotNone(updater.parse_version(APP_VERSION))
        self.assertLessEqual(updater.parse_version(MIN_SUPPORTED_VERSION), updater.parse_version(APP_VERSION))


class EvaluateTests(unittest.TestCase):
    def test_up_to_date(self) -> None:
        self.assertIsNone(updater.evaluate(manifest(latest="0.1.5"), current="0.1.5"))
        self.assertIsNone(updater.evaluate(manifest(latest="0.1.4"), current="0.1.5"))

    def test_optional_update(self) -> None:
        info = updater.evaluate(manifest(), current="0.1.5")
        self.assertIsNotNone(info)
        self.assertEqual(info.latest, "0.2.0")
        self.assertFalse(info.required)

    def test_required_update(self) -> None:
        info = updater.evaluate(manifest(min_version="0.2.0"), current="0.1.5")
        self.assertTrue(info.required)

    def test_known_minimum_is_kept(self) -> None:
        # il manifest nuovo ha un minimo piu' basso: vale comunque quello gia' visto
        info = updater.evaluate(manifest(min_version="0.1.0"), current="0.1.5", known_min="0.1.8")
        self.assertTrue(info.required)
        self.assertEqual(info.min_version, "0.1.8")

    def test_rejects_foreign_urls_and_bad_hashes(self) -> None:
        self.assertIsNone(updater.evaluate(manifest(url="https://example.com/Lectio.exe"), current="0.1.5"))
        self.assertIsNone(
            updater.evaluate(manifest(url="https://github.com/someone/else/releases/download/x.exe"), current="0.1.5")
        )
        self.assertIsNone(updater.evaluate(manifest(sha256="abc"), current="0.1.5"))
        self.assertIsNone(updater.evaluate(None, current="0.1.5"))
        self.assertIsNone(updater.evaluate({"latest": "x"}, current="0.1.5"))

    def test_below_minimum(self) -> None:
        self.assertTrue(updater.is_below_minimum("0.1.5", "0.2.0"))
        self.assertFalse(updater.is_below_minimum("0.2.0", "0.2.0"))
        self.assertFalse(updater.is_below_minimum("0.1.5", ""))


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")

    def tearDown(self) -> None:
        for connection in list(getattr(self.db, "_connections", [])):
            connection.close()
        self.tmp.cleanup()

    def test_cached_update_works_offline_and_minimum_never_drops(self) -> None:
        self.assertIsNone(updater.cached_update(self.db, current="0.1.5"))
        updater.remember_manifest(self.db, manifest(min_version="0.2.0"))
        updater.remember_manifest(self.db, manifest(latest="0.2.1", min_version="0.1.0"))
        self.assertEqual(self.db.get_setting(updater.SETTING_KNOWN_MIN), "0.2.0")
        info = updater.cached_update(self.db, current="0.1.5")
        self.assertEqual(info.latest, "0.2.1")
        self.assertTrue(info.required)


class FakeResponse:
    def __init__(self, payload: bytes, status: int = 200):
        self.payload = payload
        self.status_code = status
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size: int):
        for start in range(0, len(self.payload), chunk_size):
            yield self.payload[start:start + chunk_size]

    def json(self):
        return json.loads(self.payload)


class DownloadAndReplaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.exe = self.folder / "Lectio.exe"
        self.exe.write_bytes(b"old")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_download_verifies_hash(self) -> None:
        payload = b"new-exe" * 1000
        info = updater.evaluate(manifest(payload=payload), current="0.1.5")
        progress: list[tuple[int, int]] = []
        with mock.patch("requests.get", return_value=FakeResponse(payload)):
            target = updater.download(info, self.exe, lambda r, t: progress.append((r, t)))
        self.assertEqual(target, self.folder / "Lectio.new.exe")
        self.assertEqual(target.read_bytes(), payload)
        self.assertEqual(progress[-1], (len(payload), len(payload)))

    def test_download_rejects_tampered_file(self) -> None:
        info = updater.evaluate(manifest(payload=b"real"), current="0.1.5")
        with mock.patch("requests.get", return_value=FakeResponse(b"evil")):
            with self.assertRaises(updater.UpdateError):
                updater.download(info, self.exe)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["Lectio.exe"])

    def test_fetch_manifest_errors(self) -> None:
        with mock.patch("requests.get", return_value=FakeResponse(b"{}", status=404)):
            with self.assertRaises(updater.UpdateError):
                updater.fetch_manifest()
        with mock.patch("requests.get", return_value=FakeResponse(json.dumps(manifest()).encode())):
            self.assertEqual(updater.fetch_manifest()["latest"], "0.2.0")

    def test_replace_executable_keeps_old_copy(self) -> None:
        new = self.folder / "Lectio.new.exe"
        new.write_bytes(b"new")
        updater.replace_executable(new, self.exe)
        self.assertEqual(self.exe.read_bytes(), b"new")
        self.assertEqual((self.folder / "Lectio.old.exe").read_bytes(), b"old")
        updater.cleanup_previous(self.exe)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["Lectio.exe"])

    def test_replace_executable_rolls_back(self) -> None:
        new = self.folder / "Lectio.new.exe"
        new.write_bytes(b"new")
        real_replace = updater.os.replace

        def failing(src, dst):
            if str(src).endswith(".tmp"):
                raise OSError("bloccato")
            return real_replace(src, dst)

        with mock.patch.object(updater.os, "replace", side_effect=failing), mock.patch.object(updater.time, "sleep"):
            with self.assertRaises(OSError):
                updater.replace_executable(new, self.exe)
        self.assertEqual(self.exe.read_bytes(), b"old")
        self.assertFalse((self.folder / "Lectio.exe.tmp").exists())


    def test_finish_update_waits_replaces_and_restarts(self) -> None:
        new = self.folder / "Lectio.new.exe"
        new.write_bytes(b"new")
        old_process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])
        try:
            with mock.patch.object(updater, "current_exe", return_value=new), \
                    mock.patch.object(updater.subprocess, "Popen") as popen, \
                    mock.patch.object(updater, "_message_box") as box:
                code = updater.finish_update(
                    ["--finish-update", "--target", str(self.exe), "--wait-pid", str(old_process.pid)]
                )
            self.assertIsNotNone(old_process.poll(), "il vecchio processo doveva essere gia' uscito")
        finally:
            old_process.wait()
        self.assertEqual(code, 0)
        box.assert_not_called()
        self.assertEqual(self.exe.read_bytes(), b"new")
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], [str(self.exe)])


class ReleaseToolTests(unittest.TestCase):
    def run_tool(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "tools" / "release.py"), *args],
            capture_output=True, text=True, cwd=ROOT,
        )

    def test_info_and_tag_check(self) -> None:
        result = self.run_tool("info")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["version"], APP_VERSION)
        self.assertEqual(self.run_tool("check-tag", f"v{APP_VERSION}").returncode, 0)
        self.assertNotEqual(self.run_tool("check-tag", "v999.0.0").returncode, 0)

    def test_version_info_matches_app_version(self) -> None:
        text = (ROOT / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn(f"StringStruct('FileVersion', '{APP_VERSION}.0')", text)

    def test_manifest_is_accepted_by_the_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "Lectio.exe"
            exe.write_bytes(b"binary")
            notes = Path(tmp) / "notes.md"
            notes.write_text("<!--\nistruzioni\n-->\n- novita'\n", encoding="utf-8")
            out = Path(tmp) / "update.json"
            result = self.run_tool("manifest", "--exe", str(exe), "--out", str(out), "--notes-file", str(notes))
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["notes"], "- novita'")
        self.assertEqual(data["sha256"], hashlib.sha256(b"binary").hexdigest())
        info = updater.evaluate(data, current="0.0.1")
        self.assertIsNotNone(info)
        self.assertEqual(info.latest, APP_VERSION)


if __name__ == "__main__":
    unittest.main()
