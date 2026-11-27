"""Test della v1.5 (fase 1): export Markdown, anno accademico, percorsi relativi, copia dei materiali."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from study_manager.database import Database
from study_manager.paths import StoredPaths
from study_manager.services.pdf_tools.document import RenderOptions
from study_manager.services.pdf_tools.export import (
    EXPORT_FORMATS,
    FORMAT_MD,
    SOURCE_LATEX,
    export_file,
    markdown_to_md,
    render_source,
)
from study_manager.services.subject_folders import SubjectFolders, files_size
from study_manager.services.subjects import (
    academic_year_choices,
    academic_year_folder,
    academic_year_for,
    normalize_academic_year,
)


SOURCE = """=== SLIDE 1 ===

# Limiti

Testo **grassetto** con \\underline{sottolineato} e \\textcolor{red}{rosso} // a capo.

- primo
  - annidato
1. numerato
a) lettera

$$
\\int_{0}^{1} x \\, dx
$$

=== SLIDE 2 ===

Formula $x^{2}$ e codice \\texttt{print()}.
"""


class MarkdownExportTests(unittest.TestCase):
    def test_md_is_a_listed_export_format(self) -> None:
        self.assertIn(FORMAT_MD, EXPORT_FORMATS)

    def test_markdown_uses_common_syntax(self) -> None:
        md = markdown_to_md(SOURCE, RenderOptions())
        self.assertIn("<!-- Slide 1 -->", md)
        self.assertIn("<!-- Slide 2 -->", md)
        self.assertNotIn("=== SLIDE", md)
        self.assertIn("# Limiti", md)
        self.assertIn("**grassetto**", md)
        self.assertIn("<u>sottolineato</u>", md)
        self.assertNotIn("textcolor", md)
        self.assertIn("rosso\\\na capo", md)
        self.assertIn("- primo\n    - annidato", md)
        self.assertIn("1. numerato", md)
        self.assertIn("- a) lettera", md)
        self.assertIn("$$\n\\int_{0}^{1} x \\, dx\n$$", md)
        self.assertIn("$x^{2}$", md)
        self.assertIn("`print()`", md)

    def test_markers_can_be_omitted(self) -> None:
        md = markdown_to_md(SOURCE, RenderOptions(include_markers=False))
        self.assertNotIn("<!--", md)

    def test_latex_source_becomes_markdown(self) -> None:
        md = render_source("\\section*{Prova}\n\nTesto $a$.", SOURCE_LATEX, FORMAT_MD, RenderOptions())
        self.assertIn("Prova", md)
        self.assertIn("$a$", md)
        self.assertNotIn("\\section", md)

    def test_export_file_writes_md_and_copies_images(self) -> None:
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as out:
            with mock.patch.dict(os.environ, {"LECTIO_DATA_DIR": data}):
                image = Path(data) / "pdf_images" / "cap-123abc" / "cap_imm1.png"
                image.parent.mkdir(parents=True)
                image.write_bytes(b"png")
                source = "=== SLIDE 1 ===\n\n![cap_imm1](cap-123abc/cap_imm1.png)\n"
                target = export_file(source, "markdown", FORMAT_MD, Path(out) / "cap.md", RenderOptions())
                text = target.read_text(encoding="utf-8")
                self.assertIn("![cap_imm1](img/cap-123abc/cap_imm1.png)", text)
                self.assertTrue((Path(out) / "img" / "cap-123abc" / "cap_imm1.png").is_file())


class AcademicYearTests(unittest.TestCase):
    def test_year_starts_in_september(self) -> None:
        self.assertEqual(academic_year_for(date(2025, 8, 31)), "2024/25")
        self.assertEqual(academic_year_for(date(2025, 9, 1)), "2025/26")
        self.assertEqual(academic_year_for(date(2099, 12, 1)), "2099/00")

    def test_normalize(self) -> None:
        for value in ("2025/26", "2025-2026", "2025/2026", "25/26", " 2025 - 26 "):
            self.assertEqual(normalize_academic_year(value), "2025/26", value)
        for value in ("", "Non indicato", "2025/27", "abc", "2025"):
            self.assertEqual(normalize_academic_year(value), "", value)
        self.assertEqual(academic_year_folder("2025/26"), "2025-26")

    def test_choices_include_used_years(self) -> None:
        choices = academic_year_choices(["2010/11"], around=date(2025, 10, 1))
        self.assertEqual(choices[0], "2026/27")
        self.assertIn("2025/26", choices)
        self.assertIn("2010/11", choices)


class StoredPathsTests(unittest.TestCase):
    def test_round_trip_and_outside_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "dati"
            subjects = data / "Materie"  # Materie dentro la cartella dati: vince la radice piu' lunga
            codec = StoredPaths({"materie": subjects, "dati": data})
            inside = subjects / "Analisi" / "Slide" / "l1.pdf"
            stored = codec.to_stored(inside)
            self.assertEqual(stored, "<materie>/Analisi/Slide/l1.pdf")
            self.assertEqual(Path(codec.to_real(stored)), inside)
            self.assertEqual(codec.to_stored(data / "transcripts" / "a.txt"), "<dati>/transcripts/a.txt")
            outside = Path(tmp) / "altro" / "x.pdf"
            self.assertEqual(codec.to_stored(outside), str(outside))
            self.assertEqual(codec.to_real(str(outside)), str(outside))
            self.assertEqual(codec.to_stored(stored), stored)

    def test_unknown_root_is_left_untouched(self) -> None:
        self.assertEqual(StoredPaths().to_real("<materie>/a.pdf"), "<materie>/a.pdf")


class DatabasePathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.subjects = self.base / "Materie"
        self.db = Database(self.base / "db.sqlite")
        self.db.set_path_roots(subjects=self.subjects, data=self.base / "dati")

    def tearDown(self) -> None:
        for connection in list(getattr(self.db, "_connections", [])):
            connection.close()
        self.tmp.cleanup()

    def _raw_path(self, material_id: int) -> str:
        row = self.db._connection().execute(
            "SELECT file_path FROM materials WHERE id = ?", (material_id,)
        ).fetchone()
        return str(row["file_path"])

    def test_materials_inside_subjects_are_stored_relative(self) -> None:
        path = self.subjects / "Analisi" / "Slide" / "l1.pdf"
        material_id = self.db.add_material("L1", str(path), "Analisi", 2, academic_year="2025-2026")
        self.assertEqual(self._raw_path(material_id), "<materie>/Analisi/Slide/l1.pdf")
        material = self.db.get_material(material_id)
        self.assertEqual(Path(material["file_path"]), path)
        self.assertEqual(material["academic_year"], "2025/26")
        self.assertEqual(self.db.find_material_by_path(str(path))["id"], material_id)
        self.assertEqual(self.db.list_academic_years(), ["2025/26"])

    def test_update_keeps_year_when_not_given(self) -> None:
        material_id = self.db.add_material("L1", "C:/x.pdf", "Analisi", 0, academic_year="2024/25")
        self.db.update_material(material_id, "L1 bis", "C:/x.pdf", "Analisi", 0, None)
        self.assertEqual(self.db.get_material(material_id)["academic_year"], "2024/25")
        self.db.update_material(material_id, "L1 bis", "C:/x.pdf", "Analisi", 0, None, academic_year="")
        self.assertEqual(self.db.get_material(material_id)["academic_year"], "")

    def test_relocate_after_rename_keeps_relative_form(self) -> None:
        path = self.subjects / "Analisi" / "Slide" / "l1.pdf"
        material_id = self.db.add_material("L1", str(path), "Analisi", 0)
        self.db.relocate_paths(self.subjects / "Analisi", self.subjects / "Analisi 1")
        self.assertEqual(self._raw_path(material_id), "<materie>/Analisi 1/Slide/l1.pdf")

    def test_existing_absolute_paths_are_converted(self) -> None:
        path = self.subjects / "Fisica" / "Slide" / "f.pdf"
        self.db.set_path_roots()
        material_id = self.db.add_material("F", str(path), "Fisica", 0)
        self.assertEqual(Path(self._raw_path(material_id)), path)
        self.db.set_path_roots(subjects=self.subjects, data=self.base / "dati")
        self.assertEqual(self.db.relativize_stored_paths(), 1)
        self.assertEqual(self._raw_path(material_id), "<materie>/Fisica/Slide/f.pdf")
        self.assertEqual(self.db.relativize_stored_paths(), 0)


class SchemaMigrationTests(unittest.TestCase):
    def test_old_materials_table_gets_academic_year(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.sqlite"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE materials (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                "file_path TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', "
                "target_hours REAL NOT NULL DEFAULT 0, logged_hours REAL NOT NULL DEFAULT 0, "
                "calendar_event_id INTEGER, created_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO materials(title, file_path, subject, created_at) VALUES ('A', '', 'X', '2025-01-01')"
            )
            connection.commit()
            connection.close()
            db = Database(path)
            try:
                material = db.list_materials()[0]
                self.assertEqual(material["academic_year"], "")
            finally:
                for conn in list(db._connections):
                    conn.close()


class CopyMaterialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.folders = SubjectFolders(self.base / "Materie")
        self.source = self.base / "Download" / "Lezione 1.pdf"
        self.source.parent.mkdir()
        self.source.write_bytes(b"pdf-1")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pdf_goes_to_slide_and_original_stays(self) -> None:
        copy = self.folders.copy_material(self.source, "Analisi")
        self.assertEqual(copy, self.base / "Materie" / "Analisi" / "Slide" / "Lezione 1.pdf")
        self.assertEqual(copy.read_bytes(), b"pdf-1")
        self.assertTrue(self.source.is_file())
        self.assertEqual(files_size([copy, self.base / "manca.pdf"]), 5)

    def test_identical_file_is_reused_and_different_one_gets_suffix(self) -> None:
        first = self.folders.copy_material(self.source, "Analisi")
        self.assertEqual(self.folders.copy_material(self.source, "Analisi"), first)
        other = self.base / "Altro" / "Lezione 1.pdf"
        other.parent.mkdir()
        other.write_bytes(b"pdf-diverso")
        second = self.folders.copy_material(other, "Analisi")
        self.assertEqual(second.name, "Lezione 1 (2).pdf")
        self.assertEqual(first.read_bytes(), b"pdf-1")

    def test_file_already_inside_is_not_copied(self) -> None:
        copy = self.folders.copy_material(self.source, "Analisi")
        self.assertEqual(self.folders.copy_material(copy, "Analisi"), copy)
        self.assertTrue(self.folders.is_in_subject(copy, "Analisi"))
        self.assertFalse(self.folders.is_in_subject(copy, "Fisica"))

    def test_other_documents_go_to_documenti(self) -> None:
        doc = self.base / "Download" / "esercizi.docx"
        doc.write_bytes(b"docx")
        copy = self.folders.copy_material(doc, "Analisi")
        self.assertEqual(copy.parent.name, "Documenti")

    def test_missing_source_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.folders.copy_material(self.base / "manca.pdf", "Analisi")


if __name__ == "__main__":
    unittest.main()
