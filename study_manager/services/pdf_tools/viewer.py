"""Suddivisione delle pagine PDF in slide per il visualizzatore integrato.

Molte dispense di slide (handout Beamer, stampe "2/4 diapositive per pagina"
di PowerPoint) mettono piu' slide su una pagina A4. Il visualizzatore mostra
una slide alla volta: questo modulo trova i riquadri delle slide in ogni pagina
e produce l'elenco ``SlideRegion`` (pagina + area da ritagliare).

Rilevamento automatico:

- le slide sono rettangoli grandi (sfondi o cornici disegnate, oppure immagini)
  con proporzioni da presentazione (da 5:4 a circa 2:1), che non si
  sovrappongono tra loro;
- se la maggior parte delle pagine contiene almeno due riquadri, il documento
  e' considerato "n slide per pagina": le pagine senza riquadri riconoscibili
  usano la disposizione delle altre, scartando le aree vuote (es. l'ultima
  pagina con una sola slide).

In alternativa l'utente puo' forzare 1, 2, 4 o 6 slide per pagina: la pagina
viene divisa in parti uguali e ogni parte viene ristretta al suo contenuto.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable

LAYOUT_AUTO = "auto"
LAYOUT_CHOICES = ("auto", "1", "2", "4", "6")

# Limiti dei riquadri candidati, relativi alla pagina.
MIN_WIDTH_RATIO = 0.35
MIN_HEIGHT_RATIO = 0.16
MIN_ASPECT = 1.15
MAX_ASPECT = 2.2
MAX_REGIONS = 9
SAME_RECT_TOLERANCE = 4.0
CONTAINED_TOLERANCE = 3.0
REGION_PADDING = 2.0
CONTENT_PADDING = 6.0


@dataclass(frozen=True)
class SlideRegion:
    page: int  # indice della pagina (0-based)
    rect: tuple[float, float, float, float]  # x0, y0, x1, y1 in punti PDF
    whole_page: bool = False

    @property
    def width(self) -> float:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> float:
        return self.rect[3] - self.rect[1]


Rect = tuple[float, float, float, float]


def _width(rect: Rect) -> float:
    return rect[2] - rect[0]


def _height(rect: Rect) -> float:
    return rect[3] - rect[1]


def _area(rect: Rect) -> float:
    return max(0.0, _width(rect)) * max(0.0, _height(rect))


def _intersection(a: Rect, b: Rect) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _same(a: Rect, b: Rect, tolerance: float = SAME_RECT_TOLERANCE) -> bool:
    return all(abs(p - q) <= tolerance for p, q in zip(a, b))


def _contains(outer: Rect, inner: Rect, tolerance: float = CONTAINED_TOLERANCE) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[2] <= outer[2] + tolerance
        and inner[3] <= outer[3] + tolerance
    )


def _is_slide_shaped(rect: Rect, page_w: float, page_h: float) -> bool:
    w, h = _width(rect), _height(rect)
    if w <= 0 or h <= 0:
        return False
    if w < page_w * MIN_WIDTH_RATIO or h < page_h * MIN_HEIGHT_RATIO:
        return False
    aspect = w / h
    return MIN_ASPECT <= aspect <= MAX_ASPECT


def select_slide_rects(candidates: Iterable[Rect], page_w: float, page_h: float) -> list[Rect]:
    """Filtra i rettangoli candidati e restituisce i riquadri delle slide, in ordine di lettura.

    Restituisce i riquadri anche quando e' uno solo: e' il chiamante a decidere
    se la pagina va divisa.
    """
    page = (0.0, 0.0, page_w, page_h)
    shaped: list[Rect] = []
    for raw in candidates:
        rect = (
            max(0.0, float(raw[0])), max(0.0, float(raw[1])),
            min(page_w, float(raw[2])), min(page_h, float(raw[3])),
        )
        # Lo sfondo dell'intera pagina non e' un riquadro di slide.
        if _area(rect) >= _area(page) * 0.8:
            continue
        if _is_slide_shaped(rect, page_w, page_h) and not any(_same(rect, other) for other in shaped):
            shaped.append(rect)
    # Tiene solo i riquadri piu' esterni (una cornice contiene il suo sfondo, le immagini interne...).
    outer = [
        rect for rect in shaped
        if not any(other is not rect and _area(other) > _area(rect) and _contains(other, rect) for other in shaped)
    ]
    # Riquadri che si sovrappongono molto non sono slide affiancate: rinuncia.
    for index, a in enumerate(outer):
        for b in outer[index + 1:]:
            if _intersection(a, b) > 0.05 * min(_area(a), _area(b)):
                return []
    if len(outer) > MAX_REGIONS:
        return []
    return sort_reading_order(outer)


def sort_reading_order(rects: list[Rect]) -> list[Rect]:
    """Ordina per righe (dall'alto) e, nella stessa riga, da sinistra."""
    if not rects:
        return []
    ordered = sorted(rects, key=lambda r: (r[1], r[0]))
    rows: list[list[Rect]] = []
    for rect in ordered:
        if rows:
            reference = rows[-1][0]
            overlap = min(reference[3], rect[3]) - max(reference[1], rect[1])
            if overlap > 0.5 * min(_height(reference), _height(rect)):
                rows[-1].append(rect)
                continue
        rows.append([rect])
    return [rect for row in rows for rect in sorted(row, key=lambda r: r[0])]


def grid_for(count: int, page_w: float, page_h: float) -> tuple[int, int]:
    """Righe e colonne con cui dividere una pagina in ``count`` slide."""
    portrait = page_h >= page_w
    if count <= 1:
        return 1, 1
    if count == 2:
        return (2, 1) if portrait else (1, 2)
    if count == 4:
        return 2, 2
    if count == 6:
        return (3, 2) if portrait else (2, 3)
    return (count, 1) if portrait else (1, count)


def split_rects(count: int, page_w: float, page_h: float) -> list[Rect]:
    rows, cols = grid_for(count, page_w, page_h)
    cell_w, cell_h = page_w / cols, page_h / rows
    return [
        (col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h)
        for row in range(rows)
        for col in range(cols)
    ]


def _pad(rect: Rect, padding: float, page_w: float, page_h: float) -> Rect:
    return (
        max(0.0, rect[0] - padding), max(0.0, rect[1] - padding),
        min(page_w, rect[2] + padding), min(page_h, rect[3] + padding),
    )


# --------------------------------------------------------------------- PyMuPDF
@dataclass
class PageInfo:
    width: float
    height: float
    slides: list[Rect]  # riquadri rilevati (anche uno solo)
    content: list[Rect]  # blocchi di testo, immagini e disegni (per restringere le aree)


def _page_candidates(page) -> tuple[list[Rect], list[Rect]]:
    candidates: list[Rect] = []
    content: list[Rect] = []
    try:
        for drawing in page.get_drawings():
            rect = drawing.get("rect")
            if rect is None:
                continue
            value = (rect.x0, rect.y0, rect.x1, rect.y1)
            candidates.append(value)
            content.append(value)
    except Exception:
        pass
    try:
        for info in page.get_image_info():
            bbox = info.get("bbox")
            if bbox:
                value = tuple(float(v) for v in bbox)
                candidates.append(value)  # type: ignore[arg-type]
                content.append(value)  # type: ignore[arg-type]
    except Exception:
        pass
    try:
        for block in page.get_text("blocks"):
            if str(block[4]).strip():
                content.append((float(block[0]), float(block[1]), float(block[2]), float(block[3])))
    except Exception:
        pass
    return candidates, content


def analyze_page(page) -> PageInfo:
    rect = page.rect
    width, height = float(rect.width), float(rect.height)
    candidates, content = _page_candidates(page)
    # Le coordinate di get_drawings/get_text partono dall'angolo della pagina
    # (anche se il MediaBox non parte da 0): le normalizza.
    ox, oy = float(rect.x0), float(rect.y0)
    if ox or oy:
        candidates = [(a - ox, b - oy, c - ox, d - oy) for a, b, c, d in candidates]
        content = [(a - ox, b - oy, c - ox, d - oy) for a, b, c, d in content]
    return PageInfo(width, height, select_slide_rects(candidates, width, height), content)


def _has_content(rect: Rect, content: list[Rect]) -> bool:
    return any(_intersection(rect, item) > 0 for item in content if _area(item) < _area(rect) * 0.95)


def _shrink_to_content(rect: Rect, content: list[Rect], page_w: float, page_h: float) -> Rect | None:
    inside = []
    for item in content:
        clipped = (max(rect[0], item[0]), max(rect[1], item[1]), min(rect[2], item[2]), min(rect[3], item[3]))
        if _width(clipped) > 0.5 and _height(clipped) > 0.5:
            inside.append(clipped)
    if not inside:
        return None
    bounds = (
        min(r[0] for r in inside), min(r[1] for r in inside),
        max(r[2] for r in inside), max(r[3] for r in inside),
    )
    return _pad(bounds, CONTENT_PADDING, page_w, page_h)


def build_regions(pages: list[PageInfo], layout: str = LAYOUT_AUTO) -> list[SlideRegion]:
    """Elenco delle slide da mostrare, una per volta, nell'ordine del documento."""
    regions: list[SlideRegion] = []
    if layout != LAYOUT_AUTO:
        try:
            count = max(1, int(layout))
        except ValueError:
            count = 1
        for index, info in enumerate(pages):
            if count == 1:
                regions.append(SlideRegion(index, (0.0, 0.0, info.width, info.height), whole_page=True))
                continue
            found = False
            for cell in split_rects(count, info.width, info.height):
                shrunk = _shrink_to_content(cell, info.content, info.width, info.height)
                if shrunk is not None:
                    regions.append(SlideRegion(index, shrunk))
                    found = True
            if not found:
                regions.append(SlideRegion(index, (0.0, 0.0, info.width, info.height), whole_page=True))
        return regions

    multi = [info for info in pages if len(info.slides) >= 2]
    n_up = bool(pages) and len(multi) * 2 >= len(pages)
    template: list[Rect] = []
    if n_up:
        # Disposizione piu' frequente, usata per le pagine senza riquadri riconoscibili.
        counts = Counter(len(info.slides) for info in multi)
        typical = counts.most_common(1)[0][0]
        template = next(info.slides for info in multi if len(info.slides) == typical)
    for index, info in enumerate(pages):
        if n_up and info.slides:
            rects = info.slides
        elif n_up and template:
            rects = [rect for rect in template if _has_content(rect, info.content)]
        else:
            rects = []
        if not rects:
            regions.append(SlideRegion(index, (0.0, 0.0, info.width, info.height), whole_page=True))
            continue
        for rect in rects:
            regions.append(SlideRegion(index, _pad(rect, REGION_PADDING, info.width, info.height)))
    return regions


def analyze_document(
    path: str,
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[PageInfo]:
    """Analizza tutte le pagine (da eseguire in un thread: apre un proprio handle)."""
    import fitz  # PyMuPDF

    pages: list[PageInfo] = []
    with fitz.open(path) as document:
        total = len(document)
        for index in range(total):
            if cancel is not None and cancel():
                break
            pages.append(analyze_page(document[index]))
            if progress is not None:
                progress(index + 1, total)
    return pages


def render_region(document: Any, region: SlideRegion, scale: float) -> Any:
    """Pixmap PyMuPDF della slide a ``scale`` pixel per punto PDF."""
    import fitz  # PyMuPDF

    page = document[region.page]
    origin = page.rect
    x0, y0, x1, y1 = region.rect
    clip = fitz.Rect(origin.x0 + x0, origin.y0 + y0, origin.x0 + x1, origin.y0 + y1)
    return page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)


def region_for_page(regions: list[SlideRegion], page: int) -> int:
    """Indice della prima slide della pagina ``page`` (0-based), o della piu' vicina."""
    best = 0
    for index, region in enumerate(regions):
        if region.page == page:
            return index
        if region.page < page:
            best = index
    return best
