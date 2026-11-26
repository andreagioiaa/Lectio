"""Suddivisione delle pagine in slide per il visualizzatore PDF integrato."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from study_manager.services.pdf_tools.viewer import (
    LAYOUT_AUTO,
    analyze_document,
    build_regions,
    region_for_page,
    select_slide_rects,
    sort_reading_order,
)

A4 = (595.0, 842.0)
TOP = (36.0, 14.0, 560.0, 407.0)
BOTTOM = (36.0, 435.0, 560.0, 828.0)


def _handout(path, pages: int, last_single: bool = False, frames: bool = True) -> str:
    """PDF A4 con due slide 4:3 per pagina, come gli handout Beamer."""
    document = fitz.open()
    for index in range(pages):
        page = document.new_page(width=A4[0], height=A4[1])
        slots = [TOP] if last_single and index == pages - 1 else [TOP, BOTTOM]
        for number, rect in enumerate(slots):
            if frames:
                page.draw_rect(fitz.Rect(rect), color=None, fill=(0.92, 0.92, 0.92))
                page.draw_rect(fitz.Rect(rect[0], rect[1], rect[2], rect[1] + 14), color=None, fill=(0.1, 0.1, 0.2))
            page.insert_text((rect[0] + 20, rect[1] + 60), f"Slide {index * 2 + number + 1}", fontsize=20)
    target = path / "handout.pdf"
    document.save(str(target))
    return str(target)


class SlideRegionTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_select_two_stacked_slides(self):
        rects = select_slide_rects([BOTTOM, TOP, (40, 20, 300, 30)], *A4)
        assert rects == [TOP, BOTTOM]


    def test_full_page_background_is_not_a_slide(self):
        assert select_slide_rects([(0, 0, 842, 595)], 842, 595) == []


    def test_overlapping_rects_are_rejected(self):
        assert select_slide_rects([TOP, (60, 200, 570, 600)], *A4) == []


    def test_inner_rects_are_ignored(self):
        inner = (60, 60, 540, 380)
        assert select_slide_rects([TOP, inner, BOTTOM], *A4) == [TOP, BOTTOM]


    def test_reading_order_rows_then_columns(self):
        rects = [(300, 400, 580, 600), (10, 400, 290, 600), (300, 10, 580, 210), (10, 10, 290, 210)]
        assert sort_reading_order(rects) == [rects[3], rects[2], rects[1], rects[0]]


    def test_handout_detected_one_slide_at_a_time(self):
        pages = analyze_document(_handout(self.tmp, 3))
        regions = build_regions(pages, LAYOUT_AUTO)
        assert len(regions) == 6
        assert [region.page for region in regions] == [0, 0, 1, 1, 2, 2]
        assert regions[0].rect[1] < regions[1].rect[1]
        assert not any(region.whole_page for region in regions)


    def test_last_page_with_single_slide(self):
        regions = build_regions(analyze_document(_handout(self.tmp, 3, last_single=True)))
        assert len(regions) == 5
        assert regions[-1].page == 2


    def test_manual_split_without_frames(self):
        pages = analyze_document(_handout(self.tmp, 2, frames=False))
        # Senza riquadri disegnati il rilevamento automatico mostra le pagine intere...
        assert all(region.whole_page for region in build_regions(pages))
        # ...ma "2 per pagina" le divide, restringendo ogni meta' al contenuto.
        regions = build_regions(pages, "2")
        assert len(regions) == 4
        assert regions[0].rect[3] < A4[1] / 2 + 1


    def test_regular_slides_stay_whole(self):
        document = fitz.open()
        for index in range(3):
            page = document.new_page(width=842, height=595)
            page.draw_rect(fitz.Rect(0, 0, 842, 595), color=None, fill=(1, 1, 1))
            page.insert_text((50, 80), f"Slide {index + 1}", fontsize=24)
        target = self.tmp / "slides.pdf"
        document.save(str(target))
        regions = build_regions(analyze_document(str(target)))
        assert len(regions) == 3
        assert all(region.whole_page for region in regions)


    def test_region_for_page(self):
        regions = build_regions(analyze_document(_handout(self.tmp, 3)))
        for page, expected in ((0, 0), (1, 2), (2, 4), (9, 5)):
            self.assertEqual(region_for_page(regions, page), expected)


if __name__ == "__main__":
    unittest.main()
