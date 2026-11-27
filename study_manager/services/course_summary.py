"""Riassunto del corso e riassunto totale di una materia.

Due prodotti distinti:

* **Riassunto del corso** - l'utente aggiunge, da PDF e Slide, il proprio
  capitolo di appunti quando lo considera finito (o sovrascrive la versione
  precedente). Il riassunto e' la concatenazione dei capitoli, in ordine, con
  indice; si esporta in .md, .tex, .docx e PDF.
* **Riassunto totale** (``riassunto_rapido.md``) - generato su richiesta da
  tutte le slide della materia, in ordine naturale di nome file.

Il modulo non dipende dalla GUI: riceve righe del database e testi gia' pronti.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from .pdf_tools.document import FENCE_RE, HEADING_RE, RenderOptions, render_markdown, strip_slide_markers
from .pdf_tools.export import (
    FORMAT_DOCX,
    FORMAT_MD,
    FORMAT_TEX,
    SOURCE_LATEX,
    SOURCE_MARKDOWN,
    ExportError,
    compile_latex_pdf,
    copy_md_images,
    export_file,
    latex_to_markdown,
    markdown_to_latex,
    markdown_to_md,
)
from ..utils import atomic_write_text
from .subject_folders import SLIDE_EXTENSIONS

FORMAT_PDF = "pdf"
SUMMARY_FORMATS = (FORMAT_MD, FORMAT_TEX, FORMAT_DOCX, FORMAT_PDF)
SUMMARY_BASENAME = "Riassunto_corso"
QUICK_SUMMARY_NAME = "riassunto_rapido.md"


# ---------------------------------------------------------------------------
# Utilita' sul testo sorgente
# ---------------------------------------------------------------------------
def shift_headings(markdown: str, by: int, max_level: int = 6) -> str:
    """Abbassa i titoli ``#`` di ``by`` livelli (fuori dai blocchi di codice)."""
    if by <= 0:
        return markdown
    out: list[str] = []
    fence: str | None = None
    for line in markdown.split("\n"):
        match = FENCE_RE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not match.group(2):
                fence = None
            out.append(line)
            continue
        heading = HEADING_RE.match(line) if fence is None else None
        if heading:
            level = min(max_level, len(heading.group(1)) + by)
            out.append(f"{'#' * level} {heading.group(2)}")
        else:
            out.append(line)
    return "\n".join(out)


def chapter_markdown(source: str, fmt: str) -> str:
    """Testo del capitolo nel formato sorgente Markdown, senza marcatori di slide."""
    text = latex_to_markdown(source) if fmt == SOURCE_LATEX else str(source or "")
    text, _removed = strip_slide_markers(text)
    return text.strip()


def chapter_heading(position: int, title: str) -> str:
    return f"{int(position)}. {str(title or '').strip() or 'Capitolo'}"


# ---------------------------------------------------------------------------
# Riassunto del corso
# ---------------------------------------------------------------------------
def summary_title(subject: str) -> str:
    return f"Riassunto del corso - {str(subject or '').strip()}"


def build_summary_source(chapters: Iterable[dict[str, Any]], numbered_titles: bool = True) -> str:
    """Sorgente unico (formato dell'editor): ``# N. Titolo`` e i titoli del capitolo abbassati.

    ``numbered_titles=False`` lascia il numero a LaTeX (``\\section``), cosi' l'indice lo riporta.
    """
    parts: list[str] = []
    for chapter in chapters:
        body = shift_headings(chapter_markdown(chapter.get("source") or "", str(chapter.get("fmt") or "")), 1)
        title = str(chapter.get("title") or "").strip() or "Capitolo"
        if numbered_titles:
            title = chapter_heading(chapter.get("position") or len(parts) + 1, title)
        heading = f"# {title}"
        parts.append(heading + ("\n\n" + body if body else ""))
    return "\n\n".join(parts).strip() + "\n"


def summary_options(subject: str, latex: bool = False) -> RenderOptions:
    # In LaTeX/PDF numera LaTeX (l'indice mostra solo le sezioni numerate);
    # negli altri formati il numero e' gia' nel titolo del capitolo.
    return RenderOptions(
        include_markers=False,
        title=summary_title(subject),
        numbered_sections=latex,
        table_of_contents=True,
    )


