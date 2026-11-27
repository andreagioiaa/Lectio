"""Test della v1.5 (fase 4): esporta/importa una materia in ZIP."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from study_manager.database import Database
from study_manager.services.subject_folders import SubjectFolders
from study_manager.services.subject_share import (
    DEFAULT_PARTS,
    MANIFEST_NAME,
    PART_EXAMS,
    PART_PERSONAL,
    PART_SLIDES,
    README_NAME,
    REPOSITORY_URL,
    ShareError,
    import_share,
    inspect_share,
    plan_export,
    suggest_import_name,
    write_export,
)


def close_db(db: Database) -> None:
    for connection in list(getattr(db, "_connections", [])):
        connection.close()


class ShareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db, self.folders = self._env("a")
        self._fill()

    def tearDown(self) -> None:
        for db in getattr(self, "_dbs", []):
            close_db(db)
        self.tmp.cleanup()

    def _env(self, name: str) -> tuple[Database, SubjectFolders]:
        folders = SubjectFolders(self.base / name / "Materie")
        db = Database(self.base / name / "db.sqlite")
        db.set_path_roots(subjects=folders.root, data=self.base / name / "dati")
        self._dbs = getattr(self, "_dbs", []) + [db]
        return db, folders

    def _fill(self) -> None:
        db, folders = self.db, self.folders
        db.save_subject("Analisi", "Prof. Rossi", "", 9, "", "note private")
        downloads = self.base / "Download"
        downloads.mkdir()
        files = {}
        for name, data in (("Lezione 1.pdf", b"%PDF-1"), ("Lezione 2.pdf", b"%PDF-2"), ("esercizi.docx", b"DOCX")):
            (downloads / name).write_bytes(data)
            files[name] = folders.copy_material(downloads / name, "Analisi")
        self.l1 = db.add_material("Lezione 1", str(files["Lezione 1.pdf"]), "Analisi", 2, academic_year="2025/26")
        db.set_material_hours(self.l1, 1.5)
        db.add_material("Lezione 2", str(files["Lezione 2.pdf"]), "Analisi", 2, academic_year="2024/25")
        db.add_material("Esercizi", str(files["esercizi.docx"]), "Analisi", 0)
        notes = folders.subfolder("Analisi", "notes")
        (notes / "Lezione 1.md").write_text("# Appunti", encoding="utf-8")
        (notes / "virus.exe").write_bytes(b"MZ")
        transcripts = folders.subfolder("Analisi", "transcripts")
        (transcripts / "lezione.txt").write_text("trascrizione", encoding="utf-8")
        block = db.add_note_block("Analisi", "Limiti", 1, pdf_path=str(files["Lezione 1.pdf"]),
                                  source="=== SLIDE 1 ===\n\n# Limiti\n\nTesto.",
                                  meta=json.dumps({"title": "L1", "doubts": [{"text": "dubbio", "card": False}]}))
        db.add_summary_chapter("Analisi", "Limiti", "# Limiti\n\nRiassunto.", note_block_id=block)
        exam_pdf = folders.copy_material(downloads / "Lezione 2.pdf", "Analisi", "exams")
        session = db.add_exam_session("Analisi", "18/06/2025", "Estivo", str(exam_pdf))
        other = db.add_exam_session("Analisi", "10/02/2025", "Invernale")
        q1 = db.add_exam_question("Analisi", "Teorema di Rolle", session, material_id=self.l1, solution="Ipotesi")
        q2 = db.add_exam_question("Analisi", "Teorema di Rolle (dimostrazione)", other)
        db.link_exam_questions(q1, q2)
        db.add_flashcard("Analisi", "Cos'e' un limite?", "Un valore", note_block_id=block)

    def _export(self, parts=None) -> Path:
        plan = plan_export(self.db, self.folders, "Analisi")
        return write_export(plan, parts or dict(DEFAULT_PARTS), self.base / "out" / "Analisi.zip")

    def test_zip_layout_readme_and_manifest(self) -> None:
        target = self._export()
        with zipfile.ZipFile(target) as archive:
            names = set(archive.namelist())
            readme = archive.read(README_NAME).decode("utf-8")
            manifest = json.loads(archive.read(MANIFEST_NAME))
        for expected in ("Slide/2025-26/Lezione 1.pdf", "Slide/2024-25/Lezione 2.pdf", "Documenti/esercizi.docx",
                         "Appunti/Lezione 1.md", "Trascrizioni/lezione.txt", "Riassunto/Riassunto_corso.md",
                         "Esami/Lezione 2.pdf", "Esami/Domande_esame.md", "Flashcard/Flashcard.csv"):
            self.assertIn(expected, names)
        self.assertNotIn("Appunti/virus.exe", names)
        self.assertIn(REPOSITORY_URL, readme)
        self.assertIn("Lectio", readme)
        self.assertEqual(manifest["format"], "lectio-materia")
        self.assertEqual(manifest["counts"]["exam_questions"], 2)
        # dati personali esclusi per default
        self.assertEqual(manifest["subject"]["notes"], "")
        self.assertTrue(all(m["logged_hours"] == 0 for m in manifest["materials"]))
        self.assertEqual(manifest["note_blocks"][0]["doubts"], [])

    def test_partial_export_and_personal_data(self) -> None:
        parts = dict(DEFAULT_PARTS)
        parts[PART_SLIDES] = False
        parts[PART_EXAMS] = False
        parts[PART_PERSONAL] = True
        with zipfile.ZipFile(self._export(parts)) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read(MANIFEST_NAME))
        self.assertFalse(any(name.startswith(("Slide/", "Esami/")) for name in names))
        self.assertEqual(manifest["exam_sessions"], [])
        self.assertEqual(manifest["subject"]["notes"], "note private")
        self.assertEqual(manifest["note_blocks"][0]["doubts"][0]["text"], "dubbio")
        self.assertEqual(manifest["note_blocks"][0]["material"], "")  # la slide non c'e'

    def test_round_trip_into_new_pc(self) -> None:
        target = self._export()
        db, folders = self._env("b")
        preview = inspect_share(target)
        self.assertEqual(preview["subject"]["name"], "Analisi")
        result = import_share(db, folders, target, "Analisi")
        self.assertEqual(result["materials"], 3)
        materials = {m["title"]: m for m in db.list_materials_for_subject("Analisi")}
        self.assertEqual(materials["Lezione 1"]["academic_year"], "2025/26")
        self.assertTrue(Path(materials["Lezione 1"]["file_path"]).is_file())
        self.assertEqual(Path(materials["Lezione 1"]["file_path"]).parent, folders.root / "Analisi" / "Slide")
        self.assertEqual(db.get_subject("Analisi")["teacher_name"], "Prof. Rossi")
        block = db.list_note_blocks("Analisi")[0]
        self.assertEqual(Path(block["pdf_path"]), Path(materials["Lezione 1"]["file_path"]))
        chapter = db.list_summary_chapters("Analisi")[0]
        self.assertEqual(chapter["note_block_id"], block["id"])
        questions = db.list_exam_questions("Analisi")
        self.assertEqual({q["occurrences"] for q in questions}, {2})
        self.assertEqual(next(q for q in questions if q["solution"])["topic"], "Lezione 1")
        session = next(s for s in db.list_exam_sessions("Analisi") if s["title"] == "Estivo")
        self.assertTrue(Path(session["pdf_path"]).is_file())
        cards = db.list_flashcards(subject="Analisi")
        self.assertEqual(cards[0]["note_block_id"], block["id"])
        self.assertTrue((folders.root / "Analisi" / "Trascrizioni" / "lezione.txt").is_file())

    def test_merge_does_not_duplicate(self) -> None:
        target = self._export()
        db, folders = self._env("b")
        import_share(db, folders, target, "Analisi")
        again = import_share(db, folders, target, "Analisi")
        for key in ("materials", "note_blocks", "summary_chapters", "exam_sessions", "exam_questions", "flashcards"):
            self.assertEqual(again[key], 0, key)
        self.assertEqual(len(db.list_materials_for_subject("Analisi")), 3)
        slides = sorted(p.name for p in (folders.root / "Analisi" / "Slide").iterdir())
        self.assertEqual(slides, ["Lezione 1.pdf", "Lezione 2.pdf"])
        # importata come nuova materia
        name = suggest_import_name(db, "Analisi")
        self.assertEqual(name, "Analisi (importata)")
        import_share(db, folders, target, name)
        self.assertEqual(len(db.list_materials_for_subject(name)), 3)
        self.assertEqual(suggest_import_name(db, "Analisi"), "Analisi (importata 2)")

    def test_rejects_foreign_and_malicious_zips(self) -> None:
        plain = self.base / "plain.zip"
        with zipfile.ZipFile(plain, "w") as archive:
            archive.writestr("a.txt", "x")
        with self.assertRaises(ShareError):
            inspect_share(plain)
        bad = self.base / "notzip.zip"
        bad.write_bytes(b"ciao")
        with self.assertRaises(ShareError):
            inspect_share(bad)
        evil = self.base / "evil.zip"
        manifest = {"format": "lectio-materia", "version": 1, "subject": {"name": "Evil"}}
        with zipfile.ZipFile(evil, "w") as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest))
            archive.writestr("../../fuori.txt", "x")
            archive.writestr("Appunti/script.bat", "x")
            archive.writestr("Appunti/ok.md", "x")
        db, folders = self._env("c")
        result = import_share(db, folders, evil, "Evil")
        self.assertEqual(result["files"], 1)
        self.assertEqual(result["skipped"], 2)
        self.assertFalse((self.base / "c" / "fuori.txt").exists())
        self.assertFalse((self.base / "fuori.txt").exists())
        future = self.base / "future.zip"
        with zipfile.ZipFile(future, "w") as archive:
            archive.writestr(MANIFEST_NAME, json.dumps({**manifest, "version": 99}))
        with self.assertRaises(ShareError):
            inspect_share(future)


if __name__ == "__main__":
    unittest.main()
