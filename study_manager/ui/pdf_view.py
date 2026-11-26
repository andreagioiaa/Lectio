"""Vista "PDF e Slide": trascrizione, editor con anteprima live ed esportazione.

Layout: a sinistra sorgente, motori, pulizia ed esportazione; a destra un
editor (Markdown + LaTeX oppure LaTeX puro) affiancato all'anteprima, che
mostra a scelta il rendering con le formule, il codice LaTeX finale o il TXT.

Tutta l'elaborazione pesante gira in un thread separato
(``PdfConversionController``); la vista legge gli eventi con ``after``.
"""

from __future__ import annotations

import logging
import os
import re
import json
import time
import tkinter as tk
import webbrowser
from dataclasses import dataclass, field
from idlelib.redirector import WidgetRedirector
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Callable

import customtkinter as ctk

from ..config import COLORS, FONT_FAMILY, MONO_FONT_FAMILY
from ..services.subject_folders import KIND_NOTES
from ..services.pdf_tools.controller import PdfConversionController, list_pdfs
from ..services.pdf_tools.inline import (
    STYLE_BOLD,
    STYLE_COLOR,
    STYLE_ITALIC,
    STYLE_MONO,
    STYLE_UNDERLINE,
    COLOR_CHOICES,
    LINE_BREAK_SOURCE,
    iter_runs,
    parse_inline,
    preview_color,
)
from ..services.pdf_tools.draft import default_draft_path, load_draft, save_draft
from ..services.pdf_tools.draft import delete_draft as delete_draft_file
from ..services.pdf_tools.document import (
    BLOCK_BULLET,
    BLOCK_CODE,
    BLOCK_HEADING,
    BLOCK_LETTER,
    BLOCK_MATH,
    BLOCK_NUMBER,
    BLOCK_TABLE,
    FENCE_RE,
    LATEX_SLIDE_MARKER_RE,
    SLIDE_MARKER_RE,
    RenderOptions,
    code_fence,
    letter_label,
    parse_markdown,
    show_marker,
    strip_slide_markers,
    unescape_text,
)
from ..services.pdf_tools.export import (
    EXPORT_FORMATS,
    FORMAT_DOCX,
    FORMAT_TEX,
    FORMAT_TXT,
    SOURCE_LATEX,
    SOURCE_MARKDOWN,
    SECTION_LEVELS,
    ExportError,
    SectionNumbering,
    apply_template,
    export_file,
    find_pandoc,
    heading_level_name,
    katex_available,
    latex_to_markdown,
    letter_label_command,
    markdown_to_latex,
    numbering_for,
    render_source,
    write_html_preview,
)
from ..services.pdf_tools.extract import (
    DEFAULT_MARKER_COMMAND,
    DEFAULT_NOUGAT_COMMAND,
    ENGINE_LABELS,
    ExtractOptions,
    PdfCancelled,
    backend_status,
)
from ..services.pdf_tools.text_removal import (
    RemovalRule,
    apply_rules,
    remove_text,
    rules_from_json,
    rules_to_json,
)
from .latex_structure_dialog import LEVEL_LABELS, LatexStructureDialog
from .note_block_dialog import NoteBlockDialog
from .preview_editing import PreviewSourceMap, escape_for_source, newline_for_source
from .text_shortcuts import install_text_shortcuts
from .math_render import MathRenderer
from .pdf_viewer import PdfViewer, open_pdf_window
from .remove_text_dialog import RemoveTextDialog
from .theme import font, mono, themed
from .widgets import Card, PageHeader, separator


LOGGER = logging.getLogger(__name__)

ENGINE_CHOICES = {"Automatico": "auto", "PyMuPDF": "pymupdf", "pdfplumber": "pdfplumber"}
OCR_CHOICES = {
    "Automatico (solo scansioni)": "auto",
    "Disattivato": "off",
    "Forza OCR su ogni pagina": "force",
}
OCR_ENGINE_CHOICES = {"Tesseract": "tesseract", "PaddleOCR": "paddle"}
MATH_CHOICES = {"Integrato (euristico)": "none", "Marker": "marker", "Nougat": "nougat"}
MATH_SCOPE_CHOICES = {"Solo pagine con formule": "math_pages", "Tutte le pagine": "all"}
BATCH_FORMAT_CHOICES = {".txt": [FORMAT_TXT], ".tex": [FORMAT_TEX], ".docx": [FORMAT_DOCX], "Tutti": list(EXPORT_FORMATS)}
PREVIEW_MODES = ("Anteprima", "LaTeX", "TXT")
SOURCE_MODES = ("Markdown", "LaTeX")
LAYOUT_MODES = ("Affiancati", "Sovrapposti")

SETTING_DEFAULTS = {
    "pdf_engine": "auto",
    "pdf_ocr": "auto",
    "pdf_ocr_engine": "tesseract",
    "pdf_ocr_lang": "ita+eng",
    "pdf_math_engine": "none",
    "pdf_math_scope": "math_pages",
    "pdf_marker_cmd": "",
    "pdf_nougat_cmd": "",
    "pdf_clean_headers": "1",
    "pdf_clean_pagenum": "1",
    "pdf_dehyphenate": "1",
    "pdf_detect_math": "1",
    "pdf_markers": "1",
    "pdf_headings": "1",
    "pdf_preamble": "1",
    "pdf_docx_pagebreak": "0",
    "pdf_layout": "Affiancati",
    "pdf_last_dir": "",
    "pdf_export_dir": "",
    "pdf_batch_format": ".docx",
    "pdf_remove_rules": "[]",
    "pdf_apply_remove_rules": "1",
    "pdf_latex_numbered": "1",
    "pdf_latex_embed": "0",
    "pdf_latex_level": "section",
    "pdf_latex_template": "",
    "pdf_panel_collapsed": "0",
    "pdf_subject": "",
    "pdf_editor_font": "12",
    "pdf_preview_zoom": "100",
    "pdf_show_source": "0",
    "pdf_show_editor": "1",
}

NO_SUBJECT = "Nessuna materia"

PREVIEW_RENDER_CHUNK = 18
HEADING_FONT_SIZES = {"h1": 19, "h2": 15, "h3": 13}
EDIT_DEBOUNCE_MS = 380
DRAFT_DEBOUNCE_MS = 1500
NOTE_DURATION_MS = 4000
EDITOR_FONT_RANGE = (8, 36)
PREVIEW_ZOOM_RANGE = (60, 300)
PREVIEW_ZOOM_STEP = 10

LIST_STYLE_NUMBER = "number"
LIST_STYLE_LETTER = "letter"
LIST_STYLE_BULLET = "bullet"
# Prefissi gia' presenti che "Crea elenco" sostituisce (- , 1. , a) , \item).
# Marcatore di slide nell'editor (Markdown o commento LaTeX): il numero e' la pagina del PDF.
_ANY_SLIDE_MARKER_RE = re.compile(r"^\s*%?\s*=+\s*SLIDE\s+(\d+)\s*=+\s*$", re.IGNORECASE)
SOURCE_SYNC_DEBOUNCE_MS = 250
# Attesa dopo una selezione prima di allineare l'altra vista (il trascinamento non la fa saltare).
SELECTION_FOLLOW_MS = 90
_LIST_PREFIX_RE = re.compile(r"^(\s*)(?:[-*+•]\s+|\d{1,3}[.)]\s+|[a-z]\)\s+|\\item\b\s*)?(.*)$")
_LATEX_LIST_SCAFFOLD_RE = re.compile(
    r"^\s*\\(?:begin|end)\{(?:itemize|enumerate)\}\s*$|^\s*\\renewcommand\{?\\labelenum"
)


@dataclass
class DocState:
    path: Path
    title: str
    source: str
    fmt: str = SOURCE_MARKDOWN
    warnings: list[str] = field(default_factory=list)
    engines: dict[str, int] = field(default_factory=dict)
    error: str = ""
    # Struttura LaTeX scelta all'importazione: numbered, embed, base_level, root_title
    latex: dict[str, Any] = field(default_factory=dict)
    # Capitolo di appunti collegato a una materia (id in ``note_blocks``) o None.
    block_id: int | None = None
    # Posizione del cursore nell'editor ("riga.colonna") e slide corrispondente (0-based).
    cursor: str = "1.0"
    page: int = 0
    # Materia/numero/titolo del capitolo, letti dal database (solo per le etichette).
    block_subject: str = ""
    block_position: int = 0
    block_title: str = ""
    # Ultimo contenuto scritto nel database, per non riscriverlo se non cambia.
    synced: tuple = ()


def _pretty_name(path: Path) -> str:
    """Nome leggibile proposto per la sezione radice (es. ``03_reti-ip`` -> ``03 reti ip``)."""
    name = path.stem.replace("_", " ").replace("-", " ")
    return " ".join(name.split()) or path.stem


def _label_for(choices: dict[str, str], value: str) -> str:
    for label, key in choices.items():
        if key == value:
            return label
    return next(iter(choices))


