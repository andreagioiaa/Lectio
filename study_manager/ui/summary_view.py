"""Riassunto del corso nella pagina della materia e dialoghi collegati.

- ``SummarySection``: capitoli del riassunto (ordine, storico, export) e il
  pulsante **Crea Riassunto Totale**.
- ``SummaryAddDialog``: da PDF e Slide aggiunge il capitolo di appunti al
  riassunto come nuovo capitolo o sovrascrive uno esistente.
- ``SummaryVersionsDialog``: versioni precedenti di un capitolo, ripristinabili.
- ``ProgressDialog``: avanzamento con annullamento per i lavori lunghi.
"""

from __future__ import annotations

import gc
import logging
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog
from typing import Any, Callable

import customtkinter as ctk

from ..config import COLORS
from ..services.course_summary import (
    FORMAT_PDF,
    QUICK_SUMMARY_NAME,
    SUMMARY_BASENAME,
    SUMMARY_FORMATS,
    chapter_heading,
    convert_pdf_source,
    export_summary,
    generate_quick_summary,
    slide_materials,
)
from ..services.pdf_tools.export import FORMAT_DOCX, find_pandoc
from ..services.subject_folders import KIND_SUMMARY
from ..utils import atomic_write_text, parse_datetime
from .subject_content import _SectionBase
from .theme import font

LOGGER = logging.getLogger(__name__)
ALL_YEARS = "Tutti gli anni"


def _when(value: Any) -> str:
    parsed = parse_datetime(str(value or ""))
    return parsed.strftime("%d/%m/%Y %H:%M") if parsed else str(value or "")


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _versions_label(count: int) -> str:
    return _plural(count, "versione precedente", "versioni precedenti")


def run_in_background(app, work: Callable[[], Any], done: Callable[[Any], None],
                      failed: Callable[[BaseException], None]) -> None:
    """Esegue ``work`` in un thread e riporta il risultato sul thread della GUI."""

    def worker() -> None:
        try:
            result = work()
            callback = lambda: done(result)  # noqa: E731
        except BaseException as exc:  # noqa: BLE001 - l'errore torna all'interfaccia
            callback = lambda exc=exc: failed(exc)  # noqa: E731
        # La callback aspetta la fine del thread: cosi' gli oggetti Tk (finestre,
        # variabili) vengono rilasciati sul thread della GUI e non su questo.
        app.post(lambda: (thread.join(5), callback()))

    # I widget distrutti di recente (pagine ricaricate) formano cicli che il garbage
    # collector libererebbe dal thread di lavoro: Tk lo segnala come errore. Si
    # raccolgono qui, sul thread della GUI, prima di iniziare.
    gc.collect()
    thread = threading.Thread(target=worker, name="Lectio-Background", daemon=True)
    thread.start()


