"""Visualizzatore PDF integrato, una slide alla volta.

Le dispense con piu' slide per pagina (handout Beamer, stampe 2/4 per pagina)
vengono divise automaticamente: ogni slide occupa da sola l'area di
visualizzazione. La divisione si puo' anche forzare (1, 2, 4, 6 per pagina) e
viene ricordata per ogni file.

Usato in due punti:

- ``PdfViewerWindow``: finestra autonoma, aperta dai materiali delle materie;
- ``PdfViewer`` incorporato nel pannello di "PDF e Slide", accanto all'editor.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Any, Callable

import customtkinter as ctk

from ..config import COLORS
from ..services.pdf_tools.viewer import (
    LAYOUT_AUTO,
    PageInfo,
    SlideRegion,
    analyze_document,
    build_regions,
    region_for_page,
    render_region,
)
from .theme import font
from .widgets import center_window, set_window_icon


LOGGER = logging.getLogger(__name__)

LAYOUT_LABELS = {
    "Auto": LAYOUT_AUTO,
    "1 per pagina": "1",
    "2 per pagina": "2",
    "4 per pagina": "4",
    "6 per pagina": "6",
}
FIT_MODES = ("Pagina", "Larghezza")
ZOOM_RANGE = (50, 400)
ZOOM_STEP = 10
CANVAS_MARGIN = 12
FIT_TOLERANCE = 6
MAX_IMAGE_SIDE = 7000
RESIZE_DEBOUNCE_MS = 70
WHEEL_FLIP_COOLDOWN = 0.28
LAYOUTS_SETTING = "pdf_viewer_layouts"
FIT_SETTING = "pdf_viewer_fit"
MAX_REMEMBERED_LAYOUTS = 300


def _label_for_layout(value: str) -> str:
    for label, key in LAYOUT_LABELS.items():
        if key == value:
            return label
    return next(iter(LAYOUT_LABELS))


def is_pdf(path: str | Path) -> bool:
    return str(path).lower().endswith(".pdf")


class PdfViewer(ctk.CTkFrame):
    """Mostra un PDF una slide alla volta, con navigazione, zoom e divisione delle pagine."""

    def __init__(
        self,
        master,
        app=None,
        on_close: Callable[[], None] | None = None,
        on_detach: Callable[[], None] | None = None,
        compact: bool = False,
        **kwargs,
    ):
        kwargs.setdefault("fg_color", COLORS.surface_alt)
        kwargs.setdefault("corner_radius", 10)
        super().__init__(master, **kwargs)
        self.app = app
        self._on_close = on_close
        self._on_detach = on_detach
        self._compact = compact
        self.path: Path | None = None
        self._document: Any = None
        self._pages: list[PageInfo] = []
        self.regions: list[SlideRegion] = []
        self.index = 0
        self._layout = LAYOUT_AUTO
        self._zoom = 100
        self._image: tk.PhotoImage | None = None
        self._generation = 0
        self._analysis: dict[str, Any] | None = None
        self._poll_job: str | None = None
        self._resize_job: str | None = None
        self._last_flip = 0.0
        self._pending_page: int | None = None
        self._canvas_size = (0, 0)

        self.layout_var = ctk.StringVar(value=_label_for_layout(LAYOUT_AUTO))
        self.fit_var = ctk.StringVar(value=self._setting(FIT_SETTING, FIT_MODES[0]))
        if self.fit_var.get() not in FIT_MODES:
            self.fit_var.set(FIT_MODES[0])

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_toolbar()
        self._build_canvas()
        self._update_controls()
        self.show_message("Nessun PDF aperto.")

    # ------------------------------------------------------------------ impostazioni
    def _setting(self, key: str, default: str = "") -> str:
        db = getattr(self.app, "db", None)
        if db is None:
            return default
        try:
            return db.get_setting(key, default) or default
        except Exception:
            return default

    def _set_setting(self, key: str, value: str) -> None:
        db = getattr(self.app, "db", None)
        if db is None:
            return
        try:
            db.set_setting(key, value)
        except Exception:
            LOGGER.exception("Salvataggio dell'impostazione %s non riuscito.", key)

    def _remembered_layouts(self) -> dict[str, str]:
        try:
            value = json.loads(self._setting(LAYOUTS_SETTING, "{}"))
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _remember_layout(self) -> None:
        if self.path is None:
            return
        layouts = self._remembered_layouts()
        key = str(self.path.resolve()).lower()
        layouts.pop(key, None)
        if self._layout != LAYOUT_AUTO:
            layouts[key] = self._layout
        while len(layouts) > MAX_REMEMBERED_LAYOUTS:
            layouts.pop(next(iter(layouts)))
        self._set_setting(LAYOUTS_SETTING, json.dumps(layouts))

    # ------------------------------------------------------------------ interfaccia
    def _icon_button(self, parent, text: str, command, width: int = 30) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, width=width, height=28, font=font(12, "bold"), fg_color="transparent",
            hover_color=COLORS.surface_hover, border_width=1, border_color=COLORS.border,
            text_color=COLORS.text, corner_radius=7, command=command,
        )

    def _build_toolbar(self) -> None:
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        top.grid_columnconfigure(5, weight=1)
        self.prev_button = self._icon_button(top, "‹", self.previous)
        self.prev_button.grid(row=0, column=0, padx=(0, 4))
        self.slide_entry = ctk.CTkEntry(
            top, width=48, height=28, font=font(11), justify="center", fg_color=COLORS.surface,
            border_color=COLORS.border, text_color=COLORS.text,
        )
        self.slide_entry.grid(row=0, column=1)
        self.slide_entry.bind("<Return>", self._on_entry)
        self.slide_entry.bind("<FocusOut>", lambda _e: self._update_controls())
        self.total_label = ctk.CTkLabel(top, text="/ 0", font=font(11), text_color=COLORS.text_muted)
        self.total_label.grid(row=0, column=2, padx=(4, 4))
        self.next_button = self._icon_button(top, "›", self.next)
        self.next_button.grid(row=0, column=3, padx=(0, 8))
        self.page_label = ctk.CTkLabel(top, text="", font=font(10), text_color=COLORS.text_faint, anchor="w")
        self.page_label.grid(row=0, column=4, sticky="w")
        self.title_label = ctk.CTkLabel(top, text="", font=font(10, "bold"), text_color=COLORS.text_faint, anchor="e")
        if not self._compact:
            # Nel pannello di "PDF e Slide" il nome del file e' gia' nel menu Documento.
            self.title_label.grid(row=0, column=5, sticky="ew", padx=(8, 4))
        column = 6
        if self._on_detach is not None:
            self._icon_button(top, "⧉", self._on_detach).grid(row=0, column=column, padx=(4, 0))
            column += 1
        if self._on_close is not None:
            self._icon_button(top, "✕", self._on_close).grid(row=0, column=column, padx=(4, 0))

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=1, column=0, sticky="ew", padx=10, pady=(2, 6))
        bottom.grid_columnconfigure(3, weight=1)
        self.layout_menu = ctk.CTkOptionMenu(
            bottom, values=list(LAYOUT_LABELS), variable=self.layout_var, command=self._on_layout,
            width=118, height=28, dynamic_resizing=False, font=font(11), dropdown_font=font(11),
            fg_color=COLORS.surface, button_color=COLORS.surface_hover, button_hover_color=COLORS.border,
            text_color=COLORS.text, dropdown_fg_color=COLORS.surface, dropdown_hover_color=COLORS.surface_hover,
            dropdown_text_color=COLORS.text,
        )
        self.layout_menu.grid(row=0, column=0, sticky="w")
        self.fit_toggle = ctk.CTkSegmentedButton(
            bottom, values=list(FIT_MODES), variable=self.fit_var, command=self._on_fit, font=font(11), height=28,
            fg_color=COLORS.surface, selected_color=COLORS.accent_dark, selected_hover_color=COLORS.accent_dark,
            unselected_color=COLORS.surface, unselected_hover_color=COLORS.surface_hover, text_color=COLORS.text,
        )
        self.fit_toggle.grid(row=0, column=1, sticky="w", padx=(6, 0))
        zoom = ctk.CTkFrame(bottom, fg_color="transparent")
        zoom.grid(row=0, column=2, sticky="w", padx=(6, 0))
        self._icon_button(zoom, "−", lambda: self.zoom(-1), width=28).pack(side="left")
        self.zoom_label = ctk.CTkButton(
            zoom, text="100%", width=42, height=28, font=font(10), fg_color="transparent",
            hover_color=COLORS.surface_hover, text_color=COLORS.text_muted, command=lambda: self.zoom(0),
        )
        self.zoom_label.pack(side="left", padx=2)
        self._icon_button(zoom, "+", lambda: self.zoom(1), width=28).pack(side="left")
        self.external_button = ctk.CTkButton(
            bottom, text="↗" if self._compact else "Apri esterno", width=30 if self._compact else 96, height=28, font=font(10), fg_color="transparent",
            hover_color=COLORS.surface_hover, border_width=1, border_color=COLORS.border,
            text_color=COLORS.text_muted, corner_radius=7, command=self.open_external,
        )
        self.external_button.grid(row=0, column=4, sticky="e")

    def _build_canvas(self) -> None:
        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.grid(row=2, column=0, sticky="nsew", padx=6, pady=(0, 6))
        holder.grid_columnconfigure(0, weight=1)
        holder.grid_rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            holder, bg=COLORS.surface, highlightthickness=0, bd=0, takefocus=1,
            xscrollincrement=1, yscrollincrement=1,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar = ctk.CTkScrollbar(
            holder, command=self.canvas.yview, button_color=COLORS.border, button_hover_color=COLORS.surface_hover,
        )
        self.hbar = ctk.CTkScrollbar(
            holder, orientation="horizontal", command=self.canvas.xview, button_color=COLORS.border,
            button_hover_color=COLORS.surface_hover,
        )
        self.canvas.configure(yscrollcommand=self._yscroll, xscrollcommand=self._xscroll)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Button-1>", lambda _e: self.canvas.focus_set())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_shift_wheel)
        self.canvas.bind("<Control-MouseWheel>", lambda e: (self.zoom(1 if e.delta > 0 else -1), "break")[1])
        for sequence in ("<Left>", "<Up>", "<Prior>", "<BackSpace>"):
            self.canvas.bind(sequence, lambda _e: (self.previous(), "break")[1])
        for sequence in ("<Right>", "<Down>", "<Next>", "<space>"):
            self.canvas.bind(sequence, lambda _e: (self.next(), "break")[1])
        self.canvas.bind("<Home>", lambda _e: (self.go_to(0), "break")[1])
        self.canvas.bind("<End>", lambda _e: (self.go_to(len(self.regions) - 1), "break")[1])
        for sequence, step in (("<Control-plus>", 1), ("<Control-equal>", 1), ("<Control-minus>", -1), ("<Control-0>", 0)):
            self.canvas.bind(sequence, lambda _e, value=step: (self.zoom(value), "break")[1])

    def _yscroll(self, first, last) -> None:
        self.vbar.set(first, last)
        self._toggle_bar(self.vbar, float(last) - float(first) < 0.999, relx=1.0, rely=0.0, relheight=1.0, anchor="ne")

    def _xscroll(self, first, last) -> None:
        self.hbar.set(first, last)
        self._toggle_bar(self.hbar, float(last) - float(first) < 0.999, relx=0.0, rely=1.0, relwidth=1.0, anchor="sw")

    @staticmethod
    def _toggle_bar(bar, visible: bool, **place) -> None:
        # Le barre sono sovrapposte al canvas: mostrarle non ne cambia la
        # dimensione (altrimenti adattamento e barre si rincorrerebbero).
        if visible and not bar.winfo_ismapped():
            bar.place(**place)
        elif not visible and bar.winfo_ismapped():
            bar.place_forget()

    def on_theme_changed(self) -> None:
        try:
            self.canvas.configure(bg=COLORS.surface)
        except tk.TclError:
            pass
        if not self.regions:
            self.show_message(self._message or "")

    # ------------------------------------------------------------------ documento
    def load(self, path: str | Path, page: int | None = None) -> None:
        """Apre ``path``; ``page`` (0-based) indica la pagina da mostrare per prima."""
        target = Path(path)
        if self.path is not None and target == self.path and self._document is not None:
            if page is not None:
                self.show_page(page)
            return
        self.close_document()
        self.path = target
        self._pending_page = page
        stored = self._remembered_layouts().get(str(target.resolve()).lower(), LAYOUT_AUTO)
        self._layout = stored if stored in LAYOUT_LABELS.values() else LAYOUT_AUTO
        self.layout_var.set(_label_for_layout(self._layout))
        self.title_label.configure(text=target.name)
        try:
            import fitz  # PyMuPDF

            self._document = fitz.open(str(target))
        except Exception as exc:
            LOGGER.exception("Apertura del PDF %s non riuscita.", target)
            self._document = None
            self.show_message(f"Impossibile aprire il PDF:\n{exc}")
            self._update_controls()
            return
        self._start_analysis()

    def close_document(self) -> None:
        self._generation += 1
        if self._analysis is not None:
            self._analysis["cancel"] = True
        self._analysis = None
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except (tk.TclError, ValueError):
                pass
            self._poll_job = None
        if self._document is not None:
            try:
                self._document.close()
            except Exception:
                pass
        self._document = None
        self._pages = []
        self.regions = []
        self.index = 0
        self.path = None
        self._image = None
        self.title_label.configure(text="")
        self._update_controls()

    def _start_analysis(self) -> None:
        generation = self._generation
        state: dict[str, Any] = {"cancel": False, "done": False, "pages": None, "error": "", "progress": (0, 0)}
        self._analysis = state
        path = str(self.path)
        self.show_message("Analisi delle pagine…")

        def worker() -> None:
            try:
                state["pages"] = analyze_document(
                    path,
                    cancel=lambda: bool(state["cancel"]),
                    progress=lambda done, total: state.__setitem__("progress", (done, total)),
                )
            except Exception as exc:  # pragma: no cover - dipende dal file
                LOGGER.exception("Analisi del PDF %s non riuscita.", path)
                state["error"] = str(exc)
            state["done"] = True

        threading.Thread(target=worker, name="pdf-viewer-analysis", daemon=True).start()
        self._poll_job = self.after(40, lambda: self._poll_analysis(generation))

    def _poll_analysis(self, generation: int) -> None:
        self._poll_job = None
        state = self._analysis
        if generation != self._generation or state is None:
            return
        if not state["done"]:
            done, total = state["progress"]
            if total:
                self.show_message(f"Analisi delle pagine… {done}/{total}")
            self._poll_job = self.after(60, lambda: self._poll_analysis(generation))
            return
        self._analysis = None
        if state["error"] or state["pages"] is None:
            self.show_message(f"Impossibile analizzare il PDF:\n{state['error']}")
            return
        self._pages = state["pages"]
        self._rebuild_regions(keep_page=self._pending_page)
        self._pending_page = None

    def _rebuild_regions(self, keep_page: int | None = None) -> None:
        if keep_page is None and self.regions:
            keep_page = self.regions[self.index].page
        self.regions = build_regions(self._pages, self._layout)
        self.index = region_for_page(self.regions, keep_page) if keep_page is not None else 0
        if not self.regions:
            self.show_message("Il PDF non contiene pagine.")
        self._update_controls()
        self.render()

    # ------------------------------------------------------------------ navigazione
    def go_to(self, index: int, anchor: str = "top") -> None:
        if not self.regions:
            return
        index = max(0, min(len(self.regions) - 1, index))
        if index == self.index and self._image is not None:
            return
        self.index = index
        self._update_controls()
        self.render(anchor=anchor)

    def next(self) -> None:
        self.go_to(self.index + 1)

    def previous(self) -> None:
        self.go_to(self.index - 1)

    def show_page(self, page: int) -> None:
        """Mostra la prima slide della pagina ``page`` (0-based)."""
        if not self.regions:
            self._pending_page = page
            return
        self.go_to(region_for_page(self.regions, page))

    def _on_entry(self, _event=None) -> str:
        try:
            value = int(self.slide_entry.get().strip())
        except ValueError:
            self._update_controls()
            return "break"
        self.go_to(value - 1)
        self._update_controls()
        self.canvas.focus_set()
        return "break"

    def _on_layout(self, label: str) -> None:
        self._layout = LAYOUT_LABELS.get(label, LAYOUT_AUTO)
        self._remember_layout()
        if self._pages:
            self._rebuild_regions()

    def _on_fit(self, value: str) -> None:
        self._set_setting(FIT_SETTING, value)
        self.render()

    def zoom(self, step: int) -> None:
        value = 100 if step == 0 else self._zoom + step * ZOOM_STEP
        value = max(ZOOM_RANGE[0], min(ZOOM_RANGE[1], value))
        if value == self._zoom and step:
            return
        self._zoom = value
        self.zoom_label.configure(text=f"{value}%")
        self.render()

    def _update_controls(self) -> None:
        total = len(self.regions)
        has = total > 0
        self.slide_entry.delete(0, "end")
        if has:
            self.slide_entry.insert(0, str(self.index + 1))
        self.total_label.configure(text=f"/ {total}")
        if has:
            region = self.regions[self.index]
            pages = len(self._pages)
            text = f"pagina {region.page + 1}/{pages}"
            same_page = sum(1 for item in self.regions if item.page == region.page)
            if same_page > 1:
                position = sum(1 for item in self.regions[: self.index] if item.page == region.page) + 1
                text += f" · slide {position} di {same_page}"
            self.page_label.configure(text=text)
        else:
            self.page_label.configure(text="")
        self.prev_button.configure(state="normal" if has and self.index > 0 else "disabled")
        self.next_button.configure(state="normal" if has and self.index < total - 1 else "disabled")
        self.external_button.configure(state="normal" if self.path is not None else "disabled")

    # ------------------------------------------------------------------ disegno
    _message = ""

    def show_message(self, text: str) -> None:
        self._message = text
        self._image = None
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 200)
        height = max(self.canvas.winfo_height(), 120)
        self.canvas.create_text(
            width / 2, height / 2, text=text, fill=COLORS.text_faint, font=font(11), justify="center",
            width=max(160, width - 40),
        )
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def _on_canvas_configure(self, event) -> None:
        size = (event.width, event.height)
        if size == self._canvas_size:
            return
        self._canvas_size = size
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except (tk.TclError, ValueError):
                pass
        self._resize_job = self.after(RESIZE_DEBOUNCE_MS, self._after_resize)

    def _after_resize(self) -> None:
        self._resize_job = None
        if self.regions:
            self.render(keep_position=True)
        else:
            self.show_message(self._message)

    def render(self, anchor: str = "top", keep_position: bool = False) -> None:
        if self._document is None or not self.regions:
            return
        region = self.regions[self.index]
        width = max(1, self.canvas.winfo_width() - 2 * CANVAS_MARGIN)
        height = max(1, self.canvas.winfo_height() - 2 * CANVAS_MARGIN)
        if region.width <= 0 or region.height <= 0:
            return
        if self.fit_var.get() == FIT_MODES[1]:
            scale = width / region.width
        else:
            scale = min(width / region.width, height / region.height)
        scale *= self._zoom / 100
        scale = min(scale, MAX_IMAGE_SIDE / max(region.width, region.height))
        scale = max(scale, 0.05)
        previous = (self.canvas.xview()[0], self.canvas.yview()[0])
        try:
            pixmap = render_region(self._document, region, scale)
            try:
                image = tk.PhotoImage(master=self.canvas, data=pixmap.tobytes("ppm"))
            except tk.TclError:
                import base64

                image = tk.PhotoImage(master=self.canvas, data=base64.b64encode(pixmap.tobytes("png")).decode("ascii"))
        except Exception as exc:
            LOGGER.exception("Rendering della pagina %s non riuscito.", region.page + 1)
            self.show_message(f"Impossibile mostrare la pagina {region.page + 1}:\n{exc}")
            return
        self._image = image
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        # Qualche pixel di arrotondamento non deve far comparire le barre.
        need_w = image.width() + 2 * CANVAS_MARGIN
        need_h = image.height() + 2 * CANVAS_MARGIN
        total_w = need_w if need_w > canvas_w + FIT_TOLERANCE else canvas_w
        total_h = need_h if need_h > canvas_h + FIT_TOLERANCE else canvas_h
        self.canvas.delete("all")
        x = (total_w - image.width()) / 2
        y = (total_h - image.height()) / 2
        self.canvas.create_rectangle(
            x - 1, y - 1, x + image.width(), y + image.height(), outline=COLORS.border, width=1,
        )
        self.canvas.create_image(x, y, image=image, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, total_w, total_h))
        if keep_position:
            self.canvas.xview_moveto(previous[0])
            self.canvas.yview_moveto(previous[1])
        else:
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(1.0 if anchor == "bottom" else 0.0)

    # ------------------------------------------------------------------ rotella
    def _can_scroll(self, units: int) -> bool:
        first, last = self.canvas.yview()
        if last - first >= 0.999:
            return False
        return last < 0.999 if units > 0 else first > 0.001

    def _on_wheel(self, event) -> str:
        delta = getattr(event, "delta", 0)
        if not delta or not self.regions:
            return "break"
        units = -1 if delta > 0 else 1
        if self._can_scroll(units):
            pixels = max(1, int(round(abs(delta) / 120 * 60)))
            self.canvas.yview_scroll(units * pixels, "units")
            return "break"
        # Arrivati al bordo (o slide intera visibile): la rotella cambia slide.
        now = time.monotonic()
        if now - self._last_flip < WHEEL_FLIP_COOLDOWN:
            return "break"
        self._last_flip = now
        if units > 0 and self.index < len(self.regions) - 1:
            self.go_to(self.index + 1, anchor="top")
        elif units < 0 and self.index > 0:
            self.go_to(self.index - 1, anchor="bottom")
        return "break"

    def _on_shift_wheel(self, event) -> str:
        delta = getattr(event, "delta", 0)
        if delta:
            self.canvas.xview_scroll(-60 if delta > 0 else 60, "units")
        return "break"

    # ------------------------------------------------------------------ varie
    def open_external(self) -> None:
        if self.path is None:
            return
        try:
            os.startfile(str(self.path))  # type: ignore[attr-defined]
        except (OSError, AttributeError) as exc:
            messagebox.showerror("Impossibile aprire il file", str(exc), parent=self.winfo_toplevel())

    def current_page(self) -> int | None:
        return self.regions[self.index].page if self.regions else None

    def destroy(self) -> None:
        self.close_document()
        super().destroy()


class PdfViewerWindow(ctk.CTkToplevel):
    """Finestra con il visualizzatore; una sola finestra per file."""

    _open: dict[str, "PdfViewerWindow"] = {}

    def __init__(self, master, app, path: Path, page: int | None = None):
        super().__init__(master)
        self.app = app
        self._key = str(path.resolve()).lower()
        self.title(f"{path.name} — StudyFlow")
        self.configure(fg_color=COLORS.bg)
        self.minsize(520, 420)
        try:
            set_window_icon(self, app.paths["icon"])
            # CustomTkinter reimposta l'icona poco dopo la creazione della finestra.
            self.after(250, lambda: set_window_icon(self, app.paths["icon"]))
        except Exception:
            pass
        width = min(1100, max(700, self.winfo_screenwidth() - 160))
        height = min(860, max(520, self.winfo_screenheight() - 140))
        center_window(self, width, height)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.viewer = PdfViewer(self, app=app)
        self.viewer.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _e: self.destroy())
        PdfViewerWindow._open[self._key] = self
        self.after(30, lambda: self.viewer.load(path, page))
        self.after(120, self.viewer.canvas.focus_set)

    def destroy(self) -> None:
        PdfViewerWindow._open.pop(self._key, None)
        super().destroy()


def open_pdf_window(master, app, path: str | Path, page: int | None = None) -> PdfViewerWindow | None:
    """Apre (o porta in primo piano) la finestra del visualizzatore per ``path``."""
    target = Path(path)
    if not target.exists():
        messagebox.showerror("File non trovato", f"Il file non esiste piu':\n{target}", parent=master.winfo_toplevel())
        return None
    existing = PdfViewerWindow._open.get(str(target.resolve()).lower())
    if existing is not None and existing.winfo_exists():
        existing.deiconify()
        existing.lift()
        existing.focus_force()
        if page is not None:
            existing.viewer.show_page(page)
        return existing
    try:
        return PdfViewerWindow(master.winfo_toplevel(), app, target, page)
    except Exception as exc:
        LOGGER.exception("Apertura del visualizzatore PDF non riuscita.")
        messagebox.showerror("Visualizzatore PDF", str(exc), parent=master.winfo_toplevel())
        return None
