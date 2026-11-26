"""Corrispondenza anteprima -> sorgente per la modifica diretta nell'anteprima di PDF e Slide."""

from __future__ import annotations

import unittest

from study_manager.ui.preview_editing import PreviewSourceMap, escape_for_source, newline_for_source


class PreviewSourceMapTests(unittest.TestCase):
    def test_runs_are_found_in_order(self) -> None:
        source = "# Titolo\nLe **reti** e le reti.\n"
        mapping = PreviewSourceMap(source)
        ids = [mapping.locate(text) for text in ("Titolo", "Le ", "reti", " e le reti.")]
        self.assertNotIn(None, ids)
        spans = [source[start:end] for start, end in (mapping.runs[i] for i in ids)]
        self.assertEqual(spans, ["Titolo", "Le ", "reti", " e le reti."])

    def test_soft_line_break_matches_space(self) -> None:
        mapping = PreviewSourceMap("prima riga\nseconda")
        run = mapping.locate("prima riga seconda")
        self.assertEqual(mapping.runs[run], [0, 18])

    def test_missing_text_is_not_mappable(self) -> None:
        mapping = PreviewSourceMap("abc")
        self.assertIsNone(mapping.locate("1.2   Numero di sezione"))
        self.assertIsNone(mapping.locate(""))

    def test_insert_shifts_following_runs(self) -> None:
        source = "uno due tre"
        mapping = PreviewSourceMap(source)
        first, second = mapping.locate("uno"), mapping.locate(" due tre")
        mapping.replace(3, 3, "!", owner=first)
        self.assertEqual(mapping.source, "uno! due tre")
        self.assertEqual(mapping.runs[first], [0, 4])
        self.assertEqual(mapping.runs[second], [4, 12])

    def test_delete_across_runs(self) -> None:
        mapping = PreviewSourceMap("aa **bb** cc")
        a, b, c = mapping.locate("aa "), mapping.locate("bb"), mapping.locate(" cc")
        mapping.replace(2, 9, "", owner=a)  # da dopo "aa" a dopo "**bb**"
        self.assertEqual(mapping.source, "aa cc")
        self.assertEqual(mapping.runs[c], [2, 5])
        self.assertEqual(mapping.runs[b][0], mapping.runs[b][1])

    def test_latex_escapes_are_mapped(self) -> None:
        source = r"Il 50\% dei \textbf{pacchetti \& x}"
        mapping = PreviewSourceMap(source, "latex")
        run = mapping.locate("Il 50% dei ")
        self.assertTrue(mapping.escaped(run))
        # posizione 6 dell'anteprima (dopo "%") = dopo "\%" nel sorgente
        self.assertEqual(source[mapping.offset(run, 6):], r" dei \textbf{pacchetti \& x}")
        self.assertEqual(mapping.find(mapping.offset(run, 6)), (run, 6))

    def test_markdown_dollar_escape(self) -> None:
        source = "costa 5\\$ in tutto"
        mapping = PreviewSourceMap(source, "markdown")
        run = mapping.locate("costa 5$ in tutto")
        self.assertEqual(mapping.runs[run], [0, len(source)])


    def test_nearest_falls_back_to_closest_run(self) -> None:
        source = "# Titolo\n\nPrimo paragrafo.\n\n$$x^2$$\n\nSecondo."
        mapping = PreviewSourceMap(source)
        title = mapping.locate("Titolo")
        first = mapping.locate("Primo paragrafo.")
        second = mapping.locate("Secondo.")
        # Dentro un pezzo: come find().
        self.assertEqual(mapping.nearest(source.index("paragrafo")), (first, 6))
        # Sul marcatore "#": il titolo che segue e' piu' vicino.
        self.assertEqual(mapping.nearest(0), (title, 0))
        # Dentro la formula: il punto mappato piu' vicino.
        formula = source.index("x^2")
        self.assertIn(mapping.nearest(formula), {(first, len("Primo paragrafo.")), (second, 0)})
        self.assertEqual(mapping.nearest(source.index("$$x") + 1), (first, len("Primo paragrafo.")))
        self.assertIsNone(PreviewSourceMap("solo").nearest(0))


class SourceTextTests(unittest.TestCase):
    def test_escape(self) -> None:
        self.assertEqual(escape_for_source("50% & $x_1", "latex"), r"50\% \& \$x\_1")
        self.assertEqual(escape_for_source("5$", "markdown"), "5\\$")
        self.assertEqual(escape_for_source("a_b {x}", "latex", verbatim=True), "a_b {x}")

    def test_newline_continues_lists(self) -> None:
        self.assertEqual(newline_for_source("- uno", 5, "markdown"), "\n- ")
        self.assertEqual(newline_for_source("  3. tre", 8, "markdown"), "\n  4. ")
        self.assertEqual(newline_for_source("a) primo", 8, "markdown"), "\nb) ")
        self.assertEqual(newline_for_source("testo", 5, "markdown"), "\n\n")
        self.assertEqual(newline_for_source("  \\item voce", 12, "latex"), "\n  \\item ")
        self.assertEqual(newline_for_source("codice", 6, "markdown", verbatim=True), "\n")


if __name__ == "__main__":
    unittest.main()