# ---------------------------------------------------------------------------
# Sezione nella pagina materia
# ---------------------------------------------------------------------------
class SummarySection(_SectionBase):
    """Capitoli del riassunto del corso, in ordine, con export e riassunto totale."""

    def __init__(self, master, app, subject: str, chapters: list[dict[str, Any]], on_change=None):
        super().__init__(master, app, subject, "RIASSUNTO DEL CORSO")
        self.on_change = on_change
        self._busy = False
        self.button(self.header_actions, "Cartella", 80,
                    lambda: self.app.open_subject_folder(self.subject, KIND_SUMMARY)).pack(side="left", padx=(0, 6))
        self.year_var = ctk.StringVar(value=ALL_YEARS)
        years = self._slide_years()
        if len(years) > 1:
            ctk.CTkOptionMenu(
                self.header_actions, values=[ALL_YEARS, *years], variable=self.year_var, width=130, height=30,
                font=font(10), fg_color=COLORS.surface_alt, button_color=COLORS.surface_hover,
                button_hover_color=COLORS.border, dropdown_fg_color=COLORS.surface, text_color=COLORS.text,
            ).pack(side="left", padx=(0, 6))
        self.quick_button = self.button(self.header_actions, "Crea Riassunto Totale", 160,
                                        self.create_quick_summary, primary=True)
        self.quick_button.pack(side="left")

        versions = sum(int(row.get("versions") or 0) for row in chapters)
        self.count_label.configure(
            text=(_plural(len(chapters), "capitolo", "capitoli") + (f" · {_versions_label(versions)}" if versions else ""))
            if chapters else "Nessun capitolo nel riassunto"
        )
        if not chapters:
            self.empty("Il riassunto del corso e' vuoto. In PDF e Slide, quando un capitolo di appunti e' "
                       "finito, premi «Al riassunto»: il testo viene aggiunto qui come capitolo. "
                       "«Crea Riassunto Totale» invece unisce tutte le slide della materia in un file .md.")
            return
        for index, chapter in enumerate(chapters):
            self._chapter_row(chapter, index, len(chapters))
        footer = ctk.CTkFrame(self.body, fg_color="transparent")
        footer.grid(row=len(chapters) + 1, column=0, sticky="ew", pady=(8, 0))
        ctk.CTkLabel(footer, text="Esporta il riassunto", font=font(10), text_color=COLORS.text_faint,
                     anchor="w").pack(side="left")
        self.export_buttons: list[ctk.CTkButton] = []
        for fmt in reversed(SUMMARY_FORMATS):
            button = self.button(footer, f".{fmt}", 58, lambda value=fmt: self.export(value))
            button.pack(side="right", padx=(4, 0))
            self.export_buttons.append(button)

    # -------------------------------------------------------------- righe
    def _chapter_row(self, chapter: dict[str, Any], row: int, total: int) -> None:
        card = self.row_card(row)
        ctk.CTkLabel(card, text=chapter_heading(chapter.get("position") or row + 1, chapter.get("title") or ""),
                     font=font(13, "bold"), text_color=COLORS.text, anchor="w", wraplength=380,
                     justify="left").grid(row=0, column=0, sticky="w", padx=14, pady=(10, 1))
        meta = [f"aggiornato il {_when(chapter.get('updated_at'))}"]
        versions = int(chapter.get("versions") or 0)
        if versions:
            meta.append(_versions_label(versions))
        if chapter.get("block_title"):
            meta.append(f"da «{chapter['block_title']}»")
        ctk.CTkLabel(card, text="  ·  ".join(meta), font=font(9), text_color=COLORS.text_faint, anchor="w",
                     wraplength=420, justify="left").grid(row=1, column=0, sticky="w", padx=14)
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=14, pady=(8, 10))
        chapter_id = int(chapter["id"])
        up = self.button(actions, "↑", 32, lambda: self._move(chapter_id, -1))
        up.pack(side="left", padx=(0, 4))
        down = self.button(actions, "↓", 32, lambda: self._move(chapter_id, 1))
        down.pack(side="left", padx=(0, 8))
        if row == 0:
            up.configure(state="disabled")
        if row == total - 1:
            down.configure(state="disabled")
        block_id = chapter.get("note_block_id")
        if block_id:
            self.button(actions, "Apri in PDF e Slide", 140,
                        lambda: self.app.open_in_view("pdf", block_id=int(block_id))).pack(side="left", padx=4)
        if versions:
            self.button(actions, "Storico", 70, lambda: self._versions(chapter)).pack(side="left", padx=4)
        self.button(actions, "Rinomina", 76, lambda: self._rename(chapter)).pack(side="left", padx=4)
        self.button(actions, "Elimina", 70, lambda: self._delete(chapter)).pack(side="right")

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def _move(self, chapter_id: int, delta: int) -> None:
        if self.db.move_summary_chapter(chapter_id, delta):
            self._changed()

    def _rename(self, chapter: dict[str, Any]) -> None:
        title = simpledialog.askstring("Rinomina capitolo", "Nuovo titolo del capitolo:",
                                       initialvalue=str(chapter.get("title") or ""), parent=self.winfo_toplevel())
        if title is None or not title.strip():
            return
        self.db.rename_summary_chapter(int(chapter["id"]), title)
        self._changed()

    def _delete(self, chapter: dict[str, Any]) -> None:
        label = chapter_heading(chapter.get("position") or 0, chapter.get("title") or "")
        if not messagebox.askyesno(
            "Elimina capitolo dal riassunto",
            f"Togliere «{label}» dal riassunto del corso?\n\nVengono eliminati anche le sue versioni "
            "precedenti. Il capitolo di appunti in PDF e Slide non viene toccato.",
            icon="warning", parent=self.winfo_toplevel(),
        ):
            return
        self.db.delete_summary_chapter(int(chapter["id"]))
        self.app.toast(f"«{label}» tolto dal riassunto")
        self._changed()

    def _versions(self, chapter: dict[str, Any]) -> None:
        if SummaryVersionsDialog(self, self.db, chapter).show():
            self._changed()

    # -------------------------------------------------------------- export
    def _target(self, name: str) -> Path | None:
        folder = self.app.subject_folder(self.subject, KIND_SUMMARY)
        if folder is None:
            messagebox.showerror("Riassunto", "Cartella della materia non disponibile.", parent=self.winfo_toplevel())
            return None
        return folder / name

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in [self.quick_button, *getattr(self, "export_buttons", [])]:
            try:
                button.configure(state="disabled" if busy else "normal")
            except tk.TclError:
                pass

    def export(self, fmt: str) -> None:
        if self._busy:
            return
        if fmt == FORMAT_DOCX and not find_pandoc():
            from .pandoc_prompt import ensure_pandoc  # import locale: pandoc_prompt importa questo modulo

            ensure_pandoc(self.app, self, lambda: self.export(fmt))
            return
        target = self._target(f"{SUMMARY_BASENAME}.{fmt}")
        if target is None:
            return
        chapters = self.db.list_summary_chapters(self.subject, with_source=True)
        subject = self.subject
        self._set_busy(True)
        self.app.toast("Creo il PDF con LaTeX…" if fmt == FORMAT_PDF else f"Esporto {target.name}…")

        def done(path: Path) -> None:
            self._safe_busy(False)
            self.app.toast(f"Riassunto esportato: {path.name}")
            try:
                os.startfile(str(path))  # type: ignore[attr-defined]
            except (OSError, AttributeError):
                pass

        def failed(exc: BaseException) -> None:
            self._safe_busy(False)
            LOGGER.warning("Export del riassunto non riuscito: %s", exc)
            messagebox.showerror("Esporta riassunto", str(exc), parent=self.app)

        run_in_background(self.app, lambda: export_summary(subject, chapters, fmt, target), done, failed)

    def _safe_busy(self, busy: bool) -> None:
        try:
            if self.winfo_exists():
                self._set_busy(busy)
        except tk.TclError:
            pass

    # -------------------------------------------------------------- riassunto totale
    def _slide_years(self) -> list[str]:
        materials = slide_materials(self.db.list_materials_for_subject(self.subject))
        return sorted({str(row.get("academic_year") or "") for row in materials if row.get("academic_year")},
                      reverse=True)

    def create_quick_summary(self) -> None:
        if self._busy:
            return
        year = "" if self.year_var.get() == ALL_YEARS else self.year_var.get()
        materials = self.db.list_materials_for_subject(self.subject)
        slides = slide_materials(materials, year)
        if not slides:
            messagebox.showinfo("Crea Riassunto Totale",
                                "Nessuna slide (PDF) collegata a questa materia"
                                + (f" per l'anno {year}." if year else "."), parent=self.winfo_toplevel())
            return
        target = self._target(QUICK_SUMMARY_NAME)
        if target is None:
            return
        blocks = [self.db.get_note_block(int(row["id"])) or {} for row in self.db.list_note_blocks(self.subject)]
        pdf_view = getattr(self.app, "views", {}).get("pdf")
        options = pdf_view.extract_options() if pdf_view is not None and hasattr(pdf_view, "extract_options") else None
        cancel = threading.Event()
        progress = ProgressDialog(self.app, "Crea Riassunto Totale", f"{len(slides)} slide in ordine di nome file",
                                  on_cancel=cancel.set)
        subject = self.subject
        self._set_busy(True)

        def report(fraction: float, message: str) -> None:
            self.app.post(lambda: progress.update_progress(fraction, message))

        def work() -> tuple[str, list[str]]:
            text, warnings = generate_quick_summary(
                subject, materials, blocks,
                lambda path, step: convert_pdf_source(path, step, cancel, options),
                progress=report, cancelled=cancel.is_set, academic_year=year,
            )
            atomic_write_text(target, text)
            return text, warnings

        def done(result: tuple[str, list[str]]) -> None:
            progress.close()
            self._safe_busy(False)
            _text, warnings = result
            message = f"Creato {target.name} nella cartella Riassunto della materia."
            if warnings:
                message += "\n\nAvvisi:\n" + "\n".join(f"• {line}" for line in warnings[:8])
            if messagebox.askyesno("Crea Riassunto Totale", message + "\n\nAprire il file?", parent=self.app):
                try:
                    os.startfile(str(target))  # type: ignore[attr-defined]
                except (OSError, AttributeError):
                    pass

        def failed(exc: BaseException) -> None:
            progress.close()
            self._safe_busy(False)
            if isinstance(exc, InterruptedError):
                self.app.toast("Riassunto totale annullato.")
                return
            LOGGER.warning("Riassunto totale non riuscito: %s", exc)
            messagebox.showerror("Crea Riassunto Totale", str(exc), parent=self.app)

        run_in_background(self.app, work, done, failed)