def summary_markdown(subject: str, chapters: list[dict[str, Any]]) -> str:
    """Riassunto in Markdown comune, con titolo e indice dei capitoli."""
    source = build_summary_source(chapters)
    body = markdown_to_md(source, summary_options(subject))
    index = [f"{chapter_heading(row.get('position') or i, row.get('title') or '')}" for i, row in enumerate(chapters, 1)]
    head = [f"# {summary_title(subject)}", "", "## Indice", ""]
    head.extend(f"- {line}" for line in index)
    # Con il titolo del documento in testa, i capitoli scendono di un livello.
    return "\n".join(head) + "\n\n" + shift_headings(body, 1).strip() + "\n"


def export_summary(subject: str, chapters: list[dict[str, Any]], fmt: str, target: str | Path) -> Path:
    """Esporta il riassunto del corso nel formato richiesto (md, tex, docx o pdf)."""
    if not chapters:
        raise ExportError("Il riassunto non ha ancora capitoli.")
    target_path = Path(target)
    latex = fmt in (FORMAT_TEX, FORMAT_PDF)
    options = summary_options(subject, latex=latex)
    source = build_summary_source(chapters, numbered_titles=not latex)
    if fmt == FORMAT_MD:
        text = summary_markdown(subject, chapters)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target_path, text)
        copy_md_images(text, target_path.parent)
        return target_path
    if fmt in (FORMAT_TEX, FORMAT_DOCX):
        return export_file(source, SOURCE_MARKDOWN, fmt, target_path, options)
    if fmt == FORMAT_PDF:
        return compile_latex_pdf(markdown_to_latex(source, options, full=True), target_path)
    raise ExportError(f"Formato non supportato: {fmt}")


