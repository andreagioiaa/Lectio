"""Stato di studio delle slide e inserimento di piu' domande insieme."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from study_manager.database import SCHEMA_VERSION, Database
from study_manager.services.course_summary import (
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_TODO,
    material_status,
)
from study_manager.services.exam_questions import QUESTION_SEPARATOR, split_pasted_questions


def close_db(db: Database) -> None:
    for connection in list(getattr(db, "_connections", [])):
        connection.close()


class SlideStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "db.sqlite")
        self.pdf = self.root / "Lezione 1.pdf"
        self.pdf.write_bytes(b"%PDF-1.4")
        self.material_id = self.db.add_material("Lezione 1", str(self.pdf), "Analisi", 0)

    def tearDown(self) -> None:
        close_db(self.db)
        self.tmp.cleanup()

    def status(self) -> str:
        material = self.db.get_material(self.material_id)
        return material_status(material, self.db.summary_pdf_paths("Analisi"),
                               self.db.note_block_pdf_paths("Analisi"))

    def test_todo_then_opened_then_completed(self) -> None:
        self.assertEqual(self.status(), STATUS_TODO)
        self.db.mark_material_opened_by_path(self.pdf)
        self.assertEqual(self.status(), STATUS_IN_PROGRESS)
        self.db.set_material_completed(self.material_id, True)
        self.assertEqual(self.status(), STATUS_DONE)
        self.db.set_material_completed(self.material_id, False)
        self.assertEqual(self.status(), STATUS_IN_PROGRESS)

    def test_logged_hours_mean_in_progress(self) -> None:
        self.db.add_material_hours(self.material_id, 0.5)
        self.assertEqual(self.status(), STATUS_IN_PROGRESS)

    def test_notes_in_progress_and_summary_done(self) -> None:
        block = self.db.add_note_block("Analisi", "Limiti", pdf_path=str(self.pdf), source="testo")
        self.assertEqual(self.status(), STATUS_IN_PROGRESS)
        self.db.add_summary_chapter("Analisi", "Limiti", "testo", note_block_id=block)
        self.assertEqual(self.status(), STATUS_DONE)

    def test_migration_adds_status_columns(self) -> None:
        close_db(self.db)
        path = self.root / "old.sqlite"
        with sqlite3.connect(path) as conn:
            conn.executescript(
                "CREATE TABLE materials (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                "file_path TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', "
                "target_hours REAL NOT NULL DEFAULT 0, logged_hours REAL NOT NULL DEFAULT 0, "
                "created_at TEXT NOT NULL);"
                "INSERT INTO materials(title, created_at) VALUES ('Vecchia', '2025-01-01 10:00:00');"
                "PRAGMA user_version = 11;"
            )
        conn.close()
        db = Database(path)
        try:
            material = db.list_materials()[0]
            self.assertEqual(material["opened_at"], "")
            self.assertEqual(material["completed_at"], "")
            db.set_material_completed(int(material["id"]), True)
            self.assertTrue(db.get_material(int(material["id"]))["completed_at"])
        finally:
            close_db(db)
        with sqlite3.connect(path) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        conn.close()


class PastedQuestionsTests(unittest.TestCase):
    def test_blank_lines_split(self) -> None:
        questions = split_pasted_questions("Prima domanda\nsu due righe\n\n\nSeconda\n\n  \nTerza\n")
        self.assertEqual([q["text"] for q in questions], ["Prima domanda\nsu due righe", "Seconda", "Terza"])
        self.assertEqual([q["number"] for q in questions], [1, 2, 3])

    def test_separator_keeps_blank_lines(self) -> None:
        text = f"{QUESTION_SEPARATOR}\nA\n\nancora A\n{QUESTION_SEPARATOR}\nB\n"
        self.assertEqual([q["text"] for q in split_pasted_questions(text)], ["A\n\nancora A", "B"])

    def test_empty(self) -> None:
        self.assertEqual(split_pasted_questions("  \n\n"), [])


if __name__ == "__main__":
    unittest.main()