# ---------------------------------------------------------------------------
# Dialoghi
# ---------------------------------------------------------------------------
class _Modal(ctk.CTkToplevel):
    def __init__(self, parent, title: str):
        super().__init__(parent.winfo_toplevel())
        self.parent = parent
        self.result: Any = None
        self.title(title)
        self.configure(fg_color=COLORS.bg)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.body = ctk.CTkFrame(self, fg_color=COLORS.surface, corner_radius=14, border_width=1,
                                 border_color=COLORS.border)
        self.body.pack(fill="both", expand=True, padx=12, pady=12)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda _e: self.cancel())

    def heading(self, title: str, text: str, wrap: int = 460) -> None:
        ctk.CTkLabel(self.body, text=title, font=font(13, "bold"), text_color=COLORS.text, anchor="w").pack(
            fill="x", padx=16, pady=(14, 2))
        ctk.CTkLabel(self.body, text=text, font=font(10), text_color=COLORS.text_faint, anchor="w",
                     justify="left", wraplength=wrap).pack(fill="x", padx=16, pady=(0, 10))

    def label(self, parent, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(parent, text=text, font=font(10, "bold"), text_color=COLORS.text_muted, anchor="w")

    def action(self, parent, text: str, command, primary: bool = False, width: int = 110) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, height=32, width=width, font=font(11, "bold"), corner_radius=9,
            fg_color=COLORS.accent if primary else COLORS.surface_alt,
            hover_color=COLORS.accent_dark if primary else COLORS.surface_hover,
            text_color=COLORS.text, border_width=0 if primary else 1, border_color=COLORS.border, command=command,
        )

    def place(self, min_width: int = 520) -> None:
        try:
            self.update_idletasks()
            width = max(min_width, self.body.winfo_reqwidth() + 24)
            # Con lo zoom di Windows (125-150%) un dialogo alto puo' superare lo schermo:
            # l'altezza resta entro lo schermo e il contenuto elastico si restringe.
            height = min(self.body.winfo_reqheight() + 24, self.winfo_screenheight() - 120)
            top = self.parent.winfo_toplevel()
            x = top.winfo_rootx() + max(0, (top.winfo_width() - width) // 2)
            y = top.winfo_rooty() + max(0, (top.winfo_height() - height) // 3)
            y = max(10, min(y, self.winfo_screenheight() - height - 70))
            self.geometry(f"{width}x{height}+{x}+{y}")
            self.grab_set()
        except tk.TclError:
            pass

    def cancel(self) -> None:
        self.result = None
        self.close()

    def close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self) -> Any:
        self.wait_window(self)
        return self.result


class SummaryAddDialog(_Modal):
    """Sceglie dove mettere il capitolo: nuovo (titolo e posizione) o sovrascrittura di uno esistente.

    ``show()`` -> None se annullato, altrimenti ``{"mode": "new", "title", "position"}``
    oppure ``{"mode": "replace", "chapter_id", "title"}``.
    """

    def __init__(self, parent, chapters: list[dict[str, Any]], subject: str, title: str,
                 linked_chapter_id: int | None = None):
        super().__init__(parent, "Aggiungi al riassunto del corso")
        self.chapters = chapters
        self._labels = [chapter_heading(row.get("position") or i, row.get("title") or "")
                        for i, row in enumerate(chapters, 1)]
        self.heading(
            f"Riassunto del corso · {subject}",
            "Il testo attuale del capitolo di appunti viene copiato nel riassunto. Se sostituisci un "
            "capitolo, la versione precedente resta nello storico e si puo' ripristinare.",
        )
        self.mode_var = tk.StringVar(value="replace" if linked_chapter_id and chapters else "new")
        new_radio = ctk.CTkRadioButton(self.body, text="Nuovo capitolo", variable=self.mode_var, value="new",
                                       font=font(11, "bold"), text_color=COLORS.text, command=self._sync)
        new_radio.pack(anchor="w", padx=16, pady=(0, 6))
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", padx=36, pady=(0, 10))
        row.grid_columnconfigure(1, weight=1)
        self.label(row, "Posizione").grid(row=0, column=0, sticky="w")
        self.label(row, "Titolo").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.position_var = ctk.StringVar(value=str(len(chapters) + 1))
        self.position_entry = ctk.CTkEntry(row, textvariable=self.position_var, width=60, height=34, font=font(12),
                                           fg_color=COLORS.surface_alt, border_color=COLORS.border,
                                           text_color=COLORS.text)
        self.position_entry.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.title_var = ctk.StringVar(value=title)
        self.title_entry = ctk.CTkEntry(row, textvariable=self.title_var, height=34, font=font(12),
                                        fg_color=COLORS.surface_alt, border_color=COLORS.border,
                                        text_color=COLORS.text)
        self.title_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))

        replace_radio = ctk.CTkRadioButton(self.body, text="Sovrascrivi un capitolo esistente",
                                           variable=self.mode_var, value="replace", font=font(11, "bold"),
                                           text_color=COLORS.text, command=self._sync)
        replace_radio.pack(anchor="w", padx=16, pady=(4, 6))
        self.chapter_combo = ctk.CTkComboBox(
            self.body, values=self._labels or ["Nessun capitolo"], height=34, font=font(12),
            dropdown_font=font(12), fg_color=COLORS.surface_alt, border_color=COLORS.border,
            button_color=COLORS.surface_hover, button_hover_color=COLORS.border, dropdown_fg_color=COLORS.surface,
            dropdown_hover_color=COLORS.surface_hover, text_color=COLORS.text, corner_radius=9, state="readonly",
        )
        self.chapter_combo.pack(fill="x", padx=36, pady=(0, 10))
        selected = 0
        if linked_chapter_id is not None:
            for index, chapter in enumerate(chapters):
                if int(chapter["id"]) == int(linked_chapter_id):
                    selected = index
        self.chapter_combo.set(self._labels[selected] if self._labels else "Nessun capitolo")
        if not chapters:
            replace_radio.configure(state="disabled")

        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(8, 14))
        buttons.grid_columnconfigure(0, weight=1)
        self.action(buttons, "Annulla", self.cancel).grid(row=0, column=1, padx=(0, 6))
        self.confirm_button = self.action(buttons, "Aggiungi", self.confirm, primary=True)
        self.confirm_button.grid(row=0, column=2)
        self.bind("<Return>", lambda _e: self.confirm())
        self._sync()
        self.place()

    def _sync(self) -> None:
        new = self.mode_var.get() == "new"
        for widget in (self.position_entry, self.title_entry):
            widget.configure(state="normal" if new else "disabled")
        self.chapter_combo.configure(state="disabled" if new else "readonly")
        self.confirm_button.configure(text="Aggiungi" if new else "Sovrascrivi")

    def confirm(self) -> None:
        if self.mode_var.get() == "new":
            title = self.title_var.get().strip()
            try:
                position = int(self.position_var.get().strip())
                if position < 1:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("Riassunto", "La posizione deve essere un numero intero ≥ 1.", parent=self)
                return
            if not title:
                messagebox.showwarning("Riassunto", "Scrivi il titolo del capitolo.", parent=self)
                return
            self.result = {"mode": "new", "title": title, "position": position}
            self.close()
            return
        try:
            index = self._labels.index(self.chapter_combo.get())
        except ValueError:
            return
        chapter = self.chapters[index]
        if not messagebox.askyesno(
            "Sovrascrivi capitolo",
            f"Sostituire il testo di «{self._labels[index]}» con quello attuale?\n\n"
            "La versione precedente resta nello storico del capitolo.",
            parent=self,
        ):
            return
        self.result = {"mode": "replace", "chapter_id": int(chapter["id"]), "title": str(chapter.get("title") or "")}
        self.close()


