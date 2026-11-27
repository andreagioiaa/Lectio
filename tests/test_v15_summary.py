"""Test della v1.5 (fase 2): riassunto del corso e riassunto totale."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from study_manager.database import Database
from study_manager.services import backup
from study_manager.services.course_summary import (
    QUICK_SUMMARY_NAME,
    build_summary_source,
    export_summary,
    generate_quick_summary,
    natural_key,
    shift_headings,
    slide_materials,
    summary_markdown,
)
from study_manager.services.pdf_tools.export import ExportError, _latex_error, find_latex_engine


def close_db(db: Database) -> None:
    for connection in list(getattr(db, "_connections", [])):
        connection.close()


class SummaryDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "db.sqlite")

    def tearDown(self) -> None:
        close_db(self.db)
        self.tmp.cleanup()

    def titles(self, subject: str = "Analisi") -> list[tuple[int, str]]:
        return [(row["position"], row["title"]) for row in self.db.list_summary_chapters(subject)]

    def test_add_in_position_and_move(self) -> None:
        self.db.add_summary_chapter("Analisi", "Limiti", "a")
        self.db.add_summary_chapter("Analisi", "Derivate", "b")
        self.db.add_summary_chapter("Analisi", "Introduzione", "c", position=1)
        self.assertEqual(self.titles(), [(1, "Introduzione"), (2, "Limiti"), (3, "Derivate")])
        derivate = self.db.list_summary_chapters("Analisi")[2]["id"]
        self.assertTrue(self.db.move_summary_chapter(derivate, -1))
        self.assertEqual(self.titles(), [(1, "Introduzione"), (2, "Derivate"), (3, "Limiti")])
        self.assertFalse(self.db.move_summary_chapter(self.db.list_summary_chapters("Analisi")[0]["id"], -1))
        self.db.delete_summary_chapter(derivate)
        self.assertEqual(self.titles(), [(1, "Introduzione"), (2, "Limiti")])

    def test_overwrite_keeps_history_and_restore(self) -> None:
        chapter = self.db.add_summary_chapter("Analisi", "Limiti", "prima versione")
        self.db.overwrite_summary_chapter(chapter, "seconda versione")
        self.assertEqual(self.db.get_summary_chapter(chapter)["source"], "seconda versione")
        versions = self.db.list_summary_versions(chapter)
        self.assertEqual(len(versions), 1)
        self.assertEqual(self.db.list_summary_chapters("Analisi")[0]["versions"], 1)
        self.db.restore_summary_version(versions[0]["id"])
        self.assertEqual(self.db.get_summary_chapter(chapter)["source"], "prima versione")
        remaining = self.db._connection().execute(
            "SELECT source FROM summary_chapter_versions WHERE chapter_id = ?", (chapter,)
        ).fetchall()
        self.assertEqual([row["source"] for row in remaining], ["seconda versione"])

    def test_linked_block_and_validation(self) -> None:
        block = self.db.add_note_block("Analisi", "Cap 1", 1, source="x")
        chapter = self.db.add_summary_chapter("Analisi", "Cap 1", "x", note_block_id=block)
        self.assertEqual(self.db.find_summary_chapter_for_block("analisi", block)["id"], chapter)
        self.assertIsNone(self.db.find_summary_chapter_for_block("Fisica", block))
        self.assertEqual(self.db.list_summary_chapters("Analisi")[0]["block_title"], "Cap 1")
        self.db.delete_note_block(block)
        self.assertIsNone(self.db.get_summary_chapter(chapter)["note_block_id"])
        with self.assertRaises(ValueError):
            self.db.add_summary_chapter("", "T", "x")
        with self.assertRaises(ValueError):
            self.db.add_summary_chapter("Analisi", " ", "x")

    def test_subject_rename_moves_chapters(self) -> None:
        self.db.save_subject("Analisi")
        self.db.add_summary_chapter("Analisi", "Limiti", "a")
        self.db.save_subject("Analisi 1", original_name="Analisi")
        self.assertEqual(self.titles("Analisi"), [])
        self.assertEqual(self.titles("Analisi 1"), [(1, "Limiti")])

    def test_backup_round_trip_includes_summary(self) -> None:
        chapter = self.db.add_summary_chapter("Analisi", "Limiti", "testo")
        self.db.overwrite_summary_chapter(chapter, "testo 2")
        base = Path(self.tmp.name)
        paths = {"data": base / "dati", "transcripts": base / "dati" / "transcripts"}
        paths["transcripts"].mkdir(parents=True)
        exported = backup.export_backup(self.db, paths, base / "b.zip")
        other = Database(base / "other.sqlite")
        try:
            backup.import_backup(other, paths, exported["path"])
            self.assertEqual(other.list_summary_chapters("Analisi", with_source=True)[0]["source"], "testo 2")
            self.assertEqual(len(other.list_summary_versions(chapter)), 1)
        finally:
            close_db(other)


class SummaryTextTests(unittest.TestCase):
    CHAPTERS = [
        {"position": 1, "title": "Introduzione", "fmt": "markdown",
         "source": "=== SLIDE 1 ===\n\n# Cos'e'\n\nTesto $x$.\n\n```\n# non un titolo\n```"},
        {"position": 2, "title": "Limiti", "fmt": "latex", "source": "\\section*{Definizione}\n\nIl limite."},
    ]

    def test_shift_headings_skips_code(self) -> None:
        text = "# A\n\n```\n# codice\n```\n\n## B"
        self.assertEqual(shift_headings(text, 1), "## A\n\n```\n# codice\n```\n\n### B")
        self.assertEqual(shift_headings("###### Z", 2), "###### Z")

    def test_build_source_and_markdown(self) -> None:
        source = build_summary_source(self.CHAPTERS)
        self.assertIn("# 1. Introduzione", source)
        self.assertIn("## Cos'e'", source)
        self.assertIn("# 2. Limiti", source)
        self.assertNotIn("=== SLIDE", source)
        self.assertIn("# Introduzione", build_summary_source(self.CHAPTERS, numbered_titles=False))
        md = summary_markdown("Analisi", self.CHAPTERS)
        self.assertTrue(md.startswith("# Riassunto del corso - Analisi"))
        self.assertIn("## Indice\n\n- 1. Introduzione\n- 2. Limiti", md)
        self.assertIn("## 1. Introduzione", md)
        self.assertIn("### Cos'e'", md)
        self.assertIn("# non un titolo", md)

    def test_export_md_and_tex(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            md = export_summary("Analisi", self.CHAPTERS, "md", Path(out) / "R.md")
            self.assertIn("## 2. Limiti", md.read_text(encoding="utf-8"))
            tex = export_summary("Analisi", self.CHAPTERS, "tex", Path(out) / "R.tex").read_text(encoding="utf-8")
            self.assertIn("\\tableofcontents", tex)
            self.assertIn("\\section{Introduzione}", tex)
            self.assertIn("\\subsection{Definizione}", tex)
            with self.assertRaises(ExportError):
                export_summary("Analisi", [], "md", Path(out) / "vuoto.md")
            with self.assertRaises(ExportError):
                export_summary("Analisi", self.CHAPTERS, "odt", Path(out) / "R.odt")

    @unittest.skipUnless(find_latex_engine(), "Nessuna distribuzione LaTeX installata")
    def test_export_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            pdf = export_summary("Analisi", self.CHAPTERS, "pdf", Path(out) / "R.pdf")
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))

    def test_latex_error_line(self) -> None:
        log = "This is pdfTeX\n./documento.tex:12: Undefined control sequence.\n! Emergency stop."
        self.assertEqual(_latex_error(log), "./documento.tex:12: Undefined control sequence.")
        self.assertEqual(_latex_error(""), "errore sconosciuto")


class QuickSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.files = {}
        for name in ("Lezione 10.pdf", "Lezione 2.pdf", "Lezione 1.pdf", "esercizi.docx", "slide.pptx"):
            path = self.base / name
            path.write_bytes(b"x")
            self.files[name] = path
        self.materials = [
            {"id": 1, "title": "L10", "file_path": str(self.files["Lezione 10.pdf"]), "academic_year": "2025/26"},
            {"id": 2, "title": "L2", "file_path": str(self.files["Lezione 2.pdf"]), "academic_year": "2024/25"},
            {"id": 3, "title": "L1", "file_path": str(self.files["Lezione 1.pdf"]), "academic_year": "2025/26"},
            {"id": 4, "title": "Esercizi", "file_path": str(self.files["esercizi.docx"])},
            {"id": 5, "title": "PPT", "file_path": str(self.files["slide.pptx"])},
        ]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_natural_order_and_filter(self) -> None:
        self.assertLess(natural_key("Lezione 2.pdf"), natural_key("Lezione 10.pdf"))
        titles = [row["title"] for row in slide_materials(self.materials)]
        self.assertEqual(titles, ["L1", "L2", "L10", "PPT"])
        self.assertEqual([row["title"] for row in slide_materials(self.materials, "2025/26")], ["L1", "L10"])

    def test_uses_note_blocks_and_converts_the_rest(self) -> None:
        blocks = [
            {"pdf_path": str(self.files["Lezione 2.pdf"]), "source": "=== SLIDE 1 ===\n\n# Appunti L2\n\nmiei",
             "fmt": "markdown", "updated_at": "2025-01-01 10:00:00"},
            {"pdf_path": str(self.files["Lezione 2.pdf"]), "source": "vecchi", "fmt": "markdown",
             "updated_at": "2024-01-01 10:00:00"},
        ]
        converted: list[str] = []

        def convert(path, step):
            converted.append(path.name)
            step(0.5, "estraggo")
            return f"=== SLIDE 1 ===\n\n# Titolo {path.stem}\n\nTesto."

        progress: list[float] = []
        text, warnings = generate_quick_summary(
            "Analisi", self.materials, blocks, convert, progress=lambda f, m: progress.append(f)
        )
        self.assertEqual(converted, ["Lezione 1.pdf", "Lezione 10.pdf"])
        self.assertTrue(text.startswith("# Riassunto totale - Analisi"))
        self.assertLess(text.index("## L1"), text.index("## L2"))
        self.assertLess(text.index("## L2"), text.index("## L10"))
        self.assertIn("### Appunti L2", text)
        self.assertNotIn("vecchi", text)
        self.assertIn("<!-- Slide 1 -->", text)
        self.assertIn("### Titolo Lezione 1", text)
        self.assertEqual(len(warnings), 1)
        self.assertIn("slide.pptx", warnings[0])
        self.assertEqual(progress[-1], 1.0)
        self.assertEqual(QUICK_SUMMARY_NAME, "riassunto_rapido.md")

    def test_failures_and_cancel(self) -> None:
        def broken(path, step):
            raise RuntimeError("PDF illeggibile")

        with self.assertRaises(ExportError):
            generate_quick_summary("Analisi", self.materials[:1], [], broken)
        with self.assertRaises(InterruptedError):
            generate_quick_summary("Analisi", self.materials, [], broken, cancelled=lambda: True)
        with self.assertRaises(ExportError):
            generate_quick_summary("Analisi", self.materials[3:4], [], broken)


if __name__ == "__main__":
    unittest.main()
