"""Blocchi di codice, elenchi con le lettere, a capo ``//`` e rimozione dei marcatori."""

from __future__ import annotations

import unittest

from study_manager.services.pdf_tools.document import (
    BLOCK_CODE,
    BLOCK_LETTER,
    BLOCK_PARAGRAPH,
    RenderOptions,
    letter_label,
    letter_number,
    parse_markdown,
    strip_slide_markers,
)
from study_manager.services.pdf_tools.export import (
    latex_to_markdown,
    markdown_to_latex,
    markdown_to_txt,
    slides_to_html,
    slides_to_pandoc_markdown,
)
from study_manager.services.pdf_tools.inline import LineBreak, parse_inline, strip_inline_styles


OPTIONS = RenderOptions(include_preamble=False)


class LineBreakTests(unittest.TestCase):
    def test_double_slash_is_a_line_break(self):
        self.assertEqual(parse_inline("uno // due"), ["uno", LineBreak(), "due"])

    def test_double_backslash_is_a_line_break(self):
        self.assertEqual(parse_inline("uno \\\\ due"), ["uno", LineBreak(), "due"])

    def test_urls_math_and_mono_are_untouched(self):
        self.assertEqual(parse_inline("vedi https://esempio.it"), ["vedi https://esempio.it"])
        self.assertNotIn(LineBreak(), parse_inline("$a//b$"))
        mono = parse_inline("\\texttt{x // y}")[0]
        self.assertEqual(mono.children, ["x // y"])
        self.assertEqual(parse_inline("a///b"), ["a///b"])

    def test_exports(self):
        source = "Prima riga // seconda riga"
        self.assertIn("Prima riga\\\\ seconda riga", markdown_to_latex(source, OPTIONS))
        self.assertIn("Prima riga\nseconda riga", markdown_to_txt(source, OPTIONS))
        self.assertIn("Prima riga\\\nseconda riga", slides_to_pandoc_markdown(parse_markdown(source), OPTIONS))
        self.assertIn("<br>", slides_to_html(parse_markdown(source), OPTIONS))
        self.assertEqual(strip_inline_styles(source), "Prima riga\nseconda riga")

    def test_latex_round_trip_keeps_break(self):
        latex = markdown_to_latex("**a** // b", OPTIONS)
        self.assertIn("a** // b", latex_to_markdown(latex, OPTIONS))

    def test_line_break_stays_in_one_paragraph(self):
        blocks = parse_markdown("uno //\ndue")[0].blocks
        self.assertEqual([block.kind for block in blocks], [BLOCK_PARAGRAPH])


class LetterListTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual([letter_label(n) for n in (1, 2, 26, 27)], ["a", "b", "z", "aa"])
        self.assertEqual(letter_number("c"), 3)

    def test_parse_and_export(self):
        source = "a) alfa **grassetto**\nb) beta\n  a) annidata"
        blocks = parse_markdown(source)[0].blocks
        self.assertEqual([(b.kind, b.number, b.level) for b in blocks],
                         [(BLOCK_LETTER, 1, 0), (BLOCK_LETTER, 2, 0), (BLOCK_LETTER, 1, 1)])
        latex = markdown_to_latex(source, OPTIONS)
        self.assertIn("\\renewcommand{\\labelenumi}{\\alph{enumi})}", latex)
        self.assertIn("\\renewcommand{\\labelenumii}{\\alph{enumii})}", latex)
        self.assertIn("\\item alfa \\textbf{grassetto}", latex)
        self.assertEqual(latex_to_markdown(latex, OPTIONS).strip(), source)
        self.assertIn("b) beta", markdown_to_txt(source, OPTIONS))
        self.assertIn('<ol type="a"', slides_to_html(parse_markdown(source), OPTIONS))

    def test_plain_sentence_is_not_a_list(self):
        blocks = parse_markdown("e. poi il resto della frase")[0].blocks
        self.assertEqual(blocks[0].kind, BLOCK_PARAGRAPH)


class CodeBlockTests(unittest.TestCase):
    SOURCE = "=== SLIDE 1 ===\n\n```python\n# commento // non a capo\nprint('=== SLIDE 9 ===')\n\n    x = 1\n```\ndopo"

    def test_parse_keeps_code_verbatim(self):
        slides = parse_markdown(self.SOURCE)
        self.assertEqual(len(slides), 1)
        code = slides[0].blocks[0]
        self.assertEqual((code.kind, code.language), (BLOCK_CODE, "python"))
        self.assertEqual(code.text, "# commento // non a capo\nprint('=== SLIDE 9 ===')\n\n    x = 1")
        self.assertEqual(slides[0].blocks[1].text, "dopo")

    def test_exports(self):
        latex = markdown_to_latex(self.SOURCE, OPTIONS)
        self.assertIn("\\begin{verbatim}\n# commento // non a capo", latex)
        self.assertIn("    x = 1\n\\end{verbatim}", latex)
        back = latex_to_markdown(latex, OPTIONS)
        self.assertEqual(parse_markdown(back)[0].blocks[0].text, parse_markdown(self.SOURCE)[0].blocks[0].text)
        self.assertIn("```python\n# commento", slides_to_pandoc_markdown(parse_markdown(self.SOURCE), OPTIONS))
        self.assertIn("    print('=== SLIDE 9 ===')", markdown_to_txt(self.SOURCE, OPTIONS))
        self.assertIn('<pre class="code">', slides_to_html(parse_markdown(self.SOURCE), OPTIONS))


class SlideMarkerTests(unittest.TestCase):
    def test_strip_markers(self):
        source = "=== SLIDE 1 ===\n\nuno\n\n=== SLIDE 2 ===\n\n_(slide senza testo)_\n\n=== SLIDE 3 ===\n\ntre\n"
        text, removed = strip_slide_markers(source)
        self.assertEqual(removed, 3)
        self.assertEqual(text, "uno\n\ntre\n")

    def test_markers_inside_code_are_kept(self):
        text, removed = strip_slide_markers("```\n=== SLIDE 4 ===\n```\n")
        self.assertEqual(removed, 0)
        self.assertIn("=== SLIDE 4 ===", text)

    def test_unmarked_text_exports_without_marker(self):
        self.assertNotIn("SLIDE", markdown_to_txt("solo testo", OPTIONS))
        self.assertNotIn("Slide", markdown_to_latex("solo testo", OPTIONS))
        self.assertIn("=== SLIDE 2 ===", markdown_to_txt("=== SLIDE 2 ===\n\ntesto", OPTIONS))

    def test_latex_markers(self):
        text, removed = strip_slide_markers("% === Slide 1 ===\n\\section{A}\n")
        self.assertEqual((removed, text), (1, "\\section{A}\n"))


if __name__ == "__main__":
    unittest.main()