class SummaryVersionsDialog(_Modal):
    """Storico di un capitolo; ``show()`` -> True se e' stata ripristinata una versione."""

    def __init__(self, parent, db, chapter: dict[str, Any]):
        super().__init__(parent, "Storico del capitolo")
        self.db = db
        self.result = False
        self.heading(
            f"Storico · {chapter_heading(chapter.get('position') or 0, chapter.get('title') or '')}",
            f"Versione attuale del {_when(chapter.get('updated_at'))}. Ripristinando una versione, quella "
            "attuale finisce a sua volta nello storico.",
        )
        listing = ctk.CTkScrollableFrame(self.body, fg_color="transparent", height=260, width=460)
        listing.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        listing.grid_columnconfigure(0, weight=1)
        for row, version in enumerate(self.db.list_summary_versions(int(chapter["id"]))):
            card = ctk.CTkFrame(listing, fg_color=COLORS.surface_alt, corner_radius=10, border_width=1,
                                border_color=COLORS.border)
            card.grid(row=row, column=0, sticky="ew", pady=3)
            card.grid_columnconfigure(0, weight=1)
            size = int(version.get("source_length") or 0)
            ctk.CTkLabel(card, text=f"{_when(version.get('saved_at'))} · {version.get('title') or ''}",
                         font=font(11, "bold"), text_color=COLORS.text, anchor="w").grid(
                row=0, column=0, sticky="w", padx=12, pady=(8, 0))
            ctk.CTkLabel(card, text=f"{size} caratteri", font=font(9), text_color=COLORS.text_faint,
                         anchor="w").grid(row=1, column=0, sticky="w", padx=12, pady=(0, 8))
            self.action(card, "Ripristina", lambda value=int(version["id"]): self._restore(value),
                        width=96).grid(row=0, column=1, rowspan=2, padx=10)
        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(4, 14))
        buttons.grid_columnconfigure(0, weight=1)
        self.action(buttons, "Chiudi", self.close).grid(row=0, column=1)
        self.place()

    def cancel(self) -> None:
        self.close()

    def _restore(self, version_id: int) -> None:
        if not messagebox.askyesno("Ripristina versione",
                                   "Ripristinare questa versione? Quella attuale va nello storico.", parent=self):
            return
        try:
            self.db.restore_summary_version(version_id)
        except Exception as exc:
            messagebox.showerror("Ripristina versione", str(exc), parent=self)
            return
        self.result = True
        self.close()


