"""Capitoli di appunti (blocchi) di PDF e Slide collegati alle materie."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from study_manager.database import Database


class NoteBlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "study.db")

    def tearDown(self) -> None:
        self.db.close_all()
        self.tmp.cleanup()

    def test_blocks_are_listed_by_chapter_number(self) -> None:
        self.db.add_note_block("Reti", "Esempio", 2)
        self.db.add_note_block("Reti", "Introduzione", 1)
        self.db.add_note_block("Reti", "Routing")  # numero successivo automatico
        self.db.add_note_block("Fisica", "Altro", 1)
        blocks = self.db.list_note_blocks("reti")
        self.assertEqual([(b["position"], b["title"]) for b in blocks],
                         [(1, "Introduzione"), (2, "Esempio"), (3, "Routing")])
        self.assertEqual(self.db.next_note_block_position("Reti"), 4)
        self.assertEqual(self.db.get_subject("Reti")["note_blocks"], 3)

    def test_close_saves_content_page_and_cursor(self) -> None:
        block = self.db.add_note_block("Reti", "Introduzione", 1, source="testo")
        self.db.update_note_block(block, source="testo finale", page=4, cursor="20.3", status="closed")
        row = self.db.get_note_block(block)
        self.assertEqual((row["source"], row["page"], row["cursor"], row["status"]),
                         ("testo finale", 4, "20.3", "closed"))
        self.assertTrue(row["closed_at"])
        self.db.update_note_block(block, status="open")
        self.assertEqual(self.db.get_note_block(block)["closed_at"], "")

    def test_flashcards_linked_to_chapter(self) -> None:
        block = self.db.add_note_block("Reti", "Introduzione", 1)
        card = self.db.add_flashcard("Reti", "Cos'e' un router?", "Un nodo...", note_block_id=block)
        self.assertEqual([c["id"] for c in self.db.list_flashcards(note_block_id=block)], [card])
        self.assertEqual(self.db.list_note_blocks("Reti")[0]["flashcards"], 1)
        self.assertEqual(self.db.note_block_labels()[block], "1. Introduzione")
        # Spostare il capitolo sposta anche le sue flashcard.
        self.db.update_note_block(block, subject="Reti di calcolatori")
        self.assertEqual(self.db.get_flashcard(card)["subject"], "Reti di calcolatori")
        # Eliminare il capitolo non elimina le flashcard.
        self.db.delete_note_block(block)
        self.assertIsNone(self.db.get_flashcard(card)["note_block_id"])

    def test_subject_rename_and_backup(self) -> None:
        self.db.save_subject("Reti")
        block = self.db.add_note_block("Reti", "Introduzione", 1, source="abc")
        self.db.add_flashcard("Reti", "D", "R", note_block_id=block)
        self.db.save_subject("Reti 2", original_name="Reti")
        self.assertEqual(self.db.get_note_block(block)["subject"], "Reti 2")
        tables = self.db.snapshot_tables()
        self.assertEqual(len(tables["note_blocks"]), 1)
        self.db.restore_tables(tables)
        self.assertEqual(self.db.list_flashcards()[0]["note_block_id"], block)
        self.assertEqual(self.db.get_note_block(block)["source"], "abc")

    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.db.add_note_block("", "Titolo")
        with self.assertRaises(ValueError):
            self.db.add_note_block("Reti", "  ")


if __name__ == "__main__":
    unittest.main()
