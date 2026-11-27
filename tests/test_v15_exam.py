"""Test della v1.5 (fase 3): appelli, domande d'esame, ricorrenze ed export."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from study_manager.database import Database
from study_manager.services import backup
from study_manager.services.exam_questions import (
    QUESTION_SEPARATOR,
    exam_markdown,
    guess_exam_date,
    questions_to_review_text,
    review_text_to_questions,
    similar_questions,
    similarity,
    split_exam_text,
)
from study_manager.services.subject_folders import SubjectFolders


def close_db(db: Database) -> None:
    for connection in list(getattr(db, "_connections", [])):
        connection.close()


EXAM_TEXT = """=== SLIDE 1 ===

Universita' - Analisi 1 - Appello del 18/06/2025
Cognome e nome: ______

**Esercizio 1.** Calcolare il limite $\\lim_{x \\to 0} \\frac{\\sin x}{x}$.

a) motivare la risposta

Esercizio 2: Enunciare il teorema di Rolle.

1. ipotesi
2. tesi

## Esercizio 3
Studiare la funzione $f(x) = x^2$.
"""


class SplitTests(unittest.TestCase):
    def test_keywords_drop_header_and_keep_sub_items(self) -> None:
        questions = split_exam_text(EXAM_TEXT)
        self.assertEqual([q["number"] for q in questions], [1, 2, 3])
        self.assertTrue(questions[0]["text"].startswith("Calcolare il limite"))
        self.assertIn("a) motivare", questions[0]["text"])
        self.assertIn("1. ipotesi\n2. tesi", questions[1]["text"])
        self.assertNotIn("Cognome", " ".join(q["text"] for q in questions))

    def test_numbered_list_only_in_sequence_and_top_level(self) -> None:
        text = "Compito\n\n1. Prima\n   1. sotto\n2) Seconda\n5. fuori sequenza\n3. Terza"
        questions = split_exam_text(text)
        self.assertEqual([q["text"].split("\n")[0] for q in questions], ["Prima", "Seconda", "Terza"])
        self.assertIn("5. fuori sequenza", questions[1]["text"])

    def test_no_marker_gives_one_question(self) -> None:
        self.assertEqual(split_exam_text("Parla dei limiti."), [{"number": 1, "text": "Parla dei limiti."}])
        self.assertEqual(split_exam_text("   "), [])

    def test_review_round_trip(self) -> None:
        questions = split_exam_text(EXAM_TEXT)
        text = questions_to_review_text(questions)
        self.assertEqual(text.count(QUESTION_SEPARATOR), 3)
        self.assertEqual(review_text_to_questions(text), questions)
        merged = text.replace(f"{QUESTION_SEPARATOR}\nEnunciare", "Enunciare")
        self.assertEqual(len(review_text_to_questions(merged)), 2)
        self.assertEqual(review_text_to_questions("testo libero\n=== domanda ===\nseconda")[1]["number"], 2)

    def test_guess_date(self) -> None:
        self.assertEqual(guess_exam_date("tema", EXAM_TEXT), "18/06/2025")
        self.assertEqual(guess_exam_date("Analisi_2025-02-10", ""), "10/02/2025")
        self.assertEqual(guess_exam_date("tema", "99/99/2025"), "")


class SimilarityTests(unittest.TestCase):
    def test_similar_questions_skip_same_session(self) -> None:
        question = {"id": 1, "session_id": 1, "text": "Enunciare il teorema di Rolle."}
        candidates = [
            question,
            {"id": 2, "session_id": 1, "text": "Enunciare il teorema di Rolle."},
            {"id": 3, "session_id": 2, "text": "Enunciare e dimostrare il teorema di Rolle"},
            {"id": 4, "session_id": 3, "text": "Calcolare un integrale doppio"},
        ]
        found = similar_questions(question, candidates)
        self.assertEqual([item[1]["id"] for item in found], [3])
        self.assertGreater(similarity("Teorema di Rolle", "teorema di rolle!"), 0.95)


class ExamDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Database(self.base / "db.sqlite")

    def tearDown(self) -> None:
        close_db(self.db)
        self.tmp.cleanup()

    def test_sessions_questions_topics_and_groups(self) -> None:
        slide = self.db.add_material("Lezione 03 - Limiti", "C:/l3.pdf", "Analisi", 0)
        june = self.db.add_exam_session("Analisi", "18/06/2025", "Estivo")
        feb = self.db.add_exam_session("Analisi", "2025-02-10")
        self.db.add_exam_session("Analisi", "")
        sessions = self.db.list_exam_sessions("Analisi")
        self.assertEqual([s["exam_date"] for s in sessions], ["2025-06-18", "2025-02-10", ""])
        q1 = self.db.add_exam_question("Analisi", "Limite notevole", june, material_id=slide)
        q2 = self.db.add_exam_question("Analisi", "Rolle", june)
        self.assertEqual(self.db.get_exam_question(q2)["number"], 2)
        q3 = self.db.add_exam_question("Analisi", "Limite notevole?", feb, material_id=slide)
        loose = self.db.add_exam_question("Analisi", "Chiesta all'orale")
        self.assertEqual(self.db.get_exam_question(loose)["number"], 1)
        self.db.link_exam_questions(q1, q3)
        rows = {row["id"]: row for row in self.db.list_exam_questions("Analisi")}
        self.assertEqual(rows[q1]["occurrences"], 2)
        self.assertEqual(rows[q3]["topic"], "Lezione 03 - Limiti")
        self.assertEqual(rows[q2]["occurrences"], 1)
        self.assertEqual(list(rows)[-1], loose)  # "Senza appello" in fondo
        self.db.delete_material(slide)
        self.assertIsNone(self.db.get_exam_question(q1)["material_id"])
        self.db.delete_exam_question(q3)
        self.assertIsNone(self.db.get_exam_question(q1)["group_id"])
        self.db.delete_exam_session(june)
        self.assertIsNone(self.db.get_exam_question(q1))
        with self.assertRaises(ValueError):
            self.db.add_exam_question("Analisi", "  ")
        with self.assertRaises(ValueError):
            self.db.add_exam_session("Analisi", "31/02/2025")

    def test_link_merges_groups_and_unlink(self) -> None:
        a, b, c = (self.db.add_exam_session("Fisica", f"0{i}/01/2025") for i in (1, 2, 3))
        qa = self.db.add_exam_question("Fisica", "Moto", a)
        qb = self.db.add_exam_question("Fisica", "Moto", b)
        qc = self.db.add_exam_question("Fisica", "Moto", c)
        self.db.link_exam_questions(qa, qb)
        self.db.link_exam_questions(qc, qb)
        self.assertEqual({row["occurrences"] for row in self.db.list_exam_questions("Fisica")}, {3})
        self.db.unlink_exam_question(qb)
        self.assertEqual(sorted(row["occurrences"] for row in self.db.list_exam_questions("Fisica")), [1, 2, 2])

    def test_rename_backup_and_relative_pdf(self) -> None:
        folders = SubjectFolders(self.base / "Materie")
        self.db.set_path_roots(subjects=folders.root, data=self.base / "dati")
        source = self.base / "tema.pdf"
        source.write_bytes(b"%PDF")
        copy = folders.copy_material(source, "Analisi", "exams")
        self.assertEqual(copy.parent.name, "Esami")
        session = self.db.add_exam_session("Analisi", "", "Tema", str(copy))
        raw = self.db._connection().execute("SELECT pdf_path FROM exam_sessions").fetchone()[0]
        self.assertEqual(raw, "<materie>/Analisi/Esami/tema.pdf")
        self.assertEqual(Path(self.db.get_exam_session(session)["pdf_path"]), copy)
        self.db.add_exam_question("Analisi", "Domanda", session, solution="Risposta")
        self.db.save_subject("Analisi")
        self.db.save_subject("Analisi 1", original_name="Analisi")
        self.assertEqual(len(self.db.list_exam_questions("Analisi 1")), 1)
        self.assertEqual(len(self.db.list_exam_sessions("Analisi 1")), 1)
        paths = {"data": self.base / "dati", "transcripts": self.base / "dati" / "transcripts"}
        paths["transcripts"].mkdir(parents=True)
        exported = backup.export_backup(self.db, paths, self.base / "b.zip")
        other = Database(self.base / "other.sqlite")
        try:
            backup.import_backup(other, paths, exported["path"])
            self.assertEqual(other.list_exam_questions("Analisi 1")[0]["solution"], "Risposta")
        finally:
            close_db(other)


class ExportTests(unittest.TestCase):
    def test_markdown_grouped_by_session(self) -> None:
        sessions = [{"id": 1, "exam_date": "2025-06-18", "title": "Estivo"}, {"id": 2, "exam_date": "", "title": ""}]
        questions = [
            {"id": 10, "session_id": 1, "number": 2, "text": "Rolle", "topic": None, "occurrences": 1},
            {"id": 11, "session_id": 1, "number": 1, "text": "Limite\n$$x$$", "topic": "Lezione 3",
             "occurrences": 2, "solution": "Vale 1", "notes": "chiesto spesso"},
            {"id": 12, "session_id": None, "number": 1, "text": "Orale", "topic": None, "occurrences": 1},
        ]
        md = exam_markdown("Analisi", sessions, questions)
        self.assertTrue(md.startswith("# Domande d'esame - Analisi"))
        self.assertIn("3 domande in 2 appelli, 1 ricorrenti.", md)
        self.assertIn("## Appello 18/06/2025 - Estivo", md)
        self.assertLess(md.index("1. Limite *(Lezione 3, uscita 2 volte)*"), md.index("2. Rolle *(Non indicato)*"))
        self.assertIn("   $$x$$", md)
        self.assertIn("   **Soluzione:**\n   Vale 1", md)
        self.assertIn("   > Note: chiesto spesso", md)
        self.assertIn("## Senza appello", md)
        self.assertNotIn("Appello senza data", md)  # appello vuoto: nessuna sezione
        self.assertNotIn("Soluzione", exam_markdown("Analisi", sessions, questions, include_solutions=False))


if __name__ == "__main__":
    unittest.main()