# ---------------------------------------------------------------------------
# Riassunto totale (riassunto_rapido.md)
# ---------------------------------------------------------------------------
def natural_key(text: str) -> list[Any]:
    """Chiave di ordinamento naturale: ``Lezione 2`` prima di ``Lezione 10``."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", str(text or ""))]


def slide_materials(materials: Iterable[dict[str, Any]], academic_year: str = "") -> list[dict[str, Any]]:
    """Materiali che sono slide (PDF/presentazioni), in ordine naturale di nome file."""
    rows = [
        row for row in materials
        if Path(str(row.get("file_path") or "")).suffix.lower() in SLIDE_EXTENSIONS
        and (not academic_year or str(row.get("academic_year") or "") == academic_year)
    ]
    return sorted(rows, key=lambda row: (natural_key(Path(str(row["file_path"])).name), int(row.get("id") or 0)))


# Stato di studio di una slide (colore del titolo nella pagina materia).
STATUS_TODO = "todo"            # mai aperta: rosso
STATUS_IN_PROGRESS = "progress"  # aperta / studiata / con appunti: arancione
STATUS_DONE = "done"            # completata dall'utente o nel riassunto del corso: verde


def material_status(
    material: dict[str, Any],
    summary_paths: set[str] | frozenset[str] = frozenset(),
    notes_paths: set[str] | frozenset[str] = frozenset(),
) -> str:
    """Stato della slide: ``done`` se completata o presente nel riassunto del corso,
    ``progress`` se aperta, con ore registrate o con appunti, altrimenti ``todo``.

    ``summary_paths`` / ``notes_paths`` sono percorsi PDF normalizzati
    (``Database.summary_pdf_paths`` / ``Database.note_block_pdf_paths``).
    """
    path = str(material.get("file_path") or "").strip()
    key = _path_key(path) if path else ""
    if str(material.get("completed_at") or "").strip() or (key and key in summary_paths):
        return STATUS_DONE
    try:
        logged = float(material.get("logged_hours") or 0)
    except (TypeError, ValueError):
        logged = 0.0
    if str(material.get("opened_at") or "").strip() or logged > 0 or (key and key in notes_paths):
        return STATUS_IN_PROGRESS
    return STATUS_TODO


def block_sources_by_pdf(blocks: Iterable[dict[str, Any]], resolve: Callable[[str], str] = lambda p: p) -> dict[str, dict[str, Any]]:
    """``{percorso PDF normalizzato: capitolo di appunti piu' recente}``."""
    found: dict[str, dict[str, Any]] = {}
    for block in blocks:
        pdf = str(block.get("pdf_path") or "").strip()
        if not pdf or not str(block.get("source") or "").strip():
            continue
        key = _path_key(resolve(pdf))
        current = found.get(key)
        if current is None or str(block.get("updated_at") or "") > str(current.get("updated_at") or ""):
            found[key] = block
    return found


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def quick_summary_markdown(subject: str, sections: list[tuple[str, str, str]]) -> str:
    """``sections`` = [(titolo materiale, sorgente, formato)] gia' in ordine."""
    lines = [f"# Riassunto totale - {str(subject or '').strip()}", ""]
    lines.append("## Indice")
    lines.append("")
    lines.extend(f"{index}. {title}" for index, (title, _s, _f) in enumerate(sections, 1))
    chunks = ["\n".join(lines)]
    for title, source, fmt in sections:
        text = latex_to_markdown(source) if fmt == SOURCE_LATEX else source
        body = markdown_to_md(text, RenderOptions(include_markers=True)).strip()
        chunks.append(f"## {title}\n\n" + shift_headings(body, 2))
    return "\n\n".join(chunks).strip() + "\n"


def generate_quick_summary(
    subject: str,
    materials: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    convert: Callable[[Path, Callable[[float, str], None]], str],
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    academic_year: str = "",
) -> tuple[str, list[str]]:
    """Costruisce ``riassunto_rapido.md``.

    Per ogni slide usa il capitolo di appunti piu' recente dello stesso PDF;
    se non c'e', ``convert(path, progress)`` trascrive il PDF (testo sorgente).
    Restituisce (markdown, avvisi).
    """
    report = progress or (lambda _f, _m: None)
    ordered = slide_materials(materials, academic_year)
    if not ordered:
        raise ExportError("Nessuna slide (PDF) collegata a questa materia.")
    by_pdf = block_sources_by_pdf(blocks)
    sections: list[tuple[str, str, str]] = []
    warnings: list[str] = []
    for index, material in enumerate(ordered):
        if cancelled():
            raise InterruptedError("Riassunto totale annullato.")
        path = Path(str(material["file_path"]))
        title = str(material.get("title") or path.stem)
        base = index / len(ordered)
        block = by_pdf.get(_path_key(str(path)))
        if block is not None:
            report(base, f"{title}: uso gli appunti di PDF e Slide")
            sections.append((title, str(block["source"]), str(block.get("fmt") or SOURCE_MARKDOWN)))
            continue
        if path.suffix.lower() != ".pdf":
            warnings.append(f"{path.name}: solo i PDF possono essere trascritti, saltato.")
            continue
        if not path.is_file():
            warnings.append(f"{path.name}: file non trovato, saltato.")
            continue

        def step(fraction: float, message: str, base=base) -> None:
            report(base + fraction / len(ordered), f"{title}: {message}")

        try:
            source = convert(path, step)
        except InterruptedError:
            raise
        except Exception as exc:  # un PDF illeggibile non blocca gli altri
            warnings.append(f"{path.name}: trascrizione non riuscita ({exc}).")
            continue
        sections.append((title, source, SOURCE_MARKDOWN))
    if not sections:
        raise ExportError("Nessuna slide trascritta:\n" + "\n".join(warnings[:8]))
    report(1.0, "Riassunto totale pronto")
    return quick_summary_markdown(subject, sections), warnings


def convert_pdf_source(path: Path, progress: Callable[[float, str], None], cancel_event=None, options=None) -> str:
    """Trascrive un PDF con il motore di PDF e Slide e restituisce il testo sorgente."""
    from .pdf_tools.extract import PdfCancelled, convert_pdf

    try:
        document = convert_pdf(path, options, progress=progress, cancel=cancel_event)
    except PdfCancelled as exc:
        raise InterruptedError(str(exc)) from exc
    return render_markdown(document)