class ProgressDialog(ctk.CTkToplevel):
    """Finestra non modale con barra di avanzamento e pulsante Annulla."""

    def __init__(self, master, title: str, text: str, on_cancel: Callable[[], None] | None = None):
        super().__init__(master)
        self.title(title)
        self.configure(fg_color=COLORS.bg)
        self.resizable(False, False)
        self.transient(master)
        self._on_cancel = on_cancel
        body = ctk.CTkFrame(self, fg_color=COLORS.surface, corner_radius=14, border_width=1,
                            border_color=COLORS.border)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        ctk.CTkLabel(body, text=title, font=font(13, "bold"), text_color=COLORS.text, anchor="w").pack(
            fill="x", padx=16, pady=(14, 2))
        self.message = ctk.CTkLabel(body, text=text, font=font(10), text_color=COLORS.text_muted, anchor="w",
                                    justify="left", wraplength=420)
        self.message.pack(fill="x", padx=16, pady=(0, 8))
        self.bar = ctk.CTkProgressBar(body, height=8, corner_radius=4, fg_color=COLORS.surface_alt,
                                      progress_color=COLORS.accent)
        self.bar.pack(fill="x", padx=16, pady=(0, 10))
        self.bar.set(0)
        self.cancel_button = ctk.CTkButton(
            body, text="Annulla", height=30, width=100, font=font(11), fg_color=COLORS.surface_alt,
            hover_color=COLORS.surface_hover, border_width=1, border_color=COLORS.border, text_color=COLORS.text,
            command=self.cancel,
        )
        self.cancel_button.pack(anchor="e", padx=16, pady=(0, 14))
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        try:
            self.update_idletasks()
            width, height = 460, body.winfo_reqheight() + 24
            x = master.winfo_rootx() + max(0, (master.winfo_width() - width) // 2)
            y = master.winfo_rooty() + max(0, (master.winfo_height() - height) // 3)
            self.geometry(f"{width}x{height}+{x}+{y}")
        except tk.TclError:
            pass

    def update_progress(self, fraction: float, message: str) -> None:
        try:
            if self.winfo_exists():
                self.bar.set(max(0.0, min(1.0, fraction)))
                self.message.configure(text=message[:160])
        except tk.TclError:
            pass

    def cancel(self) -> None:
        if self._on_cancel is not None:
            self._on_cancel()
        try:
            self.cancel_button.configure(state="disabled", text="Annullo…")
        except tk.TclError:
            pass

    def close(self) -> None:
        try:
            if self.winfo_exists():
                self.destroy()
        except tk.TclError:
            pass