class PdfView(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.db = app.db
        self.controller = PdfConversionController()
        self.docs: list[DocState] = []
        self.current: int = -1
        self.sources: list[Path] = []
        self._poll_job: str | None = None
        self._edit_job: str | None = None
        self._render_job: str | None = None
        self._render_generation = 0
        self._suspend_modified = False
        self._task_label = ""
        self._task_done: Callable[[dict[str, Any]], None] | None = None
        self._renderer: MathRenderer | None = None
        self._preview_images: list[tk.PhotoImage] = []
        self._remove_dialog: RemoveTextDialog | None = None
        self._preview_numbering: SectionNumbering | None = None
        self._font_tags: set[str] = set()
        self._draft_job: str | None = None
        self._draft_saved_at = 0.0
        self._note_job: str | None = None
        try:
            self._draft_path = default_draft_path()
        except Exception:
            LOGGER.exception("Cartella della bozza PDF non disponibile.")
            self._draft_path = None

        settings = self._load_settings()
        self._build_vars(settings)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        PageHeader(
            self,
            "PDF e Slide",
            "Trascrivi slide e dispense in TXT, LaTeX e Word, con formule e anteprima modificabile. Tutto in locale.",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))

        self._build_controls()
        self._build_workspace()
        self._apply_panel_state()
        self._refresh_backends()
        self._update_buttons()
        self._show_placeholder()
        self._restore_draft()

    # ------------------------------------------------------------------ settings
    def _load_settings(self) -> dict[str, str]:
        try:
            stored = self.db.get_settings()
        except Exception:
            stored = {}
        return {key: str(stored.get(key, default)) for key, default in SETTING_DEFAULTS.items()}

    @staticmethod
    def _int_setting(value: str, default: int, bounds: tuple[int, int]) -> int:
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            number = default
        return max(bounds[0], min(bounds[1], number))

    @staticmethod
    def _bool(value: str) -> bool:
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _build_vars(self, settings: dict[str, str]) -> None:
        self.engine_var = ctk.StringVar(value=_label_for(ENGINE_CHOICES, settings["pdf_engine"]))
        self.ocr_var = ctk.StringVar(value=_label_for(OCR_CHOICES, settings["pdf_ocr"]))
        self.ocr_engine_var = ctk.StringVar(value=_label_for(OCR_ENGINE_CHOICES, settings["pdf_ocr_engine"]))
        self.ocr_lang_var = ctk.StringVar(value=settings["pdf_ocr_lang"] or "ita+eng")
        self.math_var = ctk.StringVar(value=_label_for(MATH_CHOICES, settings["pdf_math_engine"]))
        self.math_scope_var = ctk.StringVar(value=_label_for(MATH_SCOPE_CHOICES, settings["pdf_math_scope"]))
        self.marker_cmd_var = ctk.StringVar(value=settings["pdf_marker_cmd"])
        self.nougat_cmd_var = ctk.StringVar(value=settings["pdf_nougat_cmd"])
        self.clean_headers_var = ctk.BooleanVar(value=self._bool(settings["pdf_clean_headers"]))
        self.clean_pagenum_var = ctk.BooleanVar(value=self._bool(settings["pdf_clean_pagenum"]))
        self.dehyphenate_var = ctk.BooleanVar(value=self._bool(settings["pdf_dehyphenate"]))
        self.detect_math_var = ctk.BooleanVar(value=self._bool(settings["pdf_detect_math"]))
        self.markers_var = ctk.BooleanVar(value=self._bool(settings["pdf_markers"]))
        self.headings_var = ctk.BooleanVar(value=self._bool(settings["pdf_headings"]))
        self.preamble_var = ctk.BooleanVar(value=self._bool(settings["pdf_preamble"]))
        self.pagebreak_var = ctk.BooleanVar(value=self._bool(settings["pdf_docx_pagebreak"]))
        self.layout_var = ctk.StringVar(value=settings["pdf_layout"] if settings["pdf_layout"] in LAYOUT_MODES else LAYOUT_MODES[0])
        self.batch_format_var = ctk.StringVar(
            value=settings["pdf_batch_format"] if settings["pdf_batch_format"] in BATCH_FORMAT_CHOICES else ".docx"
        )
        self.recursive_var = ctk.BooleanVar(value=False)
        self.preview_mode_var = ctk.StringVar(value=PREVIEW_MODES[0])
        self.source_mode_var = ctk.StringVar(value=SOURCE_MODES[0])
        self.doc_var = ctk.StringVar(value="")
        self._last_dir = settings["pdf_last_dir"]
        self._export_dir = settings["pdf_export_dir"]
        self.removal_rules: list[RemovalRule] = rules_from_json(settings["pdf_remove_rules"])
        self.apply_rules_var = ctk.BooleanVar(value=self._bool(settings["pdf_apply_remove_rules"]))
        self._latex_defaults = {
            "numbered": self._bool(settings["pdf_latex_numbered"]),
            "embed": self._bool(settings["pdf_latex_embed"]),
            "base_level": settings["pdf_latex_level"] if settings["pdf_latex_level"] in LEVEL_LABELS else "section",
        }
        self._pending_latex: dict[str, Any] | None = None
        # Estrazione in corso: indice dell'evento -> indice del documento da
        # sostituire (-1 = nuovo documento accodato a quelli gia' aperti).
        self._import_targets: dict[int, int] = {}
        self._import_selected = False
        # Modifica diretta nell'anteprima (vedi preview_editing.py).
        self._preview_map: PreviewSourceMap | None = None
        self._preview_user = False
        self._preview_cursor_src: int | None = None
        self._preview_refresh_job: str | None = None
        self._preview_rendering = 0
        # Sincronizzazione editor <-> anteprima: l'altra vista segue solo modifiche
        # e selezioni, mai il semplice scorrimento.
        self._follow_editor = False  # il prossimo disegno porta in vista il cursore dell'editor
        self._render_anchor: tuple[int | None, int | None, int | None] | None = None  # (vista, y, cursore)
        self._follow_job: str | None = None
        self._preview_doc: DocState | None = None  # documento dell'ultimo disegno
        # A capo in attesa: (offset nel sorgente, testo da inserire prima del primo carattere).
        self._pending_break: tuple[int, str] | None = None
        self._template_path = settings["pdf_latex_template"]
        self._template_text = ""
        self._template_error = ""
        self._load_template_text()
        self._panel_collapsed = self._bool(settings["pdf_panel_collapsed"])
        self._show_source = self._bool(settings["pdf_show_source"])
        self._editor_visible = self._bool(settings["pdf_show_editor"])
        self._editor_font_size = self._int_setting(settings["pdf_editor_font"], 12, EDITOR_FONT_RANGE)
        self._preview_zoom = self._int_setting(settings["pdf_preview_zoom"], 100, PREVIEW_ZOOM_RANGE)
        self.subject_var = ctk.StringVar(value=settings["pdf_subject"] or NO_SUBJECT)

    def _save_settings(self, *_args) -> None:
        values = {
            "pdf_engine": ENGINE_CHOICES.get(self.engine_var.get(), "auto"),
            "pdf_ocr": OCR_CHOICES.get(self.ocr_var.get(), "auto"),
            "pdf_ocr_engine": OCR_ENGINE_CHOICES.get(self.ocr_engine_var.get(), "tesseract"),
            "pdf_ocr_lang": self.ocr_lang_var.get().strip() or "ita+eng",
            "pdf_math_engine": MATH_CHOICES.get(self.math_var.get(), "none"),
            "pdf_math_scope": MATH_SCOPE_CHOICES.get(self.math_scope_var.get(), "math_pages"),
            "pdf_marker_cmd": self.marker_cmd_var.get().strip(),
            "pdf_nougat_cmd": self.nougat_cmd_var.get().strip(),
            "pdf_clean_headers": "1" if self.clean_headers_var.get() else "0",
            "pdf_clean_pagenum": "1" if self.clean_pagenum_var.get() else "0",
            "pdf_dehyphenate": "1" if self.dehyphenate_var.get() else "0",
            "pdf_detect_math": "1" if self.detect_math_var.get() else "0",
            "pdf_markers": "1" if self.markers_var.get() else "0",
            "pdf_headings": "1" if self.headings_var.get() else "0",
            "pdf_preamble": "1" if self.preamble_var.get() else "0",
            "pdf_docx_pagebreak": "1" if self.pagebreak_var.get() else "0",
            "pdf_layout": self.layout_var.get(),
            "pdf_last_dir": self._last_dir,
            "pdf_export_dir": self._export_dir,
            "pdf_batch_format": self.batch_format_var.get(),
            "pdf_remove_rules": rules_to_json(self.removal_rules),
            "pdf_apply_remove_rules": "1" if self.apply_rules_var.get() else "0",
            "pdf_latex_numbered": "1" if self._latex_defaults["numbered"] else "0",
            "pdf_latex_embed": "1" if self._latex_defaults["embed"] else "0",
            "pdf_latex_level": self._latex_defaults["base_level"],
            "pdf_latex_template": self._template_path,
            "pdf_panel_collapsed": "1" if self._panel_collapsed else "0",
            "pdf_subject": self.selected_subject(),
            "pdf_editor_font": str(self._editor_font_size),
            "pdf_preview_zoom": str(self._preview_zoom),
            "pdf_show_source": "1" if self._show_source else "0",
            "pdf_show_editor": "1" if self._editor_visible else "0",
        }
        try:
            self.db.set_settings(values)
        except Exception:
            LOGGER.exception("Salvataggio impostazioni PDF non riuscito.")

    def extract_options(self) -> ExtractOptions:
        return ExtractOptions(
            engine=ENGINE_CHOICES.get(self.engine_var.get(), "auto"),
            ocr_mode=OCR_CHOICES.get(self.ocr_var.get(), "auto"),
            ocr_engine=OCR_ENGINE_CHOICES.get(self.ocr_engine_var.get(), "tesseract"),
            ocr_language=self.ocr_lang_var.get().strip() or "ita+eng",
            math_engine=MATH_CHOICES.get(self.math_var.get(), "none"),
            math_scope=MATH_SCOPE_CHOICES.get(self.math_scope_var.get(), "math_pages"),
            remove_headers=bool(self.clean_headers_var.get()),
            remove_page_numbers=bool(self.clean_pagenum_var.get()),
            dehyphenate=bool(self.dehyphenate_var.get()),
            detect_math=bool(self.detect_math_var.get()),
            marker_command=self.marker_cmd_var.get().strip(),
            nougat_command=self.nougat_cmd_var.get().strip(),
        )

    def render_options(self, doc: DocState | None = None) -> RenderOptions:
        latex = dict(self._latex_defaults)
        if doc is not None and doc.latex:
            latex.update(doc.latex)
        root_title = str(latex.get("root_title") or "")
        if not root_title and doc is not None:
            root_title = _pretty_name(doc.path)
        return RenderOptions(
            include_markers=bool(self.markers_var.get()),
            include_headings=bool(self.headings_var.get()),
            include_preamble=bool(self.preamble_var.get()),
            docx_page_breaks=bool(self.pagebreak_var.get()),
            title=(doc.title if doc is not None else ""),
            numbered_sections=bool(latex.get("numbered", True)),
            embed=bool(latex.get("embed", False)),
            base_level=str(latex.get("base_level") or "section"),
            root_title=root_title,
            template=self._template_text,
        )

    # ------------------------------------------------------------------ layout: controlli
    def _build_controls(self) -> None:
        panel = ctk.CTkScrollableFrame(
            self,
            width=318,
            fg_color="transparent",
            scrollbar_button_color=COLORS.border,
            scrollbar_button_hover_color=COLORS.surface_hover,
        )
        panel.grid(row=1, column=0, sticky="nsw", padx=(0, 12))
        panel.grid_columnconfigure(0, weight=1)
        self.controls = panel

        # Sorgente
        source = self._card(panel, 0, "Sorgente", "PDF o cartella (batch): i nuovi documenti si aggiungono a quelli aperti.")
        buttons = ctk.CTkFrame(source, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(0, 8))
        buttons.grid_columnconfigure((0, 1), weight=1)
        self._button(buttons, "Apri PDF…", self.choose_files).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._button(buttons, "Apri cartella…", self.choose_folder).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ctk.CTkCheckBox(
            source,
            text="Includi sottocartelle",
            variable=self.recursive_var,
            font=font(11),
            text_color=COLORS.text_muted,
            checkbox_width=18,
            checkbox_height=18,
            border_width=2,
            fg_color=COLORS.accent,
            hover_color=COLORS.accent_hover,
        ).pack(anchor="w", padx=16, pady=(0, 6))
        self.source_label = ctk.CTkLabel(
            source,
            text="Nessun file selezionato.",
            font=font(11),
            text_color=COLORS.text_faint,
            justify="left",
            anchor="w",
            wraplength=270,
        )
        self.source_label.pack(fill="x", padx=16, pady=(0, 10))
        self.extract_button = ctk.CTkButton(
            source, text="▶  Estrai testo", height=40, font=font(13, "bold"),
            fg_color=COLORS.accent, hover_color=COLORS.accent_hover, text_color="#04130F",
            corner_radius=10, command=self.start_extraction,
        )
        self.extract_button.pack(fill="x", padx=16, pady=(0, 8))
        self.progress = ctk.CTkProgressBar(source, height=8, progress_color=COLORS.accent, fg_color=COLORS.surface_alt)
        self.progress.pack(fill="x", padx=16, pady=(0, 6))
        self.progress.set(0)
        status_row = ctk.CTkFrame(source, fg_color="transparent")
        status_row.pack(fill="x", padx=16, pady=(0, 14))
        status_row.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(
            status_row, text="Pronto.", font=font(11), text_color=COLORS.text_muted,
            justify="left", anchor="w", wraplength=190,
        )
        self.status_label.grid(row=0, column=0, sticky="ew")
        self.cancel_button = self._button(status_row, "Annulla", self.cancel, danger=True)
        self.cancel_button.configure(width=74, height=28)
        self.cancel_button.grid(row=0, column=1, sticky="e", padx=(6, 0))

        # Motori
        engines = self._card(panel, 3, "Motori di trascrizione", "Tutto locale: nessuna API esterna.")
        self._option(engines, "Estrazione testo", self.engine_var, list(ENGINE_CHOICES))
        self._option(engines, "OCR (PDF scansionati)", self.ocr_var, list(OCR_CHOICES))
        row = ctk.CTkFrame(engines, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 8))
        row.grid_columnconfigure(0, weight=3)
        row.grid_columnconfigure(1, weight=2)
        self._small_label(row, "Motore OCR").grid(row=0, column=0, sticky="w")
        self._small_label(row, "Lingue").grid(row=0, column=1, sticky="w", padx=(8, 0))
        ctk.CTkOptionMenu(
            row, values=list(OCR_ENGINE_CHOICES), variable=self.ocr_engine_var, command=self._on_engine_change,
            **self._menu_style(),
        ).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        lang = ctk.CTkEntry(row, textvariable=self.ocr_lang_var, height=32, **self._entry_style())
        lang.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))
        lang.bind("<FocusOut>", self._save_settings)
        self._option(engines, "Formule matematiche", self.math_var, list(MATH_CHOICES))
        self._option(engines, "Ambito Marker/Nougat", self.math_scope_var, list(MATH_SCOPE_CHOICES))
        for label, variable, default in (
            ("Comando Marker", self.marker_cmd_var, DEFAULT_MARKER_COMMAND),
            ("Comando Nougat", self.nougat_cmd_var, DEFAULT_NOUGAT_COMMAND),
        ):
            self._small_label(engines, f"{label} (vuoto = predefinito)").pack(anchor="w", padx=16)
            entry = ctk.CTkEntry(engines, textvariable=variable, height=30, **self._entry_style(mono_font=True))
            entry.pack(fill="x", padx=16, pady=(4, 2))
            ctk.CTkLabel(
                engines, text=default, font=mono(9), text_color=COLORS.text_faint, anchor="w",
                justify="left", wraplength=270,
            ).pack(fill="x", padx=16, pady=(0, 6))
            entry.bind("<FocusOut>", lambda _e: (self._save_settings(), self._refresh_backends()))
        status_header = ctk.CTkFrame(engines, fg_color="transparent")
        status_header.pack(fill="x", padx=16, pady=(2, 2))
        self._small_label(status_header, "Stato dei motori").pack(side="left")
        ctk.CTkButton(
            status_header, text="Aggiorna", width=70, height=22, font=font(10, "bold"),
            fg_color="transparent", hover_color=COLORS.surface_hover, text_color=COLORS.accent,
            command=self._refresh_backends,
        ).pack(side="right")
        self.backend_frame = ctk.CTkFrame(engines, fg_color="transparent")
        self.backend_frame.pack(fill="x", padx=16, pady=(2, 14))

        # Pulizia
        cleanup = self._card(panel, 2, "Pulizia e struttura", "Applicata alla prossima estrazione.")
        for text, variable in (
            ("Rimuovi intestazioni e piè di pagina ripetuti", self.clean_headers_var),
            ("Rimuovi numeri di pagina isolati", self.clean_pagenum_var),
            ("Correggi le sillabazioni a fine riga", self.dehyphenate_var),
            ("Riconosci formule (LaTeX $…$ e $$…$$)", self.detect_math_var),
        ):
            self._switch(cleanup, text, variable, self._save_settings)
        self.rules_switch = ctk.CTkSwitch(
            cleanup, text="", variable=self.apply_rules_var, command=self._save_settings, font=font(11),
            text_color=COLORS.text_muted, progress_color=COLORS.accent, button_color=COLORS.text,
            button_hover_color="#FFFFFF", fg_color=COLORS.border, switch_width=34, switch_height=18,
        )
        self.rules_switch.pack(anchor="w", padx=16, pady=3)
        self._update_rules_label()
        manage = self._button(cleanup, "Rimuovi testo…  (Ctrl+H)", self.open_remove_dialog)
        manage.pack(fill="x", padx=16, pady=(8, 14))

        # Esportazione
        export = self._card(panel, 1, "Esportazione", "Le opzioni valgono per anteprima, copia ed esportazione.")

        self._small_label(export, "Materia").pack(anchor="w", padx=16)
        self.subject_combo = ctk.CTkComboBox(
            export, values=[NO_SUBJECT], variable=self.subject_var, command=self._on_subject_change,
            border_color=COLORS.border, **self._menu_style(),
        )
        self.subject_combo.pack(fill="x", padx=16, pady=(4, 2))
        self.subject_combo.bind("<FocusOut>", lambda _event: self._on_subject_change())
        self.subject_hint = ctk.CTkLabel(
            export, text="", font=font(10), text_color=COLORS.text_faint, anchor="w", justify="left", wraplength=270,
        )
        self.subject_hint.pack(fill="x", padx=16, pady=(0, 8))
        self._refresh_subjects()

        for text, variable in (
            ("Marcatori e numeri di slide", self.markers_var),
            ("Titoli delle slide (intestazioni)", self.headings_var),
            ("Preambolo LaTeX (documento compilabile)", self.preamble_var),
            ("Word: nuova pagina per ogni slide", self.pagebreak_var),
        ):
            self._switch(export, text, variable, self._on_render_option)

        self._small_label(export, "Struttura LaTeX").pack(anchor="w", padx=16, pady=(10, 2))
        self.structure_label = ctk.CTkLabel(
            export, text="", font=font(10), text_color=COLORS.text_faint, anchor="w", justify="left", wraplength=270,
        )
        self.structure_label.pack(fill="x", padx=16, pady=(0, 4))
        self.structure_button = self._button(export, "Struttura LaTeX…", self.edit_structure)
        self.structure_button.pack(fill="x", padx=16, pady=(0, 8))
        self._small_label(export, "Template del documento").pack(anchor="w", padx=16, pady=(2, 2))
        self.template_status = ctk.CTkLabel(
            export, text="", font=font(10), text_color=COLORS.text_faint, anchor="w", justify="left", wraplength=270,
        )
        self.template_status.pack(fill="x", padx=16, pady=(0, 4))
        template_row = ctk.CTkFrame(export, fg_color="transparent")
        template_row.pack(fill="x", padx=16)
        template_row.grid_columnconfigure((0, 1), weight=1)
        self._button(template_row, "Carica template…", self.choose_template).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        self.template_remove_button = self._button(template_row, "Rimuovi", self.clear_template)
        self.template_remove_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self._update_template_label()

        quick = ctk.CTkFrame(export, fg_color="transparent")
        quick.pack(fill="x", padx=16, pady=(10, 6))
        quick.grid_columnconfigure((0, 1), weight=1)
        self.copy_button = self._button(quick, "Copia LaTeX", self.copy_latex)
        self.copy_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.katex_button = self._button(quick, "Anteprima KaTeX", self.open_katex_preview)
        self.katex_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self._small_label(export, "Esporta il documento aperto").pack(anchor="w", padx=16, pady=(6, 4))
        formats = ctk.CTkFrame(export, fg_color="transparent")
        formats.pack(fill="x", padx=16)
        formats.grid_columnconfigure((0, 1, 2), weight=1)
        self.export_buttons: list[ctk.CTkButton] = []
        for column, fmt in enumerate(EXPORT_FORMATS):
            button = self._button(formats, f".{fmt}", lambda value=fmt: self.export_current(value))
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 3, 0 if column == 2 else 3))
            self.export_buttons.append(button)

        self._small_label(export, "Esporta tutti i documenti estratti").pack(anchor="w", padx=16, pady=(10, 4))
        batch = ctk.CTkFrame(export, fg_color="transparent")
        batch.pack(fill="x", padx=16, pady=(0, 6))
        batch.grid_columnconfigure(1, weight=1)
        ctk.CTkOptionMenu(
            batch, values=list(BATCH_FORMAT_CHOICES), variable=self.batch_format_var, width=86,
            command=lambda _v: self._save_settings(), **self._menu_style(),
        ).grid(row=0, column=0, sticky="w")
        self.batch_button = self._button(batch, "Esporta tutti…", self.export_all)
        self.batch_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        separator(export).pack(fill="x", padx=16, pady=(10, 8))
        self._small_label(export, "Salvataggio temporaneo").pack(anchor="w", padx=16, pady=(0, 2))
        self.draft_label = ctk.CTkLabel(
            export, text="Nessuna bozza salvata.", font=font(10), text_color=COLORS.text_faint,
            anchor="w", justify="left", wraplength=270,
        )
        self.draft_label.pack(fill="x", padx=16, pady=(0, 6))
        draft = ctk.CTkFrame(export, fg_color="transparent")
        draft.pack(fill="x", padx=16, pady=(0, 16))
        draft.grid_columnconfigure((0, 1), weight=1)
        self.draft_save_button = self._button(draft, "Salva ora", lambda: self._save_draft(notify=True))
        self.draft_save_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.draft_delete_button = self._button(draft, "Elimina", self.delete_draft, danger=True)
        self.draft_delete_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

    # ------------------------------------------------------------------ layout: area di lavoro
    def _build_workspace(self) -> None:
        workspace = Card(self)
        workspace.grid(row=1, column=1, sticky="nsew")
        self.workspace = workspace
        workspace.grid_columnconfigure(0, weight=1)
        workspace.grid_rowconfigure(2, weight=1)

        toolbar = ctk.CTkFrame(workspace, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        toolbar.grid_columnconfigure(6, weight=1)
        self.panel_button = self._button(toolbar, "◀  Opzioni", self.toggle_panel)
        self.panel_button.configure(height=28, width=104)
        self.panel_button.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self._small_label(toolbar, "Documento").grid(row=0, column=1, sticky="w", padx=(0, 6))
        self.doc_menu = ctk.CTkOptionMenu(
            toolbar, values=["—"], variable=self.doc_var, command=self._on_doc_selected,
            dynamic_resizing=False, width=220, **self._menu_style(),
        )
        self.doc_menu.grid(row=0, column=3, sticky="w", padx=4)
        self.prev_doc_button = self._button(toolbar, "‹", lambda: self.step_doc(-1))
        self.prev_doc_button.configure(height=28, width=30)
        self.prev_doc_button.grid(row=0, column=2, sticky="w")
        self.next_doc_button = self._button(toolbar, "›", lambda: self.step_doc(1))
        self.next_doc_button.configure(height=28, width=30)
        self.next_doc_button.grid(row=0, column=4, sticky="w")
        self.close_doc_button = self._button(toolbar, "✕", self.close_current_doc, danger=True)
        self.close_doc_button.configure(height=28, width=30)
        self.close_doc_button.grid(row=0, column=5, sticky="w", padx=(6, 0))
        # Stato compatto, visibile quando il pannello delle opzioni e' chiuso.
        # Stato compatto e conferme brevi (salvataggio, rimozioni, zoom) accanto al documento.
        self.mini_status = ctk.CTkLabel(
            toolbar, text="", font=font(10, "bold"), text_color=COLORS.text_faint, anchor="w",
        )
        self.mini_status.grid(row=0, column=6, sticky="ew", padx=(12, 8))
        self.source_button = self._button(toolbar, "📄  PDF originale", self.toggle_source_pane)
        self.source_button.configure(height=28, width=130)
        self.source_button.grid(row=0, column=8, sticky="e", padx=(0, 8))
        self.editor_toggle_button = self._button(toolbar, "", self.toggle_editor_pane)
        self.editor_toggle_button.configure(height=28, width=128)
        self.editor_toggle_button.grid(row=0, column=7, sticky="e", padx=(0, 8))
        self.layout_toggle = ctk.CTkSegmentedButton(
            toolbar, values=list(LAYOUT_MODES), variable=self.layout_var, command=self._on_layout_change,
            font=font(11), **self._segmented_style(),
        )
        self.layout_toggle.grid(row=0, column=9, sticky="e")

        modes = ctk.CTkFrame(workspace, fg_color="transparent")
        modes.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 6))
        modes.grid_columnconfigure(2, weight=1)
        self._small_label(modes, "Modifica in").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.source_toggle = ctk.CTkSegmentedButton(
            modes, values=list(SOURCE_MODES), variable=self.source_mode_var, command=self._on_source_mode,
            font=font(11), **self._segmented_style(),
        )
        self.source_toggle.grid(row=0, column=1, sticky="w")
        self.remove_button = self._button(modes, "✂  Rimuovi testo", self.open_remove_dialog)
        self.remove_button.configure(height=28, width=130)
        self.remove_button.grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.markers_button = self._button(modes, "Togli === SLIDE ===", self.remove_slide_markers)
        self.markers_button.configure(height=28, width=150)
        self.markers_button.grid(row=0, column=3, sticky="w", padx=(6, 0))
        chapter = ctk.CTkFrame(modes, fg_color="transparent")
        chapter.grid(row=0, column=4, sticky="w", padx=(14, 0))
        self.link_button = self._button(chapter, "📚  Collega a materia", self.link_current_doc)
        self.link_button.configure(height=28, width=150)
        self.link_button.pack(side="left")
        self.close_block_button = self._button(chapter, "✔  Chiudi blocco", self.close_current_block)
        self.close_block_button.configure(height=28, width=124)
        self.close_block_button.pack(side="left", padx=(6, 0))
        self.flashcard_button = self._button(chapter, "＋  Flashcard", self.create_chapter_flashcard)
        self.flashcard_button.configure(height=28, width=110)
        self.flashcard_button.pack(side="left", padx=(6, 0))
        modes.grid_columnconfigure(2, weight=0)
        modes.grid_columnconfigure(5, weight=1)
        self.preview_toggle = ctk.CTkSegmentedButton(
            modes, values=list(PREVIEW_MODES), variable=self.preview_mode_var,
            command=lambda _v: self.schedule_render(immediate=True), font=font(11), **self._segmented_style(),
        )
        self.preview_toggle.grid(row=0, column=6, sticky="e")
        self.info_label = ctk.CTkLabel(
            workspace, text="", font=font(10), text_color=COLORS.amber, anchor="w", justify="left", wraplength=760,
        )
        self.info_label.grid(row=3, column=0, sticky="ew", padx=16, pady=(4, 10))

        # Il PDF originale sta sempre a sinistra, qualunque sia la disposizione
        # di editor e anteprima: per questo c'e' un pannello esterno orizzontale.
        self.outer_paned = tk.PanedWindow(
            workspace, orient=tk.HORIZONTAL, bg=COLORS.surface, sashwidth=8, sashrelief=tk.FLAT, bd=0,
            opaqueresize=True,
        )
        self.outer_paned.grid(row=2, column=0, sticky="nsew", padx=12)
        self.paned = tk.PanedWindow(
            self.outer_paned,
            orient=tk.HORIZONTAL if self.layout_var.get() == LAYOUT_MODES[0] else tk.VERTICAL,
            bg=COLORS.surface, sashwidth=8, sashrelief=tk.FLAT, bd=0, opaqueresize=True,
        )
        self.source_viewer = PdfViewer(
            self.outer_paned, app=self.app, on_close=self.toggle_source_pane, on_detach=self.detach_source_pane,
            compact=True,
        )
        self._source_visible = False
        self._source_sync_job: str | None = None
        self._source_synced_page: int | None = None
        self.outer_paned.add(self.paned, minsize=440, stretch="always")

        editor_frame = ctk.CTkFrame(self.paned, fg_color=COLORS.surface_alt, corner_radius=10)
        editor_frame.grid_columnconfigure(0, weight=1)
        editor_frame.grid_rowconfigure(1, weight=1)
        self.editor_title = ctk.CTkLabel(
            editor_frame, text="Sorgente (Markdown + LaTeX)", font=font(10, "bold"), text_color=COLORS.text_faint,
            anchor="w",
        )
        self.editor_title.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 0))
        self.editor = ctk.CTkTextbox(
            editor_frame, font=mono(self._editor_font_size), wrap="word", fg_color=COLORS.surface_alt, text_color=COLORS.text,
            border_width=0, corner_radius=8, undo=True, maxundo=200, autoseparators=True,
        )
        self.editor.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2, 6))
        self.editor._textbox.bind("<<Modified>>", self._on_editor_modified)
        self.editor._textbox.configure(insertbackground=COLORS.accent, tabs=("1c",))
        for sequence in ("<Control-h>", "<Control-H>"):
            self.editor._textbox.bind(sequence, lambda _e: (self.open_remove_dialog(), "break")[1])
        for key, style in (("b", STYLE_BOLD), ("i", STYLE_ITALIC), ("u", STYLE_UNDERLINE), ("e", STYLE_MONO)):
            for sequence in (f"<Control-{key}>", f"<Control-{key.upper()}>"):
                self.editor._textbox.bind(sequence, lambda _e, value=style: (self.format_selection(value), "break")[1])
        for sequence in ("<Control-space>",):
            self.editor._textbox.bind(sequence, lambda _e: (self.clear_formatting(), "break")[1])
        self.editor._textbox.bind("<Button-3>", self._show_editor_menu)
        # Con il PDF originale aperto, spostare il cursore su una slide la mostra.
        for sequence in ("<ButtonRelease-1>", "<KeyRelease>"):
            self.editor._textbox.bind(sequence, self._schedule_source_sync, add="+")
        # Testo selezionato nell'editor: l'anteprima mostra lo stesso punto.
        self.editor._textbox.bind("<<Selection>>", lambda _e: self._schedule_follow("editor"), add="+")
        self._bind_zoom(self.editor._textbox, self.zoom_editor)

        preview_frame = ctk.CTkFrame(self.paned, fg_color=COLORS.surface_alt, corner_radius=10)
        preview_frame.grid_columnconfigure(0, weight=1)
        preview_frame.grid_rowconfigure(1, weight=1)
        self.preview_title = ctk.CTkLabel(
            preview_frame, text="Anteprima", font=font(10, "bold"), text_color=COLORS.text_faint, anchor="w",
        )
        self.preview_title.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 0))
        self.preview = tk.Text(
            preview_frame, wrap="word", bg=COLORS.surface_alt, fg=COLORS.text, relief=tk.FLAT,
            highlightthickness=0, bd=0, padx=14, pady=8, cursor="arrow",
            font=font(self._pz(12)), insertbackground=COLORS.surface_alt, spacing1=2, spacing3=2,
            selectbackground=COLORS.accent_dark,
        )
        self.preview.grid(row=1, column=0, sticky="nsew", padx=(4, 0), pady=(2, 6))
        scrollbar = ctk.CTkScrollbar(
            preview_frame, command=lambda *args: (self._on_preview_user_scroll(), self.preview.yview(*args)),
            button_color=COLORS.border,
            button_hover_color=COLORS.surface_hover,
        )
        scrollbar.grid(row=1, column=1, sticky="ns", pady=6)
        self.preview.configure(yscrollcommand=scrollbar.set)
        self._configure_preview_tags()
        install_text_shortcuts(self.preview)
        # Ogni modifica dell'anteprima passa da qui: viene riportata nel sorgente
        # oppure rifiutata (formule, numeri, marcatori, modalita' LaTeX/TXT).
        self._preview_redirect = WidgetRedirector(self.preview)
        self._preview_orig_insert = self._preview_redirect.register("insert", self._preview_insert)
        self._preview_orig_delete = self._preview_redirect.register("delete", self._preview_delete)
        self.preview.bind("<Key>", self._mark_preview_user_edit)
        for sequence in ("<Control-z>", "<Control-Z>"):
            self.preview.bind(sequence, lambda _e: (self._preview_undo(redo=False), "break")[1])
        for sequence in ("<Control-y>", "<Control-Y>", "<Control-Shift-z>", "<Control-Shift-Z>"):
            self.preview.bind(sequence, lambda _e: (self._preview_undo(redo=True), "break")[1])
        for key, style in (("b", STYLE_BOLD), ("i", STYLE_ITALIC), ("u", STYLE_UNDERLINE), ("e", STYLE_MONO)):
            for sequence in (f"<Control-{key}>", f"<Control-{key.upper()}>"):
                self.preview.bind(sequence, lambda _e, value=style: (self._apply_on_preview_selection(value), "break")[1])
        self.preview.bind("<Button-3>", self._show_preview_menu)
        # Testo selezionato nell'anteprima: l'editor mostra lo stesso punto del sorgente.
        self.preview.bind("<<Selection>>", lambda _e: self._schedule_follow("preview"), add="+")
        # Se l'utente scorre l'anteprima dopo aver scritto nell'editor, il disegno
        # successivo non la riporta indietro.
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.preview.bind(sequence, self._on_preview_user_scroll, add="+")
        self._bind_zoom(self.preview, self.zoom_preview)
        # Ctrl+S salva il documento aperto (bozza) ovunque si trovi il fuoco nella sezione.
        top = self.winfo_toplevel()
        for sequence in ("<Control-s>", "<Control-S>"):
            top.bind(sequence, self._on_ctrl_s, add="+")
        # Ctrl+PagSu / Ctrl+PagGiu passano al documento precedente / successivo.
        top.bind("<Control-Prior>", lambda _e: self._on_doc_shortcut(-1), add="+")
        top.bind("<Control-Next>", lambda _e: self._on_doc_shortcut(1), add="+")
        # I menu del tasto destro servono sia all'editor sia all'anteprima: vanno
        # costruiti dopo che entrambi i widget esistono.
        self._build_editor_menu()

        self.editor_frame = editor_frame
        self.preview_frame = preview_frame
        self.paned.add(editor_frame, minsize=220, stretch="always")
        self.paned.add(preview_frame, minsize=220, stretch="always")
        if not self._editor_visible:
            self.paned.forget(editor_frame)
        self._update_editor_toggle()
        if self._show_source:
            self.after(50, self._show_source_pane)

    def _pz(self, size: float) -> int:
        """Dimensione del carattere dell'anteprima con lo zoom corrente."""
        return max(6, int(round(size * self._preview_zoom / 100)))

    def _configure_preview_tags(self) -> None:
        text = self.preview
        pz = self._pz
        text.configure(font=font(pz(12)), fg=COLORS.text, bg=COLORS.surface_alt)
        text.tag_configure("marker", foreground=COLORS.accent, font=font(pz(10), "bold"), spacing1=14, spacing3=4)
        text.tag_configure("h1", font=font(pz(19), "bold"), spacing1=6, spacing3=6)
        text.tag_configure("h2", font=font(pz(15), "bold"), foreground=themed("#CBD5E1"), spacing1=6, spacing3=4)
        text.tag_configure("h3", font=font(pz(13), "bold"), spacing1=4, spacing3=3)
        text.tag_configure("para", spacing3=6)
        text.tag_configure("secnum", foreground=COLORS.accent)
        text.tag_configure("underline", underline=True)
        text.tag_configure("inline_code", background=COLORS.surface, foreground=themed("#F9A8D4"))
        text.tag_configure("bold", font=font(pz(12), "bold"))
        text.tag_configure("italic", font=font(pz(12), "normal"), foreground=themed("#E2E8F0"))
        for level in range(4):
            indent = 14 + level * 24
            text.tag_configure(f"list{level}", lmargin1=indent, lmargin2=indent + 18 + (6 if level else 0), spacing3=2)
        text.tag_configure("display", justify="center", spacing1=6, spacing3=8)
        text.tag_configure("mathcode", font=mono(pz(11)), foreground=COLORS.amber)
        text.tag_configure("code", font=mono(pz(11)), foreground=COLORS.text)
        text.tag_configure(
            "codeblock", font=mono(pz(11)), foreground=COLORS.text, background=COLORS.surface,
            lmargin1=18, lmargin2=18, rmargin=10, spacing1=0, spacing3=0,
        )
        text.tag_configure("codeblock_label", font=font(pz(9), "bold"), foreground=COLORS.text_faint,
                           lmargin1=18, spacing1=6, spacing3=2)
        text.tag_configure("table", font=mono(pz(11)), foreground=COLORS.text_muted, lmargin1=14, lmargin2=14)
        text.tag_configure("rule", foreground=COLORS.border, spacing1=4)
        text.tag_configure(
            "placeholder", foreground=COLORS.text_faint, font=font(pz(12)), justify="center", spacing1=60,
        )
        self._raise_overlay_tags()

    # ------------------------------------------------------------------ zoom
    def _bind_zoom(self, widget: tk.Text, handler: Callable[[int], None]) -> None:
        widget.bind("<Control-MouseWheel>", lambda event: (handler(1 if event.delta > 0 else -1), "break")[1])
        for sequence, step in (
            ("<Control-plus>", 1), ("<Control-equal>", 1), ("<Control-KP_Add>", 1),
            ("<Control-minus>", -1), ("<Control-KP_Subtract>", -1),
            ("<Control-0>", 0), ("<Control-KP_0>", 0),
        ):
            widget.bind(sequence, lambda _event, value=step: (handler(value), "break")[1])

    def zoom_editor(self, step: int) -> None:
        """Ingrandisce (+1), riduce (-1) o riporta a 12 pt (0) il testo dell'editor."""
        size = 12 if step == 0 else self._editor_font_size + step
        size = max(EDITOR_FONT_RANGE[0], min(EDITOR_FONT_RANGE[1], size))
        if size == self._editor_font_size and step:
            return
        self._editor_font_size = size
        self.editor.configure(font=mono(size))
        self._flash_note(f"Zoom editor: {size} pt  (Ctrl+rotella, Ctrl+0 per ripristinare)", COLORS.text_muted)
        self._save_settings()

    def zoom_preview(self, step: int) -> None:
        """Ingrandisce (+1), riduce (-1) o riporta al 100% (0) l'anteprima."""
        zoom = 100 if step == 0 else self._preview_zoom + step * PREVIEW_ZOOM_STEP
        zoom = max(PREVIEW_ZOOM_RANGE[0], min(PREVIEW_ZOOM_RANGE[1], zoom))
        if zoom == self._preview_zoom and step:
            return
        self._preview_zoom = zoom
        self._configure_preview_tags()
        self._flash_note(f"Zoom anteprima: {zoom}%  (Ctrl+rotella, Ctrl+0 per ripristinare)", COLORS.text_muted)
        self._save_settings()
        if self._current_doc() is None:
            self._show_placeholder()
        else:
            self.schedule_render(immediate=True)

    # ------------------------------------------------------------------ note brevi accanto al documento
    def _flash_note(self, message: str, color: str | None = None) -> None:
        """Conferma breve mostrata nella barra, accanto al menu del documento."""
        if self._note_job is not None:
            try:
                self.after_cancel(self._note_job)
            except (tk.TclError, ValueError):
                pass
        self.mini_status.configure(text=message[:120], text_color=color or COLORS.accent)
        self._note_job = self.after(NOTE_DURATION_MS, self._clear_note)

    def _clear_note(self) -> None:
        self._note_job = None
        text = self.status_label.cget("text")[:110] if self._panel_collapsed else ""
        self.mini_status.configure(text=text, text_color=COLORS.text_faint)

    # ------------------------------------------------------------------ helper widget
    def _card(self, parent, row: int, title: str, subtitle: str) -> Card:
        card = Card(parent)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(card, text=title, font=font(14, "bold"), text_color=COLORS.text, anchor="w").pack(
            fill="x", padx=16, pady=(14, 2 if subtitle else 10)
        )
        if subtitle:
            ctk.CTkLabel(
                card, text=subtitle, font=font(10), text_color=COLORS.text_faint, anchor="w",
                justify="left", wraplength=270,
            ).pack(fill="x", padx=16, pady=(0, 10))
        return card

    def _button(self, parent, text: str, command, danger: bool = False) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, height=32, font=font(11, "bold"), corner_radius=9,
            fg_color=COLORS.red_dark if danger else COLORS.surface_alt,
            hover_color="#991B1B" if danger else COLORS.surface_hover,
            text_color=COLORS.text, border_width=1, border_color=COLORS.border, command=command,
        )

    def _small_label(self, parent, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(parent, text=text, font=font(10, "bold"), text_color=COLORS.text_muted, anchor="w")

    @staticmethod
    def _menu_style() -> dict[str, Any]:
        return {
            "height": 32,
            "font": font(11),
            "dropdown_font": font(11),
            "fg_color": COLORS.surface_alt,
            "button_color": COLORS.surface_hover,
            "button_hover_color": COLORS.border,
            "text_color": COLORS.text,
            "dropdown_fg_color": COLORS.surface_alt,
            "dropdown_hover_color": COLORS.surface_hover,
            "dropdown_text_color": COLORS.text,
            "corner_radius": 8,
        }

    @staticmethod
    def _entry_style(mono_font: bool = False) -> dict[str, Any]:
        return {
            "fg_color": COLORS.surface_alt,
            "border_color": COLORS.border,
            "text_color": COLORS.text,
            "font": mono(11) if mono_font else font(11),
            "corner_radius": 8,
        }

    @staticmethod
    def _segmented_style() -> dict[str, Any]:
        return {
            "fg_color": COLORS.surface_alt,
            "selected_color": COLORS.accent_dark,
            "selected_hover_color": COLORS.accent_dark,
            "unselected_color": COLORS.surface_alt,
            "unselected_hover_color": COLORS.surface_hover,
            "text_color": COLORS.text,
            "height": 28,
        }

    def _option(self, parent, label: str, variable: ctk.StringVar, values: list[str]) -> None:
        self._small_label(parent, label).pack(anchor="w", padx=16)
        ctk.CTkOptionMenu(
            parent, values=values, variable=variable, command=self._on_engine_change, dynamic_resizing=False,
            **self._menu_style(),
        ).pack(fill="x", padx=16, pady=(4, 8))

    def _switch(self, parent, text: str, variable: ctk.BooleanVar, command) -> None:
        ctk.CTkSwitch(
            parent, text=text, variable=variable, command=command, font=font(11), text_color=COLORS.text_muted,
            progress_color=COLORS.accent, button_color=COLORS.text, button_hover_color="#FFFFFF",
            fg_color=COLORS.border, switch_width=34, switch_height=18,
        ).pack(anchor="w", padx=16, pady=3)

    # ------------------------------------------------------------------ eventi impostazioni
    def _on_engine_change(self, *_args) -> None:
        self._save_settings()
        self._refresh_backends()

    def _on_render_option(self) -> None:
        self._save_settings()
        self.schedule_render(immediate=True)

    def _on_layout_change(self, value: str) -> None:
        self.paned.configure(orient=tk.HORIZONTAL if value == LAYOUT_MODES[0] else tk.VERTICAL)
        self._save_settings()

    def _refresh_backends(self) -> None:
        for child in self.backend_frame.winfo_children():
            child.destroy()
        try:
            status = backend_status(self.extract_options())
        except Exception:
            LOGGER.exception("Verifica dei motori PDF non riuscita.")
            status = []
        pandoc = find_pandoc()
        status.append({"label": "Pandoc (Word)", "available": pandoc is not None,
                       "detail": "OMML nativo" if pandoc else "necessario per .docx"})
        status.append({"label": "KaTeX locale", "available": katex_available(),
                       "detail": "anteprima browser" if katex_available() else "assets/katex mancante"})
        for index, item in enumerate(status):
            row = ctk.CTkFrame(self.backend_frame, fg_color="transparent")
            row.grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 10), pady=1)
            ctk.CTkLabel(
                row, text="●", width=12, font=font(10),
                text_color=COLORS.green if item["available"] else COLORS.text_faint,
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=item["label"], font=font(10, "bold" if item["available"] else "normal"),
                text_color=COLORS.text_muted if item["available"] else COLORS.text_faint,
            ).pack(side="left", padx=(2, 0))
        self._update_buttons()

    # ------------------------------------------------------------------ sorgenti
    def _initial_dir(self) -> str:
        return self._last_dir if self._last_dir and Path(self._last_dir).is_dir() else str(Path.home())

    def choose_files(self) -> None:
        names = filedialog.askopenfilenames(
            parent=self, title="Scegli uno o piu' PDF", initialdir=self._initial_dir(),
            filetypes=[("PDF", "*.pdf"), ("Tutti i file", "*.*")],
        )
        if not names:
            return
        self.sources = [Path(name) for name in names]
        self._last_dir = str(self.sources[0].parent)
        self._save_settings()
        self._update_source_label()

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Cartella con i PDF", initialdir=self._initial_dir())
        if not folder:
            return
        pdfs = list_pdfs(folder, recursive=bool(self.recursive_var.get()))
        if not pdfs:
            messagebox.showinfo("PDF e Slide", "La cartella non contiene file PDF.", parent=self)
            return
        self.sources = pdfs
        self._last_dir = folder
        self._save_settings()
        self._update_source_label()

    def set_sources(self, paths: list[Path]) -> None:
        """Usato anche dai test e dallo smoke test."""
        self.sources = [Path(path) for path in paths]
        self._update_source_label()

    def _update_source_label(self) -> None:
        if not self.sources:
            self.source_label.configure(text="Nessun file selezionato.")
        elif len(self.sources) == 1:
            self.source_label.configure(text=f"{self.sources[0].name}\n{self.sources[0].parent}")
        else:
            names = ", ".join(path.name for path in self.sources[:3])
            more = f" e altri {len(self.sources) - 3}" if len(self.sources) > 3 else ""
            self.source_label.configure(text=f"{len(self.sources)} PDF: {names}{more}")
        self._update_buttons()

    # ------------------------------------------------------------------ estrazione
    def is_busy(self) -> bool:
        return self.controller.is_running()

    def start_extraction(self) -> None:
        if not self.sources:
            self.choose_files()
            if not self.sources:
                return
        plan = self._plan_import(self.sources)
        if plan is None:
            return
        sources, targets = plan
        structure = self._ask_structure_for_import()
        if structure is None:
            return
        self._pending_latex = structure
        self._save_settings()
        self._commit_editor()
        ok, error = self.controller.start(sources, self.extract_options())
        if not ok:
            messagebox.showwarning("PDF e Slide", error, parent=self)
            return
        self._import_targets = targets
        self._import_selected = False
        self._task_label = "extract"
        self.progress.set(0)
        self._set_status("Avvio estrazione…")
        self._refresh_doc_menu()
        self._update_buttons()
        self._start_polling()

    @staticmethod
    def _same_path(a: Path, b: Path) -> bool:
        try:
            return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
        except (OSError, ValueError):
            return False

    def _find_doc(self, path: Path) -> int:
        for index, doc in enumerate(self.docs):
            if self._same_path(doc.path, path):
                return index
        return -1

    def _plan_import(self, sources: list[Path]) -> tuple[list[Path], dict[int, int]] | None:
        """Decide quali PDF estrarre: i nuovi si aggiungono ai documenti aperti.

        Se alcuni PDF sono gia' aperti si chiede se sostituirli (perdendo le
        modifiche manuali) o lasciarli com'e'. ``None`` = niente da estrarre.
        """
        duplicates = [path for path in sources if self._find_doc(path) >= 0]
        replace = False
        if duplicates:
            names = "\n".join(f"• {path.name}" for path in duplicates[:6])
            if len(duplicates) > 6:
                names += f"\n… e altri {len(duplicates) - 6}"
            answer = messagebox.askyesnocancel(
                "PDF e Slide",
                f"Questi PDF sono gia' aperti:\n{names}\n\n"
                "Sì = estrai di nuovo e sostituisci il testo (le modifiche manuali andranno perse)\n"
                "No = lasciali come sono ed estrai solo gli altri",
                icon="warning",
                parent=self,
            )
            if answer is None:
                return None
            replace = bool(answer)
        chosen: list[Path] = []
        targets: dict[int, int] = {}
        for path in sources:
            existing = self._find_doc(path)
            if existing >= 0 and not replace:
                continue
            targets[len(chosen)] = existing
            chosen.append(path)
        if not chosen:
            if duplicates:
                self._select_doc(self._find_doc(duplicates[0]))
                self._set_status("I PDF scelti sono gia' aperti.")
            return None
        return chosen, targets

    def _place_imported(self, event_index: int, state: DocState) -> None:
        """Inserisce un documento estratto senza toccare gli altri documenti aperti."""
        target = self._import_targets.get(event_index, -1)
        if 0 <= target < len(self.docs):
            old = self.docs[target]
            # Il capitolo collegato resta lo stesso: cambia solo il testo.
            state.block_id, state.block_subject = old.block_id, old.block_subject
            state.block_position, state.block_title = old.block_position, old.block_title
            if target == self.current:
                # Il testo nell'editor appartiene al documento sostituito: va scartato.
                self._cancel_edit_job()
                self.current = -1
            self.docs[target] = state
            index = target
        else:
            self.docs.append(state)
            index = len(self.docs) - 1
        self._refresh_doc_menu()
        if not self._import_selected or self.current < 0:
            # Si apre il primo documento della nuova estrazione; i successivi
            # arrivano in background senza strappare l'utente da cio' che legge.
            self._import_selected = True
            self._select_doc(index)
        else:
            self._update_buttons()

    def _cancel_edit_job(self) -> None:
        if self._edit_job is not None:
            try:
                self.after_cancel(self._edit_job)
            except (tk.TclError, ValueError):
                pass
            self._edit_job = None

    def cancel(self) -> None:
        if self.controller.is_running():
            self.controller.cancel()
            self._set_status("Annullamento in corso…")

    def _start_polling(self) -> None:
        if self._poll_job is None:
            self._poll_job = self.after(80, self._poll_events)

    def _poll_events(self) -> None:
        self._poll_job = None
        processed = 0
        while processed < 200:
            try:
                event = self.controller.events.get_nowait()
            except Exception:
                break
            processed += 1
            try:
                self._handle_event(event)
            except Exception:
                LOGGER.exception("Gestione evento PDF non riuscita.")
        if self.controller.is_running() or processed:
            self._poll_job = self.after(80, self._poll_events)
        else:
            self._update_buttons()

    def _handle_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "progress":
            self.progress.set(float(event.get("fraction", 0.0)))
            self._set_status(str(event.get("message", "")))
        elif kind == "document":
            document = event["document"]
            source = event["markdown"]
            warnings = list(document.warnings)
            if self.apply_rules_var.get() and self.removal_rules:
                source, removed = apply_rules(source, self.removal_rules)
                if removed:
                    warnings.append(f"Regole di rimozione: {removed} occorrenze tolte")
            state = DocState(
                path=Path(event["path"]),
                title=document.title,
                source=source,
                warnings=warnings,
                engines=dict(document.engines),
                latex=dict(self._pending_latex or {}),
            )
            self._place_imported(int(event.get("index", -1)), state)
            self._schedule_draft_save()
        elif kind == "file_error":
            path = Path(event["path"])
            target = self._import_targets.get(int(event.get("index", -1)), -1)
            if 0 <= target < len(self.docs) and self.docs[target].source.strip():
                # Riestrazione fallita: si tiene il testo che c'era gia'.
                self._set_status(f"{path.name}: {event.get('error', '')} (testo precedente mantenuto)")
            else:
                self._place_imported(int(event.get("index", -1)), DocState(
                    path=path, title=path.stem, source="", error=str(event.get("error", "")),
                    latex=dict(self._pending_latex or {}),
                ))
        elif kind == "done":
            self.progress.set(1.0 if not event.get("cancelled") else self.progress.get())
            converted = int(event.get("converted", 0))
            failed = int(event.get("failed", 0))
            if event.get("cancelled"):
                message = f"Estrazione annullata ({converted} completati)."
            elif event.get("error"):
                message = f"Errore: {event['error']}"
            elif failed:
                message = f"Completato: {converted} PDF estratti, {failed} con errori."
            else:
                message = f"Completato: {converted} PDF estratti. Documenti aperti: {len(self.docs)}."
            self._set_status(message)
            self._task_label = ""
            self._import_targets = {}
            self._save_draft()
            self._update_buttons()
            if converted and not event.get("cancelled"):
                self.app.toast(message)
        elif kind == "task_done":
            callback = self._task_done
            self._task_done = None
            self._task_label = ""
            if event.get("cancelled"):
                self._set_status("Operazione annullata.")
            elif not event.get("ok"):
                self._set_status(f"Errore: {event.get('error')}")
                messagebox.showerror("PDF e Slide", str(event.get("error")), parent=self)
            elif callback is not None:
                callback(event)
            self._update_buttons()

    def _set_status(self, message: str) -> None:
        self.status_label.configure(text=message[:220])
        if hasattr(self, "mini_status") and self._note_job is None:
            self.mini_status.configure(text=message[:110] if self._panel_collapsed else "")

    # ------------------------------------------------------------------ pannello opzioni
    def toggle_panel(self) -> None:
        self._panel_collapsed = not self._panel_collapsed
        self._apply_panel_state()
        self._save_settings()

    def _apply_panel_state(self) -> None:
        """Mostra o nasconde il pannello delle opzioni; chiuso, editor e anteprima usano tutta la larghezza."""
        if self._panel_collapsed:
            self.controls.grid_remove()
            self.workspace.grid_configure(column=0, columnspan=2)
            self.panel_button.configure(text="▶  Opzioni")
            self.mini_status.configure(text=self.status_label.cget("text")[:110])
        else:
            self.workspace.grid_configure(column=1, columnspan=1)
            self.controls.grid()
            self.panel_button.configure(text="◀  Opzioni")
            self.mini_status.configure(text="")

    def _update_buttons(self) -> None:
        busy = self.controller.is_running()
        has_doc = 0 <= self.current < len(self.docs) and bool(self._current_source().strip())
        has_any = any(doc.source.strip() for doc in self.docs)
        self.extract_button.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")
        for button in (self.copy_button, self.katex_button, self.remove_button, self.markers_button,
                       *self.export_buttons):
            button.configure(state="normal" if has_doc and not busy else "disabled")
        if not katex_available():
            self.katex_button.configure(state="disabled")
        self.batch_button.configure(state="normal" if has_any and not busy else "disabled")
        if hasattr(self, "link_button"):
            doc = self._current_doc()
            linked = doc is not None and doc.block_id is not None
            self.link_button.configure(
                text="📚  Capitolo…" if linked else "📚  Collega a materia",
                state="normal" if doc is not None and not busy else "disabled",
            )
            self.close_block_button.configure(state="normal" if linked and not busy else "disabled")
            self.flashcard_button.configure(state="normal" if doc is not None and not busy else "disabled")
        if hasattr(self, "close_doc_button"):
            many = len(self.docs) > 1
            self.prev_doc_button.configure(state="normal" if many else "disabled")
            self.next_doc_button.configure(state="normal" if many else "disabled")
            self.close_doc_button.configure(
                state="normal" if self._current_doc() is not None and not busy else "disabled"
            )
        if hasattr(self, "structure_button"):
            self.structure_button.configure(state="normal" if self._current_doc() is not None and not busy else "disabled")
            self.template_remove_button.configure(state="normal" if self._template_path else "disabled")
            self._update_structure_label()
        if hasattr(self, "draft_delete_button"):
            has_draft = bool(self.docs) or self._draft_exists()
            self.draft_save_button.configure(state="normal" if has_any and not busy else "disabled")
            self.draft_delete_button.configure(state="normal" if has_draft and not busy else "disabled")

    # ------------------------------------------------------------------ documenti
    def _doc_label(self, index: int) -> str:
        doc = self.docs[index]
        prefix = "⚠ " if doc.error else ""
        if doc.block_id is not None and doc.block_title:
            # Capitolo: materia e numero del capitolo, senza la posizione nell'elenco.
            label = f"{prefix}📚 {doc.block_subject} · {doc.block_position}. {doc.block_title}"
            twins = sum(
                1 for other in self.docs
                if other.block_id is not None and (other.block_subject, other.block_position, other.block_title)
                == (doc.block_subject, doc.block_position, doc.block_title)
            )
            return label if twins == 1 else f"{label} ({index + 1})"
        return f"{index + 1}. {prefix}{doc.path.name}"

    def _refresh_doc_menu(self) -> None:
        labels = [self._doc_label(index) for index in range(len(self.docs))] or ["—"]
        self.doc_menu.configure(values=labels)
        if 0 <= self.current < len(self.docs):
            self.doc_var.set(self._doc_label(self.current))
        else:
            self.doc_var.set(labels[0])

    def _on_doc_selected(self, label: str) -> None:
        for index in range(len(self.docs)):
            if self._doc_label(index) == label:
                self._select_doc(index)
                return

    def _select_doc(self, index: int) -> None:
        self._commit_editor()
        if not (0 <= index < len(self.docs)):
            return
        self.current = index
        doc = self.docs[index]
        self.doc_var.set(self._doc_label(index))
        self.source_mode_var.set(SOURCE_MODES[1] if doc.fmt == SOURCE_LATEX else SOURCE_MODES[0])
        self._update_editor_title()
        self._set_editor_text(doc.source)
        self._restore_cursor(doc.cursor)
        if doc.block_id is not None and doc.block_subject and hasattr(self, "subject_combo"):
            # Gli export del capitolo vanno nella cartella Appunti della sua materia.
            if self.selected_subject().casefold() != doc.block_subject.casefold():
                self.subject_var.set(doc.block_subject)
                self._refresh_subjects()
        notes = []
        if doc.block_id is not None and doc.block_title:
            notes.append(f"Capitolo {doc.block_position}. {doc.block_title} di {doc.block_subject}")
        if doc.error:
            notes.append(f"Errore: {doc.error}")
        notes.extend(doc.warnings)
        if doc.engines:
            used = ", ".join(f"{ENGINE_LABELS.get(key, key)} ×{count}" for key, count in doc.engines.items())
            notes.append(f"Motori usati: {used}")
        self.info_label.configure(
            text="  ·  ".join(notes),
            text_color=COLORS.red if doc.error else (COLORS.amber if doc.warnings else COLORS.text_faint),
        )
        self.schedule_render(immediate=True)
        self._update_buttons()
        self._schedule_draft_save()
        self._load_source_pdf()
        if self._remove_dialog is not None and self._remove_dialog.winfo_exists():
            self._remove_dialog.after(10, self._remove_dialog._refresh_count)

    def step_doc(self, delta: int) -> None:
        """Passa al documento precedente/successivo (le modifiche restano in memoria e nella bozza)."""
        if len(self.docs) < 2:
            return
        start = self.current if self.current >= 0 else 0
        self._select_doc((start + delta) % len(self.docs))

    def _on_doc_shortcut(self, delta: int):
        if not self.app.is_view_visible(self):
            return None
        self.step_doc(delta)
        return "break"

    def close_current_doc(self) -> None:
        """Chiude il documento aperto; gli altri restano aperti e nella bozza."""
        doc = self._current_doc()
        if doc is None or self.controller.is_running():
            return
        if doc.block_id is not None:
            self.close_current_block()
            return
        self._commit_editor()
        if doc.source.strip() and not messagebox.askyesno(
            "PDF e Slide",
            f"Chiudere «{doc.path.name}»?\n\nIl testo e le modifiche di questo documento verranno tolti "
            "dalla bozza. Gli altri documenti aperti e i file gia' esportati non vengono toccati.",
            icon="warning",
            parent=self,
        ):
            return
        self._remove_current_doc()
        self._set_status(f"Chiuso: {doc.path.name}")

    def _remove_current_doc(self) -> None:
        """Toglie il documento corrente dall'elenco e passa al vicino (o allo stato vuoto)."""
        self._cancel_edit_job()
        index = self.current
        del self.docs[index]
        self.current = -1
        if self.docs:
            self._refresh_doc_menu()
            self._select_doc(min(index, len(self.docs) - 1))
            self._save_draft()
        else:
            self._set_editor_text("")
            self.source_mode_var.set(SOURCE_MODES[0])
            self._update_editor_title()
            self.info_label.configure(text="")
            self._refresh_doc_menu()
            self._show_placeholder()
            self._load_source_pdf()
            self._cancel_draft_job()
            try:
                if self._draft_path is not None:
                    delete_draft_file(self._draft_path)
            except Exception:
                LOGGER.exception("Eliminazione bozza PDF non riuscita.")
            self._draft_saved_at = 0.0
            self._update_draft_label()
            self._update_buttons()

    # ------------------------------------------------------------------ capitoli (blocchi di appunti)
    def _block_meta(self, doc: DocState) -> str:
        return json.dumps(
            {"warnings": list(doc.warnings), "engines": dict(doc.engines), "latex": dict(doc.latex),
             "title": doc.title},
            ensure_ascii=False,
        )

    def _sync_block(self, doc: DocState, status: str | None = None) -> bool:
        """Scrive nel database testo, formato, slide e cursore del capitolo collegato."""
        if doc.block_id is None:
            return False
        payload = {
            "pdf_path": str(doc.path), "source": doc.source, "fmt": doc.fmt, "meta": self._block_meta(doc),
            "page": doc.page, "cursor": doc.cursor,
        }
        signature = tuple(payload.values())
        if status is None and signature == doc.synced:
            return True
        if status is not None:
            payload["status"] = status
        try:
            self.db.update_note_block(doc.block_id, **payload)
        except Exception:
            LOGGER.exception("Salvataggio del capitolo di appunti non riuscito.")
            return False
        doc.synced = signature
        return True

    def _apply_block_row(self, doc: DocState, row: dict[str, Any]) -> None:
        doc.block_id = int(row["id"])
        doc.block_subject = str(row.get("subject") or "")
        doc.block_position = int(row.get("position") or 0)
        doc.block_title = str(row.get("title") or "")

    def _refresh_subject_pages(self) -> None:
        for key in ("subjects", "flashcards"):
            view = getattr(self.app, "views", {}).get(key) if hasattr(self.app, "views") else None
            if view is not None and hasattr(view, "refresh_if_visible"):
                try:
                    view.refresh_if_visible()
                except Exception:
                    LOGGER.exception("Aggiornamento della vista '%s' non riuscito.", key)

    def link_current_doc(self) -> bool:
        """Collega il documento a una materia come capitolo (o ne modifica numero/titolo/materia)."""
        doc = self._current_doc()
        if doc is None or self.controller.is_running():
            return False
        self._commit_editor()
        try:
            subjects = self.db.list_subject_tags()
        except Exception:
            subjects = []
        editing = doc.block_id is not None
        if editing:
            subject, position, title = doc.block_subject, doc.block_position, doc.block_title
        else:
            subject = self.selected_subject() or (subjects[0] if subjects else "")
            position = self.db.next_note_block_position(subject) if subject else 1
            title = doc.title.strip() or _pretty_name(doc.path)
        result = NoteBlockDialog(
            self, subjects, subject, position, title, editing=editing,
            next_position=self.db.next_note_block_position,
        ).show()
        if result is None:
            return False
        try:
            if editing:
                self.db.update_note_block(doc.block_id, **result)
                row = self.db.get_note_block(doc.block_id)
                if row is None:
                    raise ValueError("Il capitolo non esiste piu' nel database.")
            else:
                block_id = self.db.add_note_block(
                    result["subject"], result["title"], result["position"], pdf_path=str(doc.path),
                    source=doc.source, fmt=doc.fmt, meta=self._block_meta(doc), page=doc.page, cursor=doc.cursor,
                )
                row = self.db.get_note_block(block_id)
        except Exception as exc:
            LOGGER.exception("Collegamento del capitolo non riuscito.")
            messagebox.showerror("PDF e Slide", f"Collegamento non riuscito:\n{exc}", parent=self)
            return False
        self._apply_block_row(doc, row)
        self._sync_block(doc)
        self.app.subject_folder(doc.block_subject)
        self._refresh_doc_menu()
        self._select_doc(self.current)
        self._save_draft()
        self._refresh_subject_pages()
        label = f"{doc.block_position}. {doc.block_title}"
        self.app.toast(f"Capitolo «{label}» salvato in {doc.block_subject}")
        return True

    def close_current_block(self) -> None:
        """Salva il capitolo (testo, slide e cursore) nella materia e lo toglie dai documenti aperti."""
        doc = self._current_doc()
        if doc is None or doc.block_id is None or self.controller.is_running():
            return
        self._commit_editor()
        if not self._sync_block(doc, status="closed"):
            messagebox.showerror(
                "PDF e Slide", "Salvataggio del capitolo non riuscito: dettagli in studyflow.log.", parent=self
            )
            return
        self._remove_current_doc()
        self._save_draft()
        self._refresh_subject_pages()
        slide = f", slide {doc.page + 1}" if doc.page else ""
        message = f"Capitolo {doc.block_position}. {doc.block_title} salvato in {doc.block_subject}{slide}"
        self._set_status(message)
        self.app.toast(message)

    def open_block(self, block_id: int) -> bool:
        """Riapre un capitolo salvato (dalla pagina della materia) con testo, slide e cursore."""
        for index, doc in enumerate(self.docs):
            if doc.block_id == int(block_id):
                self._select_doc(index)
                return True
        row = self.db.get_note_block(int(block_id))
        if row is None:
            messagebox.showerror("PDF e Slide", "Il capitolo non esiste piu'.", parent=self)
            return False
        try:
            meta = json.loads(row.get("meta") or "{}")
        except ValueError:
            meta = {}
        meta = meta if isinstance(meta, dict) else {}
        path = Path(str(row.get("pdf_path") or "")) if row.get("pdf_path") else Path(f"{row['title']}.pdf")
        doc = DocState(
            path=path,
            title=str(meta.get("title") or row.get("title") or path.stem),
            source=str(row.get("source") or ""),
            fmt=SOURCE_LATEX if row.get("fmt") == SOURCE_LATEX else SOURCE_MARKDOWN,
            warnings=[str(item) for item in meta.get("warnings", []) if item] if isinstance(meta.get("warnings"), list) else [],
            engines={str(k): int(v) for k, v in meta.get("engines", {}).items()} if isinstance(meta.get("engines"), dict) else {},
            latex=dict(meta["latex"]) if isinstance(meta.get("latex"), dict) else {},
            cursor=str(row.get("cursor") or "1.0"),
            page=int(row.get("page") or 0),
        )
        self._apply_block_row(doc, row)
        self._commit_editor()
        self.docs.append(doc)
        self._sync_block(doc, status="open")
        self._refresh_doc_menu()
        self._select_doc(len(self.docs) - 1)
        self._save_draft()
        self._refresh_subject_pages()
        self._set_status(f"Capitolo riaperto: {doc.block_position}. {doc.block_title}")
        return True

    def forget_block(self, block_id: int) -> None:
        """Il capitolo e' stato eliminato dalla materia: il documento aperto resta, scollegato."""
        for doc in self.docs:
            if doc.block_id == int(block_id):
                doc.block_id = None
                doc.block_subject = doc.block_title = ""
                doc.block_position = 0
                doc.synced = ()
        self._refresh_doc_menu()
        self._update_buttons()
        self._schedule_draft_save()

    def focus_item(self, block_id: int | None = None, subject: str | None = None) -> None:
        """Usato dalla pagina Materie: apre un capitolo o prepara un nuovo collegamento."""
        if subject:
            self.subject_var.set(subject)
            self._refresh_subjects()
            self._save_settings()
        if block_id is not None:
            self.open_block(int(block_id))

    def _editor_selection(self) -> str:
        for widget in (self.editor._textbox, self.preview):
            try:
                text = widget.get("sel.first", "sel.last").strip()
            except tk.TclError:
                continue
            if text:
                return text
        return ""

    def create_chapter_flashcard(self) -> None:
        """Nuova flashcard collegata al capitolo aperto (il testo selezionato diventa la domanda)."""
        doc = self._current_doc()
        if doc is None or self.controller.is_running():
            return
        selection = self._editor_selection()
        if doc.block_id is None:
            if not messagebox.askyesno(
                "PDF e Slide",
                "Per collegare la flashcard a un capitolo, il documento deve prima essere collegato "
                "a una materia.\n\nCollegarlo ora?",
                parent=self,
            ) or not self.link_current_doc():
                return
            doc = self._current_doc()
            if doc is None or doc.block_id is None:
                return
        self._commit_editor()
        from .flashcard_view import FlashcardDialog

        chapter = f"{doc.block_position}. {doc.block_title}"
        slide = f" · slide {doc.page + 1}" if doc.page or "SLIDE" in doc.source[:200].upper() else ""
        block_id = doc.block_id
        subject = doc.block_subject

        def saved(card_id: int) -> None:
            self.app.sync_flashcard_export(subject)
            self._refresh_subject_pages()
            self._flash_note(f"✓ Flashcard aggiunta al capitolo {chapter}")

        FlashcardDialog(
            self, None,
            prefill={"subject": subject, "front": selection, "notes": f"Capitolo {chapter}{slide}",
                     "note_block_id": block_id, "chapter": chapter},
            on_saved=saved,
        )

    def _current_doc(self) -> DocState | None:
        return self.docs[self.current] if 0 <= self.current < len(self.docs) else None

    def _current_source(self) -> str:
        doc = self._current_doc()
        return doc.source if doc is not None else ""

    def _update_editor_title(self) -> None:
        self._update_editor_toggle()
        doc = self._current_doc()
        latex = doc is not None and doc.fmt == SOURCE_LATEX
        self.editor_title.configure(text="Sorgente LaTeX (corpo del documento)" if latex else "Sorgente (Markdown + LaTeX)")

    # ------------------------------------------------------------------ editor
    def _set_editor_text(self, text: str) -> None:
        self._suspend_modified = True
        widget = self.editor._textbox
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.edit_reset()
        widget.edit_modified(False)
        self._suspend_modified = False

    def _editor_text(self) -> str:
        return self.editor._textbox.get("1.0", "end-1c")

    def _on_editor_modified(self, _event=None) -> None:
        widget = self.editor._textbox
        if not widget.edit_modified():
            return
        widget.edit_modified(False)
        if self._suspend_modified:
            return
        # Modifica scritta nell'editor: il prossimo disegno segue il cursore.
        self._follow_editor = True
        if self._edit_job is not None:
            self.after_cancel(self._edit_job)
        self._edit_job = self.after(EDIT_DEBOUNCE_MS, self._after_edit)

    def _after_edit(self) -> None:
        self._edit_job = None
        self._commit_editor()
        self.schedule_render()
        self._update_buttons()
        self._schedule_draft_save()

    def _commit_editor(self) -> None:
        doc = self._current_doc()
        if doc is not None:
            doc.source = self._editor_text()
            try:
                doc.cursor = self.editor._textbox.index("insert")
                doc.page = self._cursor_slide_page() or 0
            except tk.TclError:
                pass

    def _restore_cursor(self, cursor: str) -> None:
        widget = self.editor._textbox
        try:
            widget.mark_set("insert", cursor or "1.0")
            widget.see("insert")
        except tk.TclError:
            widget.mark_set("insert", "1.0")

    def _on_source_mode(self, value: str) -> None:
        doc = self._current_doc()
        if doc is None:
            self.source_mode_var.set(SOURCE_MODES[0])
            return
        self._commit_editor()
        target = SOURCE_LATEX if value == SOURCE_MODES[1] else SOURCE_MARKDOWN
        if target == doc.fmt:
            return
        if target == SOURCE_LATEX:
            options = self.render_options(doc)
            doc.source = markdown_to_latex(doc.source, options, full=False, root=False)
        else:
            if not messagebox.askyesno(
                "PDF e Slide",
                "Il codice LaTeX verra' riconvertito in Markdown: i comandi non riconosciuti "
                "restano come testo. Continuare?",
                parent=self,
            ):
                self.source_mode_var.set(SOURCE_MODES[1])
                return
            doc.source = latex_to_markdown(doc.source, self.render_options(doc))
        doc.fmt = target
        self._update_editor_title()
        self._set_editor_text(doc.source)
        self.schedule_render(immediate=True)
        self._schedule_draft_save()

    # ------------------------------------------------------------------ anteprima
    def schedule_render(self, immediate: bool = False) -> None:
        if self._render_job is not None:
            try:
                self.after_cancel(self._render_job)
            except (tk.TclError, ValueError):
                pass
        self._render_job = self.after(1 if immediate else 60, self.render_preview)

    def _show_placeholder(self) -> None:
        self._clear_preview()
        self.preview.insert(
            "end",
            "Scegli un PDF e premi «Estrai testo».\n\nIl testo estratto comparira' nell'editor: ogni modifica "
            "aggiorna questa anteprima e finisce nei file esportati.",
            "placeholder",
        )

    def _clear_preview(self) -> None:
        self._render_generation += 1
        self.preview.delete("1.0", "end")
        self._preview_images = []

    def render_preview(self, sync: bool = False) -> None:
        self._render_job = None
        self._cancel_preview_refresh()
        self._pending_break = None
        doc = self._current_doc()
        if doc is None:
            self._preview_map = None
            self._render_anchor = None
            self._set_preview_editable(False)
            self._show_placeholder()
            return
        mode = self.preview_mode_var.get()
        options = self.render_options(doc)
        position = self.preview.yview()[0]
        if self.focus_get() is self.preview and self._preview_cursor_src is None:
            self._preview_cursor_src = self._preview_source_offset("insert", prefer_before=True)
        if mode == PREVIEW_MODES[0] and doc.source.strip():
            self._render_anchor = self._capture_render_anchor(doc)
        else:
            self._render_anchor = None
            self._follow_editor = False
        self._preview_map = None
        self._preview_doc = doc
        self._set_preview_editable(mode == PREVIEW_MODES[0])
        self._clear_preview()
        if not doc.source.strip():
            self.preview.insert("end", doc.error or "Nessun testo estratto.", "placeholder")
            return
        if mode == "LaTeX":
            self.preview_title.configure(text="LaTeX generato (sola lettura)")
            self.preview.insert("end", render_source(doc.source, doc.fmt, FORMAT_TEX, options), "code")
            self.preview.yview_moveto(position)
            return
        if mode == "TXT":
            self.preview_title.configure(text="Testo .txt (sola lettura)")
            self.preview.insert("end", render_source(doc.source, doc.fmt, FORMAT_TXT, options), "code")
            self.preview.yview_moveto(position)
            return
        numbering = numbering_for(options)
        start = self._numbering_start_note(options, numbering)
        self.preview_title.configure(
            text="Anteprima modificabile, con formule e numerazione LaTeX" + (f" ({start})" if start else "")
            if options.numbered_sections else "Anteprima modificabile, con formule (sezioni non numerate)"
        )
        self._preview_numbering = numbering
        self._preview_map = PreviewSourceMap(doc.source, doc.fmt)
        markdown = latex_to_markdown(doc.source, options) if doc.fmt == SOURCE_LATEX else doc.source
        slides = parse_markdown(markdown)
        operations = list(self._preview_operations(slides, options))
        generation = self._render_generation
        self._render_chunk(operations, 0, generation, position, sync)

    # ------------------------------------------------------------------ modifica nell'anteprima
    def _set_preview_editable(self, editable: bool) -> None:
        self.preview.configure(
            insertbackground=COLORS.accent if editable else COLORS.surface_alt,
            cursor="xterm" if editable else "arrow",
        )

    def _preview_editable(self) -> bool:
        return (
            self._preview_map is not None
            and self._current_doc() is not None
            and self.preview_mode_var.get() == PREVIEW_MODES[0]
            and not self.controller.is_running()
        )

    def _mark_preview_user_edit(self, event) -> None:
        """Solo i tasti che scrivono o cancellano contano come modifica dell'utente."""
        control = bool(event.state & 0x4)
        if control and event.keysym not in ("Delete", "BackSpace", "v", "V", "x", "X"):
            return
        self._preview_user = True
        self.after_idle(self._clear_preview_user)

    def _clear_preview_user(self) -> None:
        self._preview_user = False

    def _preview_run_at(self, index: str) -> tuple[int, str] | None:
        """``(run_id, inizio del pezzo)`` per il carattere in ``index``."""
        try:
            for tag in self.preview.tag_names(index):
                if tag.startswith("src") and tag[3:].isdigit():
                    found = self.preview.tag_prevrange(tag, f"{index}+1c")
                    if found:
                        return int(tag[3:]), str(found[0])
        except tk.TclError:
            return None
        return None

    def _preview_source_offset(self, index: str, prefer_before: bool) -> int | None:
        """Offset nel sorgente della posizione ``index`` dell'anteprima (None = non modificabile)."""
        if self._preview_map is None:
            return None
        try:
            index = self.preview.index(index)
        except tk.TclError:
            return None
        sides = ("before", "after") if prefer_before else ("after", "before")
        for side in sides:
            if side == "before":
                if self.preview.compare(index, "==", "1.0"):
                    continue
                probe = self.preview.index(f"{index}-1c")
            else:
                probe = index
            found = self._preview_run_at(probe)
            if found is None:
                continue
            run_id, start = found
            counted = self.preview.count(start, index, "chars")
            if isinstance(counted, tuple):
                counted = counted[0]
            position = int(counted or 0)
            offset = self._preview_map.offset(run_id, position)
            if offset is not None:
                return offset
        return None

    def _preview_owner_run(self, index: str, prefer_before: bool) -> int | None:
        sides = (f"{index}-1c", index) if prefer_before else (index, f"{index}-1c")
        for probe in sides:
            try:
                if probe.endswith("-1c") and self.preview.compare(index, "==", "1.0"):
                    continue
            except tk.TclError:
                continue
            found = self._preview_run_at(probe)
            if found is not None:
                return found[0]
        return None

    def _preview_insert(self, index, chars, *tags):
        if not self._preview_user or self._preview_rendering:
            return self._preview_orig_insert(index, chars, *tags)
        index = self.preview.index(index)
        owner = self._preview_owner_run(index, prefer_before=True)
        applied = self._preview_apply(index, index, str(chars), owner)
        if applied == "pending":
            # A capo a fine paragrafo/voce: per ora solo visivo, il sorgente cambia al primo carattere.
            return self._preview_orig_insert(index, chars, ("para", "pendingbreak"))
        if applied != "fast":
            return ""
        # Il testo nuovo prende formattazione e collegamento del pezzo in cui si scrive.
        before = self._preview_run_at(f"{index}-1c") if self.preview.compare(index, ">", "1.0") else None
        probe = f"{index}-1c" if before is not None and before[0] == owner else index
        inherited = tuple(tag for tag in self.preview.tag_names(probe) if tag not in ("sel", "pendingbreak"))
        return self._preview_orig_insert(index, chars, inherited)

    def _preview_delete(self, index1, index2=None):
        if not self._preview_user or self._preview_rendering:
            if index2 is None:
                return self._preview_orig_delete(index1)
            return self._preview_orig_delete(index1, index2)
        start = self.preview.index(index1)
        end = self.preview.index(index2) if index2 is not None else self.preview.index(f"{start}+1c")
        if self.preview.compare(start, ">=", end):
            return ""
        owner = self._preview_owner_run(start, prefer_before=False)
        if self._preview_apply(start, end, "", owner) != "fast":
            return ""
        return self._preview_orig_delete(start, end)

    def _preview_has_tag(self, index: str, tag: str) -> bool:
        try:
            return self.preview.compare(index, ">=", "1.0") and tag in self.preview.tag_names(index)
        except tk.TclError:
            return False

    def _preview_apply(self, start: str, end: str, text: str, owner: int | None) -> str:
        """Riporta nel sorgente la sostituzione ``preview[start:end] = text``.

        Restituisce "" se rifiutata, "fast" se l'anteprima va modificata come
        l'utente ha chiesto (ridisegno piu' tardi), "rendered" se e' gia' stata
        ridisegnata (modifiche che cambiano struttura o lunghezza nel sorgente),
        "pending" per un a capo che diventera' un nuovo paragrafo/voce.
        """
        if not self._preview_editable():
            if self._current_doc() is not None and self.preview_mode_var.get() != PREVIEW_MODES[0]:
                self._flash_note("Le viste LaTeX e TXT sono di sola lettura: passa ad «Anteprima».", COLORS.amber)
            return ""
        doc = self._current_doc()
        source_map = self._preview_map
        if self._edit_job is not None or self._editor_text() != source_map.source:
            # L'editor e' cambiato dopo l'ultimo disegno: prima si aggiorna l'anteprima.
            self._cancel_edit_job()
            self._after_edit()
            self._flash_note("Anteprima aggiornata: riprova.", COLORS.text_muted)
            return ""

        pending, self._pending_break = self._pending_break, None
        if pending is not None:
            if start != end and self.preview.compare(f"{start}+1c", "==", end) and \
                    self._preview_has_tag(start, "pendingbreak"):
                return "fast"  # Backspace sull'a capo in attesa: nessuna modifica al sorgente
            if start == end and text and text != "\n" and self._preview_has_tag(f"{start}-1c", "pendingbreak"):
                src, prefix = pending
                return self._write_source(src, src, prefix + escape_for_source(text.replace("\r", ""), doc.fmt),
                                          None, structural=True)

        if start == end:
            src_start = src_end = self._preview_source_offset(start, prefer_before=True)
        else:
            src_start = self._preview_source_offset(start, prefer_before=False)
            src_end = self._preview_source_offset(end, prefer_before=True)
        if src_start is None or src_end is None or src_end < src_start:
            self._flash_note(
                "Questa parte (formula, numero, elenco o marcatore) si modifica nel sorgente.", COLORS.amber
            )
            return ""
        verbatim = owner is not None and owner in source_map.verbatim
        if text == "\n":
            replacement = newline_for_source(source_map.source, src_start, doc.fmt, verbatim)
            line_end = source_map.source.find("\n", src_end)
            tail = source_map.source[src_end:line_end if line_end >= 0 else len(source_map.source)]
            if not verbatim and start == end and not tail.strip():
                self._pending_break = (src_start, replacement)
                return "pending"
            return self._write_source(src_start, src_end, replacement, owner, structural=True)
        replacement = escape_for_source(text.replace("\r", ""), doc.fmt, verbatim)
        # Se il sorgente cresce piu' dell'anteprima (escape) o la modifica
        # attraversa piu' pezzi, l'anteprima va ridisegnata subito.
        structural = len(replacement) != len(text) or "\n" in text or source_map.escaped(owner) or (
            start != end and self._preview_owner_run(end, prefer_before=True) != owner
        )
        return self._write_source(src_start, src_end, replacement, owner, structural)

    def _write_source(self, src_start: int, src_end: int, replacement: str, owner: int | None,
                      structural: bool) -> str:
        """Scrive la modifica nell'editor del sorgente (con annulla) e nel documento."""
        doc = self._current_doc()
        widget = self.editor._textbox
        self._suspend_modified = True
        try:
            if src_end > src_start:
                widget.delete(f"1.0+{src_start}c", f"1.0+{src_end}c")
            if replacement:
                widget.insert(f"1.0+{src_start}c", replacement)
            widget.edit_modified(False)
        finally:
            self._suspend_modified = False
        self._preview_map.replace(src_start, src_end, replacement, owner)
        doc.source = self._preview_map.source
        self._preview_cursor_src = src_start + len(replacement)
        self.after_idle(self._follow_preview_edit, src_start + len(replacement))
        self._update_buttons()
        self._schedule_draft_save()
        if not structural:
            self._schedule_preview_refresh(900)
            return "fast"
        # Ridisegno immediato e completo: il tasto successivo trova gia' l'anteprima aggiornata.
        self._preview_rendering += 1
        try:
            self.render_preview(sync=True)
        finally:
            self._preview_rendering -= 1
        return "rendered"

    def _schedule_preview_refresh(self, delay: int) -> None:
        self._cancel_preview_refresh()
        self._preview_refresh_job = self.after(delay, self._refresh_preview_after_edit)

    def _cancel_preview_refresh(self) -> None:
        if self._preview_refresh_job is not None:
            try:
                self.after_cancel(self._preview_refresh_job)
            except (tk.TclError, ValueError):
                pass
            self._preview_refresh_job = None

    def _refresh_preview_after_edit(self) -> None:
        self._preview_refresh_job = None
        if self.focus_get() is self.preview and self._preview_cursor_src is None:
            self._preview_cursor_src = self._preview_source_offset("insert", prefer_before=True)
        self.render_preview()

    # ------------------------------------------------------------------ sincronizzazione editor <-> anteprima
    @staticmethod
    def _view_y(widget: tk.Text, index) -> int | None:
        """Distanza in pixel dal bordo superiore della riga di ``index`` (None = non visibile)."""
        try:
            info = widget.dlineinfo(index)
        except tk.TclError:
            return None
        return int(info[1]) if info else None

    @staticmethod
    def _align_view(widget: tk.Text, index, y: int | None) -> None:
        """Porta ``index`` in vista; con ``y`` lo mette alla stessa altezza (in pixel)."""
        try:
            widget.see(index)
            if y is None:
                return
            height = widget.winfo_height()
            if height > 1:
                y = max(0, min(y, int(height * 0.8)))
            info = widget.dlineinfo(index)
            if info is None:
                return
            delta = int(info[1]) - y
            if delta:
                widget.yview_scroll(delta, "pixels")
        except tk.TclError:
            pass

    def _editor_offset(self, index) -> int:
        return len(self.editor._textbox.get("1.0", index))

    def _preview_index_for_source(self, offset: int) -> str | None:
        """Punto dell'anteprima che mostra (o e' piu' vicino a) ``offset`` del sorgente."""
        if self._preview_map is None:
            return None
        found = self._preview_map.nearest(offset)
        if found is None:
            return None
        run_id, position = found
        ranges = self.preview.tag_ranges(f"src{run_id}")
        if not ranges:
            return None
        try:
            index = self.preview.index(f"{ranges[0]}+{position}c")
            last = self.preview.index(ranges[-1])
            return last if self.preview.compare(index, ">", last) else index
        except tk.TclError:
            return None

    def _preview_nearest_source(self, index: str) -> int | None:
        """Offset del sorgente per un punto dell'anteprima, anche dentro formule e marcatori."""
        if self._preview_map is None:
            return None
        offset = self._preview_source_offset(index, prefer_before=False)
        if offset is not None:
            return offset
        try:
            index = self.preview.index(index)
        except tk.TclError:
            return None
        # I pezzi sono numerati nell'ordine del disegno: l'ultimo che inizia prima di index.
        best: int | None = None
        first: int | None = None
        for run_id in sorted(self._preview_map.runs):
            ranges = self.preview.tag_ranges(f"src{run_id}")
            if not ranges:
                continue
            if first is None:
                first = run_id
            if self.preview.compare(ranges[0], ">", index):
                break
            best = run_id
        if best is not None:
            return self._preview_map.runs[best][1]
        return self._preview_map.runs[first][0] if first is not None else None

    def _capture_render_anchor(self, doc: DocState) -> tuple[int | None, int | None, int | None] | None:
        """Cosa tenere fermo nel prossimo disegno: ``(offset in vista, y, offset del cursore)``.

        In ordine: il cursore dell'anteprima (modifica fatta li'), il cursore
        dell'editor (modifica fatta nel sorgente), un ancoraggio ancora da applicare
        (disegno precedente interrotto), il testo in cima alla vista. Un cursore
        dell'anteprima fuori vista (l'utente ha scorso altrove) viene rimesso al suo
        posto senza riportarci la vista.
        """
        if self._preview_cursor_src is not None:
            self._follow_editor = False
            y = self._view_y(self.preview, "insert")
            if y is not None:
                return self._preview_cursor_src, y, self._preview_cursor_src
            top = self._top_anchor(doc)
            return (top[0], top[1], self._preview_cursor_src) if top else (None, None, self._preview_cursor_src)
        if self._follow_editor:
            self._follow_editor = False
            widget = self.editor._textbox
            y = self._view_y(widget, "insert") if self._editor_visible else None
            return self._editor_offset("insert"), y, None
        if self._render_anchor is not None:
            return self._render_anchor
        top = self._top_anchor(doc)
        return (top[0], top[1], None) if top else None

    def _top_anchor(self, doc: DocState) -> tuple[int, int | None] | None:
        """Punto del sorgente mostrato in cima all'anteprima, con la sua altezza in pixel."""
        if self._preview_map is None or self._preview_doc is not doc:
            return None
        try:
            top = self.preview.index("@0,0")
        except tk.TclError:
            return None
        offset = self._preview_nearest_source(top)
        return (offset, self._view_y(self.preview, top)) if offset is not None else None

    def _apply_render_anchor(self) -> None:
        """Dopo un nuovo disegno rimette in vista lo stesso punto del testo."""
        anchor, self._render_anchor = self._render_anchor, None
        self._preview_cursor_src = None
        if anchor is None:
            return
        offset, y, cursor = anchor
        if cursor is not None:
            index = self._preview_index_for_source(cursor)
            if index is not None:
                self.preview.mark_set("insert", index)
        if offset is not None:
            index = self._preview_index_for_source(offset)
            if index is not None:
                self._align_view(self.preview, index, y)

    def _on_preview_user_scroll(self, _event=None) -> None:
        """Lo scorrimento manuale dell'anteprima vince sull'inseguimento dell'editor."""
        self._follow_editor = False
        if self._render_anchor is not None:
            # Il cursore resta da rimettere; la vista resta dove l'utente l'ha portata.
            self._render_anchor = (None, None, self._render_anchor[2])

    def _schedule_follow(self, origin: str) -> None:
        if self._follow_job is not None:
            try:
                self.after_cancel(self._follow_job)
            except (tk.TclError, ValueError):
                pass
        self._follow_job = self.after(SELECTION_FOLLOW_MS, lambda: self._follow_selection(origin))

    def _follow_selection(self, origin: str) -> None:
        """Testo selezionato in una vista: l'altra mostra lo stesso punto alla stessa altezza."""
        self._follow_job = None
        if self._preview_map is None or self._current_doc() is None or self._preview_rendering:
            return
        widget = self.editor._textbox
        try:
            if origin == "editor":
                ranges = widget.tag_ranges("sel")
                if not ranges or self.focus_get() is not widget:
                    return
                if self._edit_job is not None or self._editor_text() != self._preview_map.source:
                    self._follow_editor = True  # l'anteprima e' indietro: la segue il prossimo disegno
                    return
                index = self._preview_index_for_source(self._editor_offset(ranges[0]))
                if index is not None:
                    self._align_view(self.preview, index, self._view_y(widget, ranges[0]))
            else:
                ranges = self.preview.tag_ranges("sel")
                if not ranges or self.focus_get() is not self.preview or not self._editor_visible:
                    return
                offset = self._preview_nearest_source(str(ranges[0]))
                if offset is None:
                    return
                self._align_view(widget, widget.index(f"1.0+{offset}c"), self._view_y(self.preview, ranges[0]))
        except tk.TclError:
            pass

    def _follow_preview_edit(self, offset: int) -> None:
        """Modifica scritta nell'anteprima: l'editor mostra (e mette il cursore su) quel punto."""
        if not self._editor_visible:
            return
        widget = self.editor._textbox
        try:
            index = widget.index(f"1.0+{offset}c")
            widget.mark_set("insert", index)
            self._align_view(widget, index, self._view_y(self.preview, "insert"))
        except tk.TclError:
            pass

    def _preview_undo(self, redo: bool) -> None:
        """Ctrl+Z / Ctrl+Y nell'anteprima annullano o ripetono le modifiche del sorgente."""
        if self._current_doc() is None:
            return
        widget = self.editor._textbox
        try:
            widget.edit_redo() if redo else widget.edit_undo()
        except tk.TclError:
            self._flash_note("Niente da ripetere." if redo else "Niente da annullare.", COLORS.text_muted)
            return
        self._preview_cursor_src = len(widget.get("1.0", "insert"))
        self._cancel_edit_job()
        self._after_edit()
        self.schedule_render(immediate=True)

    # ------------------------------------------------------------------ sorgente comprimibile
    def toggle_editor_pane(self) -> None:
        """Mostra o nasconde il codice sorgente: l'anteprima resta modificabile."""
        self._commit_editor()
        if self._editor_visible:
            self.paned.forget(self.editor_frame)
            self._editor_visible = False
            self._flash_note("Sorgente nascosto: scrivi direttamente nell'anteprima.", COLORS.text_muted)
        else:
            self.paned.add(self.editor_frame, before=self.preview_frame, minsize=220, stretch="always")
            self._editor_visible = True
        self._update_editor_toggle()
        self._save_settings()

    def _update_editor_toggle(self) -> None:
        if hasattr(self, "editor_toggle_button"):
            doc = self._current_doc() if hasattr(self, "docs") else None
            kind = "LaTeX" if doc is not None and doc.fmt == SOURCE_LATEX else "MD"
            self.editor_toggle_button.configure(
                text=f"◀  Nascondi {kind}" if self._editor_visible else f"▶  Mostra {kind}"
            )

    def _numbering_start_note(self, options: RenderOptions, numbering: SectionNumbering) -> str:
        """Nota sul punto di partenza quando il template contiene gia' delle sezioni."""
        if not any(numbering.counts):
            return ""
        probe = SectionNumbering(options.language)
        probe.counts = list(numbering.counts)
        probe.has_chapter = numbering.has_chapter
        first = probe.step(heading_level_name(1, options))
        return f"si parte da {first}: il template contiene gia' delle sezioni" if first else ""

    def _preview_operations(self, slides, options: RenderOptions):
        if options.embed and options.include_headings and (options.root_title or options.title).strip():
            yield ("root", (options.root_title or options.title).strip())
        for slide_index, slide in enumerate(slides):
            if show_marker(slide, options):
                yield ("marker", f"SLIDE {slide.number}")
            elif slide_index:
                yield ("rule", "")
            for block in slide.blocks:
                yield ("block", block)

    def _render_chunk(
        self, operations: list, start: int, generation: int, position: float, sync: bool = False
    ) -> None:
        if generation != self._render_generation:
            return
        end = len(operations) if sync else min(len(operations), start + PREVIEW_RENDER_CHUNK)
        options = self.render_options(self._current_doc())
        for kind, payload in operations[start:end]:
            if kind == "marker":
                self.preview.insert("end", payload + "\n", "marker")
            elif kind == "rule":
                self.preview.insert("end", "─" * 40 + "\n", "rule")
            elif kind == "root":
                self._insert_heading(payload, options.base_level, options, plain=True)
            else:
                self._insert_block(payload, options)
        if start == 0:
            self.preview.yview_moveto(position)
        if end < len(operations):
            self.after(1, lambda: self._render_chunk(operations, end, generation, position))
        else:
            self.preview.yview_moveto(position)
            self._apply_render_anchor()

    def _renderer_instance(self) -> MathRenderer:
        if self._renderer is None:
            self._renderer = MathRenderer(self.preview, COLORS.text)
        return self._renderer

    def _insert_block(self, block, options: RenderOptions) -> None:
        text = self.preview
        if block.kind == BLOCK_HEADING:
            if not options.include_headings:
                return
            self._insert_heading(block.text, heading_level_name(min(block.level, 3), options), options)
        elif block.kind in (BLOCK_BULLET, BLOCK_NUMBER, BLOCK_LETTER):
            level = max(0, min(3, block.level))
            tag = f"list{level}"
            if block.kind == BLOCK_BULLET:
                glyph = ("•", "◦", "▪", "‣")[level]
            elif block.kind == BLOCK_LETTER:
                glyph = f"{letter_label(block.number)})"
            else:
                glyph = f"{block.number or 1}."
            text.insert("end", f"{glyph}  ", (tag,))
            self._insert_inline(block.text, (tag,))
            text.insert("end", "\n", tag)
        elif block.kind == BLOCK_MATH:
            image = self._renderer_instance().image(block.text, display=True, size=self._pz(13))
            if image is not None:
                self._preview_images.append(image)
                start = text.index("end-1c")
                text.image_create("end", image=image, align="center", padx=2)
                text.insert("end", "\n")
                text.tag_add("display", start, "end-1c")
            else:
                text.insert("end", f"$$ {block.text} $$\n", ("display", "mathcode"))
        elif block.kind == BLOCK_TABLE:
            for row in block.text.splitlines():
                run_id = self._preview_map.locate(row, verbatim=True) if self._preview_map is not None else None
                text.insert("end", row, ("table", f"src{run_id}") if run_id is not None else ("table",))
                text.insert("end", "\n", "table")
        elif block.kind == BLOCK_CODE:
            if block.language:
                text.insert("end", block.language.upper() + "\n", "codeblock_label")
            for code_line in (block.text.rstrip("\n") or " ").split("\n"):
                # una riga per volta con uno spazio finale: lo sfondo copre anche le righe vuote
                shown = code_line.expandtabs(4)
                run_id = (
                    self._preview_map.locate(shown, verbatim=True)
                    if self._preview_map is not None and shown == code_line else None
                )
                if run_id is not None:
                    text.insert("end", shown, ("codeblock", f"src{run_id}"))
                    text.insert("end", " \n", "codeblock")
                else:
                    text.insert("end", (shown or " ") + " \n", "codeblock")
            text.insert("end", "\n", "para")
        else:
            self._insert_inline(block.text, ("para",))
            text.insert("end", "\n", "para")

    def _insert_heading(self, value: str, level: str, options: RenderOptions, plain: bool = False) -> None:
        """Titolo con il numero che gli darebbe LaTeX (1, 1.1, Capitolo 2, Parte I…)."""
        top = SECTION_LEVELS.index(options.base_level if options.embed else "section")
        depth = max(0, SECTION_LEVELS.index(level) - top) if level in SECTION_LEVELS else 0
        tag = f"h{min(3, depth + 1)}"
        numbering = self._preview_numbering or SectionNumbering(options.language)
        label = numbering.step(level, starred=not options.numbered_sections)
        if label:
            separator = "\n" if level in ("part", "chapter") else "   "
            self.preview.insert("end", label + separator, (tag, "secnum"))
        if plain:
            self.preview.insert("end", value, (tag,))
        else:
            self._insert_inline(value, (tag,), size=17 if tag == "h1" else 14)
        self.preview.insert("end", "\n", tag)

    def _insert_inline(self, value: str, tags: tuple[str, ...], size: int = 12) -> None:
        """Testo in linea con formule, grassetto, corsivo, sottolineato, monospazio e colori."""
        text = self.preview
        renderer = self._renderer_instance()
        heading = any(tag in ("h1", "h2", "h3") for tag in tags)
        for kind, content, styles in iter_runs(parse_inline(value)):
            names = {style for style, _value in styles}
            colors = [value for style, value in styles if style == STYLE_COLOR]
            if kind == "math":
                image = renderer.image(content, display=False, size=self._pz(size))
                if image is not None:
                    self._preview_images.append(image)
                    text.image_create("end", image=image, align="center", padx=1)
                else:
                    text.insert("end", f"${content}$", tags + ("mathcode",))
                continue
            run_tags = list(tags)
            font_size = next((HEADING_FONT_SIZES[tag] for tag in tags if tag in HEADING_FONT_SIZES), 12)
            font_tag = self._style_font_tag(
                font_size, bold=STYLE_BOLD in names or heading, italic=STYLE_ITALIC in names,
                mono_font=STYLE_MONO in names, force=bool(names & {STYLE_BOLD, STYLE_ITALIC, STYLE_MONO}),
            )
            if font_tag:
                run_tags.append(font_tag)
            if STYLE_MONO in names:
                run_tags.append("inline_code")
            if STYLE_UNDERLINE in names:
                run_tags.append("underline")
            if colors:
                color_tag = self._color_tag(colors[-1])  # il colore piu' interno vince, come in LaTeX
                if color_tag:
                    run_tags.append(color_tag)
            display = unescape_text(content)
            run_id = self._preview_map.locate(display) if self._preview_map is not None else None
            if run_id is not None:
                run_tags.append(f"src{run_id}")
            text.insert("end", display, tuple(run_tags))

    def _style_font_tag(self, size: int, bold: bool, italic: bool, mono_font: bool, force: bool) -> str:
        if not force:
            return ""
        size = self._pz(size)
        name = f"font_{size}_{int(bold)}{int(italic)}{int(mono_font)}"
        if name not in self._font_tags:
            family = MONO_FONT_FAMILY if mono_font else FONT_FAMILY
            self.preview.tag_configure(
                name,
                font=ctk.CTkFont(
                    family=family, size=size - 1 if mono_font else size,
                    weight="bold" if bold else "normal", slant="italic" if italic else "roman",
                ),
            )
            self._font_tags.add(name)
            self._raise_overlay_tags()
        return name

    def _color_tag(self, value: str) -> str:
        color = preview_color(value)
        if color is None:
            return ""
        name = f"color_{color.lstrip('#')}"
        if name not in self._font_tags:
            self.preview.tag_configure(name, foreground=color)
            self._font_tags.add(name)
            self._raise_overlay_tags()
        return name

    def _raise_overlay_tags(self) -> None:
        """Colori e sottolineatura sopra ai tag dei titoli e del testo."""
        for name in sorted(self._font_tags):
            if name.startswith("font_"):
                self.preview.tag_raise(name)
        for name in sorted(self._font_tags):
            if name.startswith("color_"):
                self.preview.tag_raise(name)
        self.preview.tag_raise("underline")

    # ------------------------------------------------------------------ formattazione rapida (tasto destro)
    def _build_editor_menu(self) -> None:
        style = dict(
            tearoff=0, bg=COLORS.surface_alt, fg=COLORS.text, activebackground=COLORS.accent_dark,
            activeforeground=COLORS.text, disabledforeground=COLORS.text_faint, bd=0, relief=tk.FLAT,
        )
        menu = tk.Menu(self.editor._textbox, **style)
        entries: dict[str, int] = {}

        def remember(key: str) -> None:
            entries[key] = menu.index("end")

        menu.add_command(label="Grassetto", accelerator="Ctrl+B", command=lambda: self.format_selection(STYLE_BOLD))
        remember("bold")
        menu.add_command(label="Corsivo", accelerator="Ctrl+I", command=lambda: self.format_selection(STYLE_ITALIC))
        remember("italic")
        menu.add_command(label="Sottolineato", accelerator="Ctrl+U",
                         command=lambda: self.format_selection(STYLE_UNDERLINE))
        remember("underline")
        menu.add_command(label="Monospazio (\\texttt)", accelerator="Ctrl+E",
                         command=lambda: self.format_selection(STYLE_MONO))
        remember("mono")
        colors = tk.Menu(menu, **style)
        for name, (label, hex_color) in COLOR_CHOICES.items():
            colors.add_command(
                label=f"●  {label}", foreground=hex_color, activeforeground=hex_color,
                command=lambda value=name: self.format_selection(STYLE_COLOR, value),
            )
        menu.add_cascade(label="Colore", menu=colors)
        remember("color")
        menu.add_command(label="Rimuovi formattazione", accelerator="Ctrl+Spazio", command=self.clear_formatting)
        remember("clear")
        menu.add_separator()
        lists = tk.Menu(menu, **style)
        lists.add_command(label="1.  2.  3.   Numerato", command=lambda: self.create_list(LIST_STYLE_NUMBER))
        lists.add_command(label="a)  b)  c)   Con le lettere", command=lambda: self.create_list(LIST_STYLE_LETTER))
        lists.add_command(label="•   •   •    Senza numeri (puntato)",
                          command=lambda: self.create_list(LIST_STYLE_BULLET))
        menu.add_cascade(label="Crea elenco", menu=lists)
        remember("list")
        menu.add_command(label="Blocco di codice", command=self.toggle_code_block)
        remember("code")
        menu.add_command(label="A capo qui  ( // )", command=self.insert_line_break)
        remember("break")
        menu.add_separator()
        menu.add_command(label="Taglia", accelerator="Ctrl+X",
                         command=lambda: self.editor._textbox.event_generate("<<Cut>>"))
        remember("cut")
        menu.add_command(label="Copia", accelerator="Ctrl+C",
                         command=lambda: self.editor._textbox.event_generate("<<Copy>>"))
        remember("copy")
        menu.add_command(label="Incolla", accelerator="Ctrl+V",
                         command=lambda: self.editor._textbox.event_generate("<<Paste>>"))
        remember("paste")
        menu.add_separator()
        menu.add_command(label="Rimuovi questo testo ovunque…", accelerator="Ctrl+H", command=self.open_remove_dialog)
        remember("remove")
        menu.add_command(label="Togli i marcatori === SLIDE ===", command=self.remove_slide_markers)
        remember("markers")
        menu.add_command(label="Salva", accelerator="Ctrl+S", command=self.save_now)
        remember("save")
        self.editor_menu = menu
        self._editor_entries = entries

        # Stesso menu sull'anteprima: la selezione viene ritrovata nel sorgente.
        preview_menu = tk.Menu(self.preview, **style)
        on_preview = self._apply_on_preview_selection
        preview_menu.add_command(label="Grassetto", command=lambda: on_preview(STYLE_BOLD))
        preview_menu.add_command(label="Corsivo", command=lambda: on_preview(STYLE_ITALIC))
        preview_menu.add_command(label="Sottolineato", command=lambda: on_preview(STYLE_UNDERLINE))
        preview_menu.add_command(label="Monospazio (\\texttt)", command=lambda: on_preview(STYLE_MONO))
        preview_colors = tk.Menu(preview_menu, **style)
        for name, (label, hex_color) in COLOR_CHOICES.items():
            preview_colors.add_command(
                label=f"●  {label}", foreground=hex_color, activeforeground=hex_color,
                command=lambda value=name: on_preview(STYLE_COLOR, value),
            )
        preview_menu.add_cascade(label="Colore", menu=preview_colors)
        preview_menu.add_command(label="Rimuovi formattazione", command=lambda: on_preview(""))
        preview_menu.add_separator()
        preview_menu.add_command(label="Copia", accelerator="Ctrl+C",
                                 command=lambda: self.preview.event_generate("<<Copy>>"))
        preview_menu.add_command(label="Mostra nell'editor", command=self._reveal_preview_selection)
        self.preview_menu = preview_menu

    def _show_editor_menu(self, event) -> str:
        LOGGER.info("Menu tasto destro aperto (editor).")
        widget = self.editor._textbox
        has_doc = self._current_doc() is not None and not self.controller.is_running()
        has_selection = bool(widget.tag_ranges("sel"))
        if not has_selection:
            widget.mark_set("insert", f"@{event.x},{event.y}")
        multi_line = has_selection and len(
            [line for line in widget.get("sel.first", "sel.last").split("\n") if line.strip()]
        ) >= 2
        entries = self._editor_entries

        def state(key: str, enabled: bool) -> None:
            self.editor_menu.entryconfigure(entries[key], state="normal" if enabled else "disabled")

        for key in ("bold", "italic", "underline", "mono", "color", "code", "break", "markers", "save"):
            state(key, has_doc)
        state("clear", has_doc and has_selection)
        state("list", has_doc and multi_line)
        state("cut", has_selection)
        state("copy", has_selection)
        state("remove", has_doc and has_selection)
        widget.focus_set()
        try:
            self.editor_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.editor_menu.grab_release()
        return "break"

    def _show_preview_menu(self, event) -> str:
        LOGGER.info("Menu tasto destro aperto (anteprima).")
        has_doc = self._current_doc() is not None and not self.controller.is_running()
        has_selection = bool(self.preview.tag_ranges("sel"))
        rendered = self.preview_mode_var.get() == PREVIEW_MODES[0]
        state = "normal" if has_doc and has_selection and rendered else "disabled"
        for index in range(6):
            self.preview_menu.entryconfigure(index, state=state)
        self.preview_menu.entryconfigure(7, state="normal" if has_selection else "disabled")
        self.preview_menu.entryconfigure(8, state=state)
        try:
            self.preview_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.preview_menu.grab_release()
        return "break"

    _PREVIEW_PREFIX_RE = re.compile(
        r"^(?:[•◦▪‣]|\d+\.|\d+(?:\.\d+)*|Parte [IVXLCDM]+|Capitolo \d+)\s{2,}"
    )

    def _preview_selection_targets(self) -> list[tuple[str, str]]:
        """Intervalli dell'editor che corrispondono al testo selezionato nell'anteprima.

        Ogni riga selezionata (senza punti elenco, numeri di sezione o di lista)
        viene cercata nel sorgente, in ordine; per la prima si sceglie
        l'occorrenza con lo stesso indice che ha nell'anteprima, cosi' un testo
        ripetuto in piu' slide viene trovato nel punto giusto.
        """
        try:
            selected = self.preview.get("sel.first", "sel.last")
            before = self.preview.get("1.0", "sel.first")
        except tk.TclError:
            return []
        self._commit_editor()
        source = self._editor_text()
        if self._preview_map is not None and self._preview_map.source == source:
            start = self._preview_source_offset("sel.first", prefer_before=False)
            end = self._preview_source_offset("sel.last", prefer_before=True)
            if start is not None and end is not None and start < end:
                return [(f"1.0+{start}c", f"1.0+{end}c")]
        lines = []
        for raw in selected.split("\n"):
            line = self._PREVIEW_PREFIX_RE.sub("", raw.strip()) if raw.strip() else ""
            if not line or line.startswith("SLIDE ") or set(line) <= {"─"}:
                continue
            lines.append(line)
        targets: list[tuple[str, str]] = []
        position = 0
        for index, line in enumerate(lines):
            candidates = [line] + ([line.replace("$", "\\$")] if "$" in line else [])
            found = -1
            length = 0
            for candidate in candidates:
                if index == 0:
                    occurrence = before.count(candidate)
                    found = self._nth_find(source, candidate, occurrence)
                    if found == -1:
                        found = source.find(candidate)
                else:
                    found = source.find(candidate, position)
                if found != -1:
                    length = len(candidate)
                    break
            if found == -1:
                continue
            targets.append((f"1.0+{found}c", f"1.0+{found + length}c"))
            position = found + length
        return targets

    @staticmethod
    def _nth_find(text: str, needle: str, occurrence: int) -> int:
        position = -1
        for _ in range(occurrence + 1):
            position = text.find(needle, position + 1)
            if position == -1:
                return -1
        return position

    def _apply_on_preview_selection(self, style: str, color: str = "") -> None:
        """Applica la formattazione (``style`` vuoto = rimuovi) al testo selezionato nell'anteprima."""
        if self._current_doc() is None or self.controller.is_running():
            return
        targets = self._preview_selection_targets()
        if not targets:
            messagebox.showinfo(
                "PDF e Slide",
                "Non riesco a ritrovare il testo selezionato nel sorgente (succede se la selezione "
                "comprende formule o testo gia' formattato).\nSelezionalo nell'editor e usa il tasto destro li'.",
                parent=self,
            )
            return
        widget = self.editor._textbox
        # dall'ultimo al primo, cosi' gli indici precedenti restano validi
        for start, end in reversed(targets):
            start, end = widget.index(start), widget.index(end)
            widget.tag_remove("sel", "1.0", "end")
            widget.tag_add("sel", start, end)
            if style:
                self.format_selection(style, color)
            else:
                self.clear_formatting()
        widget.tag_remove("sel", "1.0", "end")
        widget.see(widget.index(targets[0][0]))

    def _reveal_preview_selection(self) -> None:
        targets = self._preview_selection_targets()
        if not targets:
            return
        if not self._editor_visible:
            self.toggle_editor_pane()
        widget = self.editor._textbox
        widget.tag_remove("sel", "1.0", "end")
        widget.tag_add("sel", targets[0][0], targets[-1][1])
        widget.mark_set("insert", targets[-1][1])
        widget.see(targets[0][0])
        widget.focus_set()

    def _wrapper(self, style: str, color: str = "") -> tuple[str, str]:
        doc = self._current_doc()
        latex = doc is not None and doc.fmt == SOURCE_LATEX
        if style == STYLE_BOLD:
            return ("\\textbf{", "}") if latex else ("**", "**")
        if style == STYLE_ITALIC:
            return ("\\emph{", "}") if latex else ("*", "*")
        if style == STYLE_UNDERLINE:
            return "\\underline{", "}"
        if style == STYLE_MONO:
            return "\\texttt{", "}"
        return f"\\textcolor{{{color or 'red'}}}{{", "}"

    _LINE_PREFIX_RE = re.compile(r"^(\s*(?:[-*+•]\s+|\d+[.)]\s+|#{1,6}\s+)?)(.*?)(\s*)$")
    _SKIP_LINE_RE = re.compile(
        r"^\s*(=+\s*SLIDE\s+\d+\s*=+|\$\$|\\\[|\\\]|\\begin\{|\\end\{|%|\||```|~~~)", re.IGNORECASE
    )
    _COLOR_OPEN_RE = re.compile(r"\\textcolor\{[^{}]*\}\{$")

    def _wrap_text(self, text: str, prefix: str, suffix: str) -> str:
        """Avvolge ogni riga non vuota mantenendo punti elenco, numeri e # dei titoli."""
        lines = text.split("\n")
        out = []
        for line in lines:
            if not line.strip() or (len(lines) > 1 and self._SKIP_LINE_RE.match(line)):
                out.append(line)
                continue
            match = self._LINE_PREFIX_RE.match(line) if len(lines) > 1 else None
            if match and match.group(2):
                out.append(f"{match.group(1)}{prefix}{match.group(2)}{suffix}{match.group(3)}")
            else:
                out.append(f"{prefix}{line}{suffix}")
        return "\n".join(out)

    def _replace_range(self, start: str, end: str, new_text: str, select: bool = True) -> None:
        widget = self.editor._textbox
        widget.edit_separator()
        widget.delete(start, end)
        widget.insert(start, new_text)
        widget.edit_separator()
        widget.tag_remove("sel", "1.0", "end")
        if select:
            widget.tag_add("sel", start, f"{start}+{len(new_text)}c")
        widget.mark_set("insert", f"{start}+{len(new_text)}c")

    def format_selection(self, style: str, color: str = "") -> None:
        """Applica (o toglie, se gia' presente) la formattazione alla selezione."""
        if self._current_doc() is None or self.controller.is_running():
            return
        widget = self.editor._textbox
        prefix, suffix = self._wrapper(style, color)
        ranges = widget.tag_ranges("sel")
        if not ranges:
            # nessuna selezione: inserisce il comando vuoto con il cursore dentro
            widget.edit_separator()
            widget.insert("insert", prefix + suffix)
            widget.mark_set("insert", f"insert-{len(suffix)}c")
            widget.edit_separator()
            return
        start, end = str(ranges[0]), str(ranges[1])
        selected = widget.get(start, end)
        before = widget.get(f"{start} linestart", start)
        after = widget.get(end, f"{end} lineend")
        # 1) la selezione e' gia' dentro lo stesso comando: lo si toglie (o si cambia colore)
        if style == STYLE_COLOR:
            opened = self._COLOR_OPEN_RE.search(before)
            if opened and after.startswith("}"):
                outer_start = f"{start}-{len(opened.group(0))}c"
                current = re.match(r"\\textcolor\{([^{}]*)\}", opened.group(0)).group(1)
                replacement = selected if current == color else prefix + selected + suffix
                self._replace_range(outer_start, f"{end}+1c", replacement)
                return
        elif before.endswith(prefix) and after.startswith(suffix) and not (
            style == STYLE_ITALIC and prefix == "*" and before.endswith("**")
        ):
            self._replace_range(f"{start}-{len(prefix)}c", f"{end}+{len(suffix)}c", selected)
            return
        # 2) la selezione comprende gia' il comando: lo si toglie
        if "\n" not in selected:
            if style == STYLE_COLOR:
                inner = re.fullmatch(r"\\textcolor\{([^{}]*)\}\{(.*)\}", selected, re.DOTALL)
                if inner and self._balanced(inner.group(2)):
                    replacement = inner.group(2) if inner.group(1) == color else prefix + inner.group(2) + suffix
                    self._replace_range(start, end, replacement)
                    return
            elif (
                selected.startswith(prefix) and selected.endswith(suffix) and len(selected) > len(prefix + suffix)
                and self._balanced(selected[len(prefix):len(selected) - len(suffix)])
                and not (prefix == "*" and selected.startswith("**"))
            ):
                self._replace_range(start, end, selected[len(prefix):len(selected) - len(suffix)])
                return
        # 3) altrimenti si applica, riga per riga
        self._replace_range(start, end, self._wrap_text(selected, prefix, suffix))

    @staticmethod
    def _balanced(text: str) -> bool:
        depth = 0
        index = 0
        while index < len(text):
            char = text[index]
            if char == "\\":
                index += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth < 0:
                    return False
            index += 1
        return depth == 0

    @staticmethod
    def _strip_styles(text: str) -> str:
        """Toglie tutta la formattazione in linea lasciando testo e formule."""
        lines = []
        for line in text.split("\n"):
            out = []
            for node_kind, content, _styles in iter_runs(parse_inline(line)):
                if node_kind == "math":
                    out.append(f"${content}$")
                elif node_kind == "break":
                    out.append(LINE_BREAK_SOURCE)
                else:
                    out.append(content)
            lines.append("".join(out))
        return "\n".join(lines)

    def clear_formatting(self) -> None:
        if self._current_doc() is None or self.controller.is_running():
            return
        widget = self.editor._textbox
        ranges = widget.tag_ranges("sel")
        if not ranges:
            return
        start, end = str(ranges[0]), str(ranges[1])
        # se la selezione e' esattamente il contenuto di un comando, si toglie anche quello
        before = widget.get(f"{start} linestart", start)
        after = widget.get(end, f"{end} lineend")
        outer = re.search(r"(\\(?:underline|texttt|textbf|textit|emph)\{|\\textcolor\{[^{}]*\}\{|\*\*|\*)$", before)
        if outer:
            closing = "}" if outer.group(1).endswith("{") else outer.group(1)
            if after.startswith(closing):
                start = f"{start}-{len(outer.group(1))}c"
                end = f"{end}+{len(closing)}c"
        selected = widget.get(start, end)
        cleaned = self._strip_styles(selected)
        if cleaned != selected:
            self._replace_range(start, end, cleaned)

    # ------------------------------------------------------------------ elenchi, codice, a capo
    def _selected_lines_range(self) -> tuple[str, str] | None:
        """Righe intere coperte dalla selezione (una selezione che finisce a inizio riga la esclude)."""
        widget = self.editor._textbox
        ranges = widget.tag_ranges("sel")
        if not ranges:
            return None
        start = widget.index(f"{ranges[0]} linestart")
        end = widget.index(ranges[1])
        if end.endswith(".0") and widget.compare(end, ">", start):
            end = widget.index(f"{end} -1c")
        return start, widget.index(f"{end} lineend")

    def _is_latex_doc(self) -> bool:
        doc = self._current_doc()
        return doc is not None and doc.fmt == SOURCE_LATEX

    def create_list(self, style: str) -> None:
        """Trasforma le righe selezionate in un elenco numerato, con le lettere o puntato.

        La formattazione delle righe (grassetto, colori, formule...) resta com'e';
        eventuali punti elenco o numeri gia' presenti vengono sostituiti, quindi
        lo stesso comando serve anche a cambiare il tipo di un elenco.
        """
        if self._current_doc() is None or self.controller.is_running():
            return
        span = self._selected_lines_range()
        widget = self.editor._textbox
        lines = widget.get(*span).split("\n") if span else []
        if len([line for line in lines if line.strip()]) < 2:
            messagebox.showinfo(
                "PDF e Slide", "Seleziona almeno due righe: ogni riga diventa una voce dell'elenco.", parent=self,
            )
            return
        latex = self._is_latex_doc()
        items: list[tuple[int, str]] = []
        for line in lines:
            if not line.strip() or (latex and _LATEX_LIST_SCAFFOLD_RE.match(line)):
                continue
            match = _LIST_PREFIX_RE.match(line.expandtabs(4))
            indent, body = len(match.group(1)), match.group(2).rstrip()
            if body:
                items.append((indent, body))
        if not items:
            return
        base = min(indent for indent, _body in items)
        leveled = [(min(3, (indent - base) // 2), body) for indent, body in items]
        text = self._latex_list(leveled, style, " " * base) if latex else self._markdown_list(leveled, style, " " * base)
        self._replace_range(span[0], span[1], text, select=False)
        names = {LIST_STYLE_NUMBER: "numerato", LIST_STYLE_LETTER: "con le lettere", LIST_STYLE_BULLET: "puntato"}
        self._flash_note(f"Creato elenco {names.get(style, '')} con {len(items)} voci.")

    @staticmethod
    def _markdown_list(items: list[tuple[int, str]], style: str, base: str) -> str:
        counters = [0, 0, 0, 0]
        out = []
        previous = 0
        for level, body in items:
            level = min(level, previous + 1)  # niente salti di livello
            counters[level] += 1
            for deeper in range(level + 1, 4):
                counters[deeper] = 0
            if style == LIST_STYLE_NUMBER:
                label = f"{counters[level]}."
            elif style == LIST_STYLE_LETTER:
                label = f"{letter_label(counters[level])})"
            else:
                label = "-"
            out.append(f"{base}{'  ' * level}{label} {body}")
            previous = level
        return "\n".join(out)

    @staticmethod
    def _latex_list(items: list[tuple[int, str]], style: str, base: str) -> str:
        env = "itemize" if style == LIST_STYLE_BULLET else "enumerate"
        out: list[str] = []
        depth = -1

        def open_env() -> None:
            nonlocal depth
            depth += 1
            out.append(f"{base}{'  ' * depth}\\begin{{{env}}}")
            if style == LIST_STYLE_LETTER:
                out.append(f"{base}{'  ' * depth}  {letter_label_command(depth)}")

        for level, body in items:
            level = min(level, depth + 1)
            while depth < level:
                open_env()
            while depth > level:
                out.append(f"{base}{'  ' * depth}\\end{{{env}}}")
                depth -= 1
            out.append(f"{base}{'  ' * depth}  \\item {body}")
        while depth >= 0:
            out.append(f"{base}{'  ' * depth}\\end{{{env}}}")
            depth -= 1
        return "\n".join(out)

    def toggle_code_block(self) -> None:
        """Racchiude le righe selezionate in un blocco di codice (o lo toglie se c'e' gia')."""
        if self._current_doc() is None or self.controller.is_running():
            return
        widget = self.editor._textbox
        latex = self._is_latex_doc()
        span = self._selected_lines_range()
        if span is None:
            # nessuna selezione: blocco vuoto su una nuova riga, con il cursore dentro
            opener, closer = ("\\begin{verbatim}", "\\end{verbatim}") if latex else ("```", "```")
            row = int(widget.index("insert").split(".")[0])
            widget.edit_separator()
            if widget.get("insert linestart", "insert lineend").strip():
                widget.insert("insert lineend", f"\n{opener}\n\n{closer}")
                row += 2
            else:
                widget.insert("insert linestart", f"{opener}\n\n{closer}")
                row += 1
            widget.edit_separator()
            widget.mark_set("insert", f"{row}.0")
            widget.see("insert")
            widget.focus_set()
            return
        text = widget.get(*span)
        lines = text.split("\n")
        if latex:
            wrapped = (
                len(lines) >= 2 and re.match(r"^\s*\\begin\{(verbatim|lstlisting|minted)\}", lines[0])
                and re.match(r"^\s*\\end\{(verbatim|lstlisting|minted)\}", lines[-1])
            )
            if wrapped:
                new_text = "\n".join(lines[1:-1])
                note = "Blocco di codice rimosso."
            else:
                new_text = "\\begin{verbatim}\n" + text + "\n\\end{verbatim}"
                note = "Blocco di codice creato (verbatim)."
        else:
            opening = FENCE_RE.match(lines[0]) if lines else None
            closing = FENCE_RE.match(lines[-1]) if len(lines) >= 2 else None
            if opening and closing and not closing.group(2):
                new_text = "\n".join(lines[1:-1])
                note = "Blocco di codice rimosso."
            else:
                fence = code_fence(text)
                new_text = f"{fence}\n{text}\n{fence}"
                note = "Blocco di codice creato."
        self._replace_range(span[0], span[1], new_text, select=False)
        self._flash_note(note)

    def insert_line_break(self) -> None:
        """Inserisce ``//`` (a capo senza nuovo paragrafo) al cursore."""
        if self._current_doc() is None or self.controller.is_running():
            return
        widget = self.editor._textbox
        widget.edit_separator()
        before = widget.get("insert -1c", "insert")
        widget.insert("insert", ("" if before in (" ", "\n", "") else " ") + "// ")
        widget.edit_separator()
        widget.focus_set()

    def remove_slide_markers(self) -> None:
        """Toglie i marcatori ``=== SLIDE N ===`` dal testo (annullabile con Ctrl+Z nell'editor)."""
        doc = self._current_doc()
        if doc is None or self.controller.is_running():
            return
        self._commit_editor()
        targets = [self.current]
        others = [
            index for index, item in enumerate(self.docs)
            if index != self.current and self._has_slide_markers(item.source)
        ]
        if others:
            answer = messagebox.askyesnocancel(
                "PDF e Slide",
                f"Togliere i marcatori === SLIDE N === anche dagli altri {len(others)} documenti?\n\n"
                "Si' = tutti i documenti, No = solo quello aperto.",
                parent=self,
            )
            if answer is None:
                return
            if answer:
                targets.extend(others)
        removed = 0
        for index in targets:
            item = self.docs[index]
            new_source, count = strip_slide_markers(item.source)
            if not count:
                continue
            removed += count
            if index == self.current:
                self._replace_editor_text(new_source)
            item.source = new_source
        if not removed:
            self._flash_note("Nessun marcatore === SLIDE === da togliere.", COLORS.text_muted)
            return
        message = f"Rimossi {removed} marcatori di slide."
        self._set_status(message)
        self._flash_note(message)
        self.schedule_render(immediate=True)
        self._update_buttons()
        self._schedule_draft_save()

    @staticmethod
    def _has_slide_markers(text: str) -> bool:
        return any(SLIDE_MARKER_RE.match(line) or LATEX_SLIDE_MARKER_RE.match(line) for line in text.split("\n"))

    # ------------------------------------------------------------------ Ctrl+S
    def _on_ctrl_s(self, _event=None):
        if not self.app.is_view_visible(self):
            return None
        self.save_now()
        return "break"

    def save_now(self) -> None:
        """Salva subito il documento aperto e lo conferma accanto al menu del documento."""
        doc = self._current_doc()
        if doc is None:
            self._flash_note("Nessun documento da salvare.", COLORS.text_muted)
            return
        if self._edit_job is not None:
            try:
                self.after_cancel(self._edit_job)
            except (tk.TclError, ValueError):
                pass
            self._edit_job = None
            self._after_edit()
        if not self._save_draft():
            self._flash_note("Salvataggio non riuscito: dettagli in studyflow.log.", COLORS.red)
            return
        message = f"✓ Salvato «{doc.path.name}» alle {time.strftime('%H:%M:%S')}"
        self._set_status(message)
        self._flash_note(message)

    # ------------------------------------------------------------------ rimozione testo
    def open_remove_dialog(self) -> None:
        if self._current_doc() is None or self.controller.is_running():
            return
        try:
            selected = self.editor._textbox.get("sel.first", "sel.last")
        except tk.TclError:
            selected = ""
        if self._remove_dialog is not None and self._remove_dialog.winfo_exists():
            if selected:
                self._remove_dialog.set_pattern(selected)
            self._remove_dialog.lift()
            self._remove_dialog.focus_force()
            return
        self._remove_dialog = RemoveTextDialog(self, initial=selected)

    def remove_text(self, rule: RemovalRule, indices: list[int]) -> tuple[list[tuple[int, str]], int, int]:
        """Rimuove il testo dai documenti indicati.

        Restituisce (istantanea per l'annullamento, occorrenze rimosse, documenti modificati).
        Sul documento aperto la modifica passa dall'editor, quindi anche Ctrl+Z la annulla.
        """
        self._commit_editor()
        snapshot: list[tuple[int, str]] = []
        removed = 0
        touched = 0
        for index in indices:
            doc = self.docs[index]
            new_source, count = remove_text(doc.source, rule)
            if not count:
                continue
            snapshot.append((index, doc.source))
            removed += count
            touched += 1
            if index == self.current:
                self._replace_editor_text(new_source)
            doc.source = new_source
        if removed:
            self.schedule_render(immediate=True)
            self._update_buttons()
            self._set_status(f"Rimosse {removed} occorrenze di testo.")
            self._flash_note(f"Rimosse {removed} occorrenze di testo.")
            self._schedule_draft_save()
        return snapshot, removed, touched

    def restore_sources(self, snapshot: list[tuple[int, str]]) -> None:
        for index, source in snapshot:
            if not (0 <= index < len(self.docs)):
                continue
            if index == self.current:
                self._replace_editor_text(source)
            self.docs[index].source = source
        self.schedule_render(immediate=True)
        self._update_buttons()
        self._set_status("Rimozione annullata.")
        self._schedule_draft_save()

    def _replace_editor_text(self, text: str) -> None:
        """Sostituisce il contenuto dell'editor come un'unica modifica annullabile."""
        widget = self.editor._textbox
        position = widget.yview()[0]
        self._suspend_modified = True
        widget.configure(autoseparators=False)
        try:
            widget.edit_separator()
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
            widget.edit_separator()
            widget.edit_modified(False)
        finally:
            widget.configure(autoseparators=True)
            self._suspend_modified = False
        widget.yview_moveto(position)

    def add_removal_rule(self, rule: RemovalRule) -> bool:
        if any(existing.to_dict() == rule.to_dict() for existing in self.removal_rules):
            return False
        self.removal_rules.append(rule)
        self._update_rules_label()
        self._save_settings()
        return True

    def delete_removal_rule(self, index: int) -> None:
        if 0 <= index < len(self.removal_rules):
            del self.removal_rules[index]
            self._update_rules_label()
            self._save_settings()

    def _update_rules_label(self) -> None:
        count = len(self.removal_rules)
        self.rules_switch.configure(
            text=f"Applica le regole di rimozione salvate ({count})" if count
            else "Applica le regole di rimozione salvate (nessuna)"
        )

    # ------------------------------------------------------------------ azioni rapide
    def _rendered_latex(self) -> str:
        doc = self._current_doc()
        if doc is None:
            return ""
        return render_source(doc.source, doc.fmt, FORMAT_TEX, self.render_options(doc))

    def copy_latex(self) -> None:
        self._commit_editor()
        latex = self._rendered_latex()
        if not latex:
            return
        self.clipboard_clear()
        self.clipboard_append(latex)
        self.app.toast("LaTeX copiato negli appunti.")

    def open_katex_preview(self) -> None:
        self._commit_editor()
        doc = self._current_doc()
        if doc is None:
            return
        try:
            path = write_html_preview(doc.source, doc.fmt, self.render_options(doc))
        except (ExportError, OSError) as exc:
            messagebox.showerror("PDF e Slide", str(exc), parent=self)
            return
        webbrowser.open(path.as_uri())
        self.app.toast("Anteprima KaTeX aperta nel browser (offline).")

    # ------------------------------------------------------------------ esportazione
    # ------------------------------------------------------------------ materia
    def selected_subject(self) -> str:
        value = self.subject_var.get().strip() if hasattr(self, "subject_var") else ""
        return "" if value in {"", NO_SUBJECT} else value

    def _refresh_subjects(self) -> None:
        try:
            tags = self.db.list_subject_tags()
        except Exception:
            tags = []
        current = self.selected_subject()
        values = [NO_SUBJECT, *tags]
        if current and current.casefold() not in {tag.casefold() for tag in tags}:
            values.append(current)
        self.subject_combo.configure(values=values)
        self.subject_combo.set(current or NO_SUBJECT)
        self._update_subject_hint()

    def _update_subject_hint(self) -> None:
        subject = self.selected_subject()
        if subject:
            text = f"Gli export vanno in Materie/{subject}/Appunti e compaiono nella pagina della materia."
        else:
            text = "Scegli una materia per salvare gli export nella sua cartella e collegarli alla materia."
        self.subject_hint.configure(text=text)

    def _on_subject_change(self, *_args) -> None:
        subject = self.selected_subject()
        if subject:
            self.app.subject_folder(subject)
        self._update_subject_hint()
        self._save_settings()

    def _link_exports(self, paths: list[Path]) -> None:
        """Collega i file esportati alla materia scelta (sezione Materie)."""
        subject = self.selected_subject()
        if not subject:
            return
        for path in paths:
            if not path.is_file():
                continue
            try:
                self.app.register_generated_material(path, subject, path.name)
            except Exception:
                LOGGER.exception("Collegamento dell'export alla materia non riuscito.")

    def _export_initial_dir(self, doc: DocState) -> str:
        subject = self.selected_subject()
        if subject:
            folder = self.app.subject_folder(subject, KIND_NOTES)
            if folder is not None:
                return str(folder)
        if self._export_dir and Path(self._export_dir).is_dir():
            return self._export_dir
        return str(doc.path.parent)

    def export_current(self, fmt: str) -> None:
        self._commit_editor()
        doc = self._current_doc()
        if doc is None or not doc.source.strip():
            return
        labels = {FORMAT_TXT: "Testo", FORMAT_TEX: "LaTeX", FORMAT_DOCX: "Microsoft Word"}
        target = filedialog.asksaveasfilename(
            parent=self, title=f"Esporta .{fmt}", defaultextension=f".{fmt}",
            initialdir=self._export_initial_dir(doc), initialfile=f"{doc.path.stem}.{fmt}",
            filetypes=[(labels[fmt], f"*.{fmt}")],
        )
        if not target:
            return
        if not self.selected_subject():
            self._export_dir = str(Path(target).parent)
        self._save_settings()
        source, source_format, options = doc.source, doc.fmt, self.render_options(doc)
        if fmt != FORMAT_DOCX:
            try:
                export_file(source, source_format, fmt, target, options)
            except (ExportError, OSError) as exc:
                messagebox.showerror("PDF e Slide", f"Esportazione non riuscita:\n{exc}", parent=self)
                return
            self._link_exports([Path(target)])
            self._set_status(f"Esportato: {Path(target).name}")
            self.app.toast(f"Esportato {Path(target).name}")
            return

        def task(progress, cancel):
            progress(0.3, "Conversione Word con Pandoc…")
            return export_file(source, source_format, fmt, target, options)

        def done(_event: dict[str, Any]) -> None:
            self._link_exports([Path(target)])
            message = f"Esportato: {Path(target).name}"
            self._set_status(message)
            self.progress.set(1.0)
            self.app.toast(message)

        self._run_task(task, f"Esportato: {Path(target).name}", done)

    def export_all(self) -> None:
        self._commit_editor()
        ready = [doc for doc in self.docs if doc.source.strip()]
        if not ready:
            return
        folder = filedialog.askdirectory(
            parent=self, title="Cartella di destinazione", initialdir=self._export_initial_dir(ready[0]),
        )
        if not folder:
            return
        if not self.selected_subject():
            self._export_dir = folder
        self._save_settings()
        formats = BATCH_FORMAT_CHOICES.get(self.batch_format_var.get(), [FORMAT_DOCX])
        jobs = [(doc, fmt, Path(folder) / f"{doc.path.stem}.{fmt}") for doc in ready for fmt in formats]
        existing = [target for _, _, target in jobs if target.exists()]
        if existing and not messagebox.askyesno(
            "PDF e Slide",
            f"{len(existing)} file esistono gia' nella cartella e verranno sovrascritti.\nContinuare?",
            parent=self,
        ):
            return
        snapshots = [(doc.source, doc.fmt, self.render_options(doc), fmt, target) for doc, fmt, target in jobs]

        def task(progress, cancel):
            written = 0
            written_paths: list[str] = []
            errors: list[str] = []
            for index, (source, source_format, options, fmt, target) in enumerate(snapshots):
                if cancel.is_set():
                    raise PdfCancelled("Esportazione annullata.")
                progress(index / len(snapshots), f"Esporto {target.name} ({index + 1}/{len(snapshots)})")
                try:
                    export_file(source, source_format, fmt, target, options)
                    written += 1
                    written_paths.append(str(target))
                except (ExportError, OSError) as exc:
                    errors.append(f"{target.name}: {exc}")
            if errors and not written:
                raise ExportError("\n".join(errors[:5]))
            return {"written": written, "errors": errors, "folder": folder, "paths": written_paths}

        def done(event: dict[str, Any]) -> None:
            result = event.get("result") or {}
            self._link_exports([Path(value) for value in result.get("paths") or []])
            message = f"Esportati {result.get('written', 0)} file in {Path(folder).name}."
            if result.get("errors"):
                message += f" {len(result['errors'])} errori."
                messagebox.showwarning("PDF e Slide", "\n".join(result["errors"][:8]), parent=self)
            self._set_status(message)
            self.progress.set(1.0)
            self.app.toast(message)

        self._run_task(task, "", done)

    def _run_task(self, task, success_message: str, done: Callable[[dict[str, Any]], None] | None = None) -> None:
        def default_done(_event: dict[str, Any]) -> None:
            self._set_status(success_message)
            self.progress.set(1.0)
            self.app.toast(success_message)

        ok, error = self.controller.run_task(task)
        if not ok:
            messagebox.showwarning("PDF e Slide", error, parent=self)
            return
        self._task_done = done or default_done
        self._task_label = "task"
        self.progress.set(0)
        self._set_status("Esportazione in corso…")
        self._update_buttons()
        self._start_polling()

    # ------------------------------------------------------------------ struttura LaTeX e template
    def _ask_structure_for_import(self) -> dict[str, Any] | None:
        multiple = len(self.sources) > 1
        proposed = "" if multiple else _pretty_name(self.sources[0])
        dialog = LatexStructureDialog(
            self, self._latex_defaults, proposed, multiple=multiple,
            heading="Importazione: struttura LaTeX", confirm_text="Estrai",
        )
        result = dialog.show()
        if result is None:
            return None
        result.pop("apply_all", None)
        self._latex_defaults = {key: result[key] for key in ("numbered", "embed", "base_level")}
        return result

    def edit_structure(self) -> None:
        doc = self._current_doc()
        if doc is None:
            return
        current = dict(self._latex_defaults)
        current.update(doc.latex)
        proposed = str(current.get("root_title") or "") or _pretty_name(doc.path)
        dialog = LatexStructureDialog(
            self, current, proposed, heading="Struttura LaTeX", confirm_text="Applica",
            allow_apply_all=len(self.docs) > 1,
        )
        result = dialog.show()
        if result is None:
            return
        apply_all = result.pop("apply_all", False)
        self._latex_defaults = {key: result[key] for key in ("numbered", "embed", "base_level")}
        for target in (self.docs if apply_all else [doc]):
            values = dict(result)
            if target is not doc:
                values["root_title"] = target.latex.get("root_title", "")
            target.latex = values
        self._save_settings()
        self.schedule_render(immediate=True)
        self._update_buttons()
        self._schedule_draft_save()

    def _update_structure_label(self) -> None:
        doc = self._current_doc()
        if doc is None:
            self.structure_label.configure(text="Viene chiesta a ogni importazione.")
            return
        options = self.render_options(doc)
        numbered = "numerate" if options.numbered_sections else "non numerate"
        if options.embed:
            star = "" if options.numbered_sections else "*"
            text = f"Inglobato, sezioni {numbered}: \\{options.base_level}{star}{{{options.root_title}}}"
        else:
            text = f"Documento autonomo, sezioni {numbered}."
        self.structure_label.configure(text=text)

    def _load_template_text(self) -> None:
        self._template_text = ""
        self._template_error = ""
        if not self._template_path:
            return
        path = Path(self._template_path)
        try:
            raw = path.read_bytes()
        except OSError:
            self._template_error = "file non trovato"
            return
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        if "\\begin{document}" not in text:
            self._template_error = "manca \\begin{document}"
            return
        self._template_text = text

    def template_name(self) -> str:
        return Path(self._template_path).name if self._template_path and self._template_text else ""

    def _update_template_label(self) -> None:
        if not hasattr(self, "template_status"):
            return
        if not self._template_path:
            text, color = "Nessuno: si usa il preambolo standard.", COLORS.text_faint
        elif self._template_error:
            text, color = f"{Path(self._template_path).name}: {self._template_error}.", COLORS.amber
        else:
            text, color = (
                f"{Path(self._template_path).name} (usato con «Preambolo LaTeX» attivo, non per l'inglobazione).",
                COLORS.text_muted,
            )
        self.template_status.configure(text=text, text_color=color)

    def choose_template(self, parent=None) -> None:
        initial = Path(self._template_path).parent if self._template_path else Path(self._initial_dir())
        name = filedialog.askopenfilename(
            parent=parent or self, title="Scegli il template LaTeX",
            initialdir=str(initial) if initial.is_dir() else self._initial_dir(),
            filetypes=[("LaTeX", "*.tex"), ("Tutti i file", "*.*")],
        )
        if not name:
            return
        previous = (self._template_path, self._template_text, self._template_error)
        self._template_path = name
        self._load_template_text()
        if self._template_error:
            messagebox.showwarning(
                "PDF e Slide", f"Template non valido ({self._template_error}).", parent=parent or self,
            )
            self._template_path, self._template_text, self._template_error = previous
            return
        try:
            apply_template(self._template_text, "\\section{Prova}\n", RenderOptions())
        except ExportError as exc:
            messagebox.showwarning("PDF e Slide", f"Template non valido: {exc}", parent=parent or self)
            self._template_path, self._template_text, self._template_error = previous
            return
        self._save_settings()
        self._update_template_label()
        self._update_buttons()
        self.schedule_render(immediate=True)
        self.app.toast(f"Template: {Path(name).name}")

    def clear_template(self) -> None:
        self._template_path = ""
        self._load_template_text()
        self._save_settings()
        self._update_template_label()
        self._update_buttons()
        self.schedule_render(immediate=True)

    # ------------------------------------------------------------------ salvataggio temporaneo
    def _draft_exists(self) -> bool:
        return self._draft_path is not None and self._draft_path.is_file()

    def _is_extracting(self) -> bool:
        return self.controller.is_running() and self._task_label == "extract"

    def _schedule_draft_save(self) -> None:
        if self._draft_path is None:
            return
        if self._draft_job is not None:
            try:
                self.after_cancel(self._draft_job)
            except (tk.TclError, ValueError):
                pass
        self._draft_job = self.after(DRAFT_DEBOUNCE_MS, self._save_draft)

    def _cancel_draft_job(self) -> None:
        if self._draft_job is not None:
            try:
                self.after_cancel(self._draft_job)
            except (tk.TclError, ValueError):
                pass
            self._draft_job = None

    def _save_draft(self, force: bool = False, notify: bool = False) -> bool:
        """Scrive la bozza su disco con tutti i documenti aperti.

        Un'estrazione non svuota piu' l'elenco dei documenti: si puo' salvare
        anche mentre e' in corso senza perdere quelli gia' aperti.
        """
        self._cancel_draft_job()
        if self._draft_path is None:
            return False
        self._commit_editor()
        if not self.docs:
            # Nessun documento: niente da salvare. Una bozza gia' presente resta
            # su disco finche' non si preme «Elimina».
            return False
        payload = [
            {
                "path": str(doc.path),
                "title": doc.title,
                "source": doc.source,
                "fmt": doc.fmt,
                "warnings": list(doc.warnings),
                "engines": dict(doc.engines),
                "error": doc.error,
                "latex": dict(doc.latex),
                "block_id": doc.block_id,
                "cursor": doc.cursor,
            }
            for doc in self.docs
        ]
        for doc in self.docs:
            self._sync_block(doc)
        try:
            self._draft_saved_at = save_draft(
                self._draft_path, payload, self.current, [str(path) for path in self.sources]
            )
        except Exception as exc:
            LOGGER.exception("Salvataggio bozza PDF non riuscito.")
            if notify:
                messagebox.showerror("PDF e Slide", f"Salvataggio della bozza non riuscito:\n{exc}", parent=self)
            return False
        self._update_draft_label()
        self._update_buttons()
        if notify:
            self.app.toast("Bozza salvata")
            self._flash_note(f"✓ Bozza salvata alle {time.strftime('%H:%M:%S')}")
        return True

    def _update_draft_label(self) -> None:
        if not hasattr(self, "draft_label"):
            return
        if not self._draft_saved_at or not self._draft_exists():
            self.draft_label.configure(
                text="Nessuna bozza salvata. Le modifiche vengono salvate in automatico.",
                text_color=COLORS.text_faint,
            )
            return
        stamp = time.localtime(self._draft_saved_at)
        if time.strftime("%Y-%m-%d", stamp) == time.strftime("%Y-%m-%d"):
            when = time.strftime("alle %H:%M", stamp)
        else:
            when = time.strftime("il %d/%m alle %H:%M", stamp)
        count = len(self.docs)
        docs = "1 documento" if count == 1 else f"{count} documenti"
        self.draft_label.configure(
            text=f"Bozza salvata {when} ({docs}). Resta disponibile anche dopo la chiusura dell'app.",
            text_color=COLORS.text_muted,
        )

    def _restore_draft(self) -> None:
        if self._draft_path is None:
            self._update_draft_label()
            return
        try:
            data = load_draft(self._draft_path)
        except Exception:
            LOGGER.exception("Lettura bozza PDF non riuscita.")
            data = None
        if not data:
            self._update_draft_label()
            return
        self.docs = [
            DocState(
                path=Path(raw["path"]),
                title=raw["title"],
                source=raw["source"],
                fmt=SOURCE_LATEX if raw["fmt"] == SOURCE_LATEX else SOURCE_MARKDOWN,
                warnings=list(raw["warnings"]),
                engines=dict(raw["engines"]),
                error=raw["error"],
                latex=dict(raw.get("latex") or {}),
                cursor=str(raw.get("cursor") or "1.0"),
            )
            for raw in data["docs"]
        ]
        for doc, raw in zip(self.docs, data["docs"]):
            block_id = raw.get("block_id")
            if not block_id:
                continue
            try:
                row = self.db.get_note_block(int(block_id))
            except Exception:
                row = None
            if row is not None:
                self._apply_block_row(doc, row)
        self.sources = [Path(item) for item in data["sources"] if Path(item).is_file()]
        self._draft_saved_at = float(data.get("saved_at") or 0.0)
        self.current = -1
        self._update_source_label()
        self._refresh_doc_menu()
        self._select_doc(int(data["current"]))
        self._cancel_draft_job()
        self._update_draft_label()
        self._set_status("Bozza ripristinata: puoi continuare da dove eri rimasto.")

    def delete_draft(self) -> None:
        if self.controller.is_running():
            return
        if not messagebox.askyesno(
            "PDF e Slide",
            "Eliminare la bozza salvata?\n\nIl testo estratto e tutte le modifiche manuali verranno "
            "rimossi dall'editor. I capitoli collegati a una materia vengono prima salvati e chiusi; "
            "i file gia' esportati non vengono toccati.",
            icon="warning",
            parent=self,
        ):
            return
        self._cancel_draft_job()
        self._commit_editor()
        for doc in self.docs:
            self._sync_block(doc, status="closed")
        if self._edit_job is not None:
            try:
                self.after_cancel(self._edit_job)
            except (tk.TclError, ValueError):
                pass
            self._edit_job = None
        try:
            if self._draft_path is not None:
                delete_draft_file(self._draft_path)
        except Exception as exc:
            LOGGER.exception("Eliminazione bozza PDF non riuscita.")
            messagebox.showerror("PDF e Slide", f"Eliminazione della bozza non riuscita:\n{exc}", parent=self)
            return
        self._draft_saved_at = 0.0
        self.docs = []
        self.current = -1
        self._set_editor_text("")
        self.source_mode_var.set(SOURCE_MODES[0])
        self._update_editor_title()
        self.info_label.configure(text="")
        self._refresh_doc_menu()
        self._show_placeholder()
        self.progress.set(0)
        self._set_status("Bozza eliminata.")
        self._update_draft_label()
        self._update_buttons()
        self.app.toast("Bozza eliminata")

    # ------------------------------------------------------------------ ciclo di vita
    # ------------------------------------------------------------------ PDF originale
    def toggle_source_pane(self) -> None:
        if self._source_visible:
            self._hide_source_pane()
        else:
            self._show_source_pane()
        self._save_settings()

    def _show_source_pane(self) -> None:
        if self._source_visible:
            return
        self._source_visible = True
        self._show_source = True
        self.outer_paned.add(self.source_viewer, before=self.paned, minsize=260, stretch="always")
        self.outer_paned.update_idletasks()
        total = self.outer_paned.winfo_width()
        if total > 600:
            # Circa un terzo dello spazio alla slide: editor e anteprima restano leggibili.
            self.outer_paned.sash_place(0, int(total * 0.4), 0)
        self.source_button.configure(text="📄  Chiudi PDF", fg_color=COLORS.accent_dark, text_color=COLORS.text)
        self._source_synced_page = None
        self._load_source_pdf()

    def _hide_source_pane(self) -> None:
        if not self._source_visible:
            return
        self._source_visible = False
        self._show_source = False
        self.outer_paned.forget(self.source_viewer)
        self.source_button.configure(text="📄  PDF originale", fg_color="transparent", text_color=COLORS.text_muted)

    def detach_source_pane(self) -> None:
        """Sposta il PDF in una finestra separata (utile con due schermi)."""
        path = self.source_viewer.path
        page = self.source_viewer.current_page()
        self._hide_source_pane()
        self._save_settings()
        if path is not None:
            open_pdf_window(self, self.app, path, page)

    def _load_source_pdf(self) -> None:
        if not self._source_visible:
            return
        doc = self._current_doc()
        if doc is None:
            self.source_viewer.close_document()
            self.source_viewer.show_message("Importa un PDF per vederlo qui accanto all'editor.")
            return
        path = Path(doc.path)
        if path.suffix.lower() != ".pdf" or not path.exists():
            self.source_viewer.close_document()
            self.source_viewer.show_message(f"PDF originale non trovato:\n{path}")
            return
        if self.source_viewer.path != path:
            self._source_synced_page = None
        self.source_viewer.load(path, self._cursor_slide_page())

    def _cursor_slide_page(self) -> int | None:
        """Pagina (0-based) del marcatore ``=== SLIDE N ===`` che precede il cursore."""
        widget = self.editor._textbox
        try:
            line = int(widget.index("insert").split(".")[0])
        except (tk.TclError, ValueError):
            return None
        lines = widget.get("1.0", f"{line}.end").splitlines()
        for text in reversed(lines):
            match = _ANY_SLIDE_MARKER_RE.match(text)
            if match:
                return max(0, int(match.group(1)) - 1)
        return None

    def _schedule_source_sync(self, _event=None) -> None:
        if not self._source_visible:
            return
        if self._source_sync_job is not None:
            try:
                self.after_cancel(self._source_sync_job)
            except (tk.TclError, ValueError):
                pass
        self._source_sync_job = self.after(SOURCE_SYNC_DEBOUNCE_MS, self._sync_source_to_cursor)

    def _sync_source_to_cursor(self) -> None:
        self._source_sync_job = None
        page = self._cursor_slide_page()
        # Si sposta solo quando il cursore passa a un'altra slide: la navigazione
        # manuale nel PDF non viene annullata mentre si scrive.
        if page is None or page == self._source_synced_page:
            return
        self._source_synced_page = page
        self.source_viewer.show_page(page)

    def on_theme_changed(self) -> None:
        """Formule e tag dell'anteprima dipendono dai colori del tema."""
        self.source_viewer.on_theme_changed()
        self._renderer = None
        self._configure_preview_tags()
        if self._current_doc() is None:
            self._show_placeholder()
        else:
            self.schedule_render(immediate=True)

    def on_show(self) -> None:
        self._refresh_subjects()
        self._update_buttons()
        if self.controller.is_running():
            self._start_polling()

    def on_hide(self) -> None:
        if self._remove_dialog is not None and self._remove_dialog.winfo_exists():
            self._remove_dialog.close()
        self._commit_editor()
        self._save_settings()
        self._save_draft()

    def shutdown(self) -> None:
        try:
            self._commit_editor()
            # Se l'app si chiude durante un'estrazione si salva quanto gia' arrivato.
            if self.docs:
                self._save_draft(force=True)
        except Exception:
            LOGGER.exception("Salvataggio bozza PDF in chiusura non riuscito.")
        try:
            self.controller.shutdown()
        except Exception:
            LOGGER.exception("Chiusura conversione PDF non riuscita.")
