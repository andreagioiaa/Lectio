"""Materiali di studio collegati alle materie.

I materiali non hanno piu' una sezione autonoma: ogni pagina materia mostra e
gestisce i propri PDF, documenti e note. Questo modulo contiene la sezione
riutilizzabile e il dialogo di creazione/modifica.
"""

from __future__ import annotations

import os
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from typing import Any, Callable

import customtkinter as ctk

from ..config import COLORS
from ..services.course_summary import (
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_TODO,
    material_status,
)
from ..services.subject_folders import SLIDE_EXTENSIONS, files_size
from ..services.subjects import (
    ACADEMIC_YEAR_UNKNOWN,
    academic_year_choices,
    academic_year_for,
    normalize_academic_year,
)
from ..utils import format_hours, safe_float
from .formatting import format_event_datetime
from .pdf_viewer import is_pdf, open_pdf_window
from .theme import font
from .widgets import Card, center_window, clear_children, set_window_icon


ORPHAN_SUBJECT = "Materiali senza materia"
ORPHAN_SUBJECT_KEY = "\x00lectio-orphan-materials"

# Etichetta e colore del titolo per lo stato di studio delle slide.
STATUS_LABELS = {
    STATUS_TODO: "Da iniziare",
    STATUS_IN_PROGRESS: "In corso",
    STATUS_DONE: "Completata",
}


def status_color(status: str) -> str:
    return {
        STATUS_TODO: COLORS.red,
        STATUS_IN_PROGRESS: COLORS.amber,
        STATUS_DONE: COLORS.green,
    }.get(status, COLORS.text)


def is_slide(material: dict[str, Any]) -> bool:
    return Path(str(material.get("file_path") or "")).suffix.lower() in SLIDE_EXTENSIONS


class MaterialsSection(Card):
    """Elenco dei materiali di una materia, con azioni di studio e modifica."""

    def __init__(
        self,
        master,
        app,
        subject: str,
        on_change: Callable[[], None] | None = None,
        allow_add: bool = True,
        allow_subject_change: bool = False,
    ):
        super().__init__(master)
        self.app = app
        self.db = app.db
        self._subject = str(subject or "")
        self._on_change = on_change
        self._allow_subject_change = allow_subject_change
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 6))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="MATERIALI",
            font=font(9, "bold"),
            text_color=COLORS.text_faint,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self._count_label = ctk.CTkLabel(
            header,
            text="",
            font=font(10),
            text_color=COLORS.text_muted,
            anchor="w",
        )
        self._count_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        # Compare solo se ci sono materiali con il file fuori dalla cartella della materia.
        self._copy_button = ctk.CTkButton(
            header,
            text="",
            height=32,
            font=font(10),
            fg_color="transparent",
            hover_color=COLORS.surface_hover,
            border_width=1,
            border_color=COLORS.border,
            text_color=COLORS.text_muted,
            corner_radius=8,
            command=self._copy_existing,
        )
        if allow_add:
            ctk.CTkButton(
                header,
                text="+  Materiale",
                height=32,
                width=126,
                font=font(10, "bold"),
                fg_color=COLORS.accent_dark,
                hover_color=COLORS.accent_hover,
                text_color=COLORS.text,
                corner_radius=8,
                command=lambda: self._open_dialog(None),
            ).grid(row=0, column=2, rowspan=2, sticky="e", padx=(10, 0))

        self._list = ctk.CTkFrame(self, fg_color="transparent")
        self._list.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 14))
        self._list.grid_columnconfigure(0, weight=1)

        self.refresh()

    # ------------------------------------------------------------------ data
    def refresh(self) -> None:
        if self._subject:
            materials = self.db.list_materials_for_subject(self._subject)
        else:
            materials = self.db.list_materials_without_subject()
        clear_children(self._list)
        self._summary_paths: set[str] = set()
        self._notes_paths: set[str] = set()
        if self._subject:
            try:
                self._summary_paths = self.db.summary_pdf_paths(self._subject)
                self._notes_paths = self.db.note_block_pdf_paths(self._subject)
            except Exception:
                self.app.logger.debug("Stato delle slide non disponibile.", exc_info=True)
        target = sum(safe_float(row.get("target_hours", 0)) for row in materials)
        logged = sum(safe_float(row.get("logged_hours", 0)) for row in materials)
        if materials:
            detail = f"{len(materials)} file · {format_hours(logged)} registrate"
            if target > 0:
                detail += f" su {format_hours(target)} previste"
            slides = [row for row in materials if is_slide(row)]
            if slides:
                done = sum(1 for row in slides if self._status(row) == STATUS_DONE)
                detail += f" · slide completate {done}/{len(slides)}"
            self._count_label.configure(text=detail)
        else:
            self._count_label.configure(text="Nessun file collegato")

        if not materials:
            ctk.CTkLabel(
                self._list,
                text=(
                    "Nessun materiale per questa materia. "
                    "Aggiungi dispense, PDF o documenti."
                    if self._subject
                    else "Tutti i materiali sono collegati a una materia."
                ),
                font=font(11),
                text_color=COLORS.text_faint,
                anchor="w",
                wraplength=420,
                justify="left",
            ).grid(row=0, column=0, sticky="w", pady=(0, 2))
            return
        self._update_copy_button(materials)
        years = {normalize_academic_year(row.get("academic_year")) for row in materials}
        if years == {""}:
            for index, material in enumerate(materials):
                self._material_row(material, index)
            return
        # Raggruppati per anno accademico, dal piu' recente; "Non indicato" in fondo.
        groups: dict[str, list[dict[str, Any]]] = {}
        for material in materials:
            groups.setdefault(normalize_academic_year(material.get("academic_year")), []).append(material)
        row = 0
        ordered = sorted((year for year in groups if year), reverse=True) + ([""] if "" in groups else [])
        for year in ordered:
            ctk.CTkLabel(
                self._list,
                text=f"A.A. {year}" if year else ACADEMIC_YEAR_UNKNOWN,
                font=font(10, "bold"),
                text_color=COLORS.text_muted,
                anchor="w",
            ).grid(row=row, column=0, sticky="w", pady=(8 if row else 0, 2))
            row += 1
            for material in groups[year]:
                self._material_row(material, row)
                row += 1

    def _status(self, material: dict[str, Any]) -> str:
        return material_status(material, self._summary_paths, self._notes_paths)

    # ------------------------------------------------------ copia nella cartella
    def _outside_materials(self, materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._subject:
            return []
        folders = self.app.subject_folders
        return [
            row for row in materials
            if str(row.get("file_path") or "").strip()
            and Path(str(row["file_path"])).is_file()
            and not folders.is_in_subject(str(row["file_path"]), self._subject)
        ]

    def _update_copy_button(self, materials: list[dict[str, Any]]) -> None:
        outside = self._outside_materials(materials)
        if not outside:
            self._copy_button.grid_remove()
            return
        size = files_size(row["file_path"] for row in outside)
        self._copy_button.configure(
            text=f"Copia {len(outside)} file nella cartella ({_format_size(size)})"
        )
        self._copy_button.grid(row=0, column=1, rowspan=2, sticky="e", padx=(10, 0))

    def _copy_existing(self) -> None:
        materials = self.db.list_materials_for_subject(self._subject)
        outside = self._outside_materials(materials)
        if not outside:
            self.refresh()
            return
        size = files_size(row["file_path"] for row in outside)
        folder = self.app.subject_folders.path_for(self._subject)
        if not messagebox.askyesno(
            "Copia nella cartella della materia",
            f"Copiare {len(outside)} file ({_format_size(size)}) in\n{folder}?\n\n"
            "Gli originali restano dove sono; il materiale usera' la copia. "
            "Serve per sincronizzare o condividere la materia.",
            parent=self.winfo_toplevel(),
        ):
            return
        copied, errors = 0, []
        for material in outside:
            try:
                target = self.app.subject_folders.copy_material(material["file_path"], self._subject)
                self.db.update_material(
                    int(material["id"]),
                    str(material.get("title") or ""),
                    str(target),
                    str(material.get("subject") or self._subject),
                    safe_float(material.get("target_hours", 0)),
                    material.get("calendar_event_id"),
                )
                copied += 1
            except Exception as exc:
                self.app.logger.exception("Copia del materiale non riuscita.")
                errors.append(f"{Path(str(material['file_path'])).name}: {exc}")
        if errors:
            messagebox.showwarning("Copia non completata", "\n".join(errors[:8]), parent=self.winfo_toplevel())
        self.app.toast(f"Copiati {copied} file nella cartella della materia.")
        self.notify_change()

    def notify_change(self) -> None:
        """Aggiorna la sezione e la pagina materia che la contiene."""
        if self._on_change is not None:
            self._on_change()
        else:
            self.refresh()

    # ------------------------------------------------------------------ rows
    def _material_row(self, material: dict[str, Any], row: int) -> None:
        card = ctk.CTkFrame(
            self._list,
            fg_color=COLORS.surface_alt,
            corner_radius=10,
            border_width=1,
            border_color=COLORS.border,
        )
        card.grid(row=row, column=0, sticky="ew", pady=3)
        card.grid_columnconfigure(0, weight=1)

        title = str(material.get("title") or "Materiale")
        slide = is_slide(material)
        status = self._status(material) if slide else ""
        ctk.CTkLabel(
            card,
            text=title,
            font=font(13, "bold"),
            text_color=status_color(status) if slide else COLORS.text,
            anchor="w",
            wraplength=380,
            justify="left",
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 1))

        meta_parts: list[str] = []
        if slide:
            label = STATUS_LABELS[status]
            if status == STATUS_DONE and not str(material.get("completed_at") or "").strip():
                label += " (nel riassunto del corso)"
            meta_parts.append(label)
        year = normalize_academic_year(material.get("academic_year"))
        if year:
            meta_parts.append(f"A.A. {year}")
        if material.get("event_title"):
            event_text = str(material["event_title"])
            if material.get("event_start"):
                event_text = f"{event_text} · {format_event_datetime(str(material['event_start']))}"
            meta_parts.append(event_text)
        file_path = str(material.get("file_path") or "")
        path_exists = bool(file_path) and Path(file_path).exists()
        if file_path:
            meta_parts.append(Path(file_path).name + ("" if path_exists else " (non trovato)"))
        if meta_parts:
            ctk.CTkLabel(
                card,
                text="  ·  ".join(meta_parts),
                font=font(9),
                text_color=COLORS.red if file_path and not path_exists else COLORS.text_faint,
                anchor="w",
                wraplength=420,
                justify="left",
            ).grid(row=1, column=0, sticky="w", padx=14)

        progress_frame = ctk.CTkFrame(card, fg_color="transparent")
        progress_frame.grid(row=2, column=0, sticky="ew", padx=14, pady=(8, 4))
        progress_frame.grid_columnconfigure(0, weight=1)
        target_hours = safe_float(material.get("target_hours", 0))
        logged_hours = safe_float(material.get("logged_hours", 0))
        ratio = min(1.0, logged_hours / target_hours) if target_hours > 0 else 0.0
        progress_text = (
            f"{logged_hours:.1f} / {target_hours:.1f} ore · {ratio * 100:.0f}%"
            if target_hours > 0
            else f"{logged_hours:.1f} ore registrate · nessun obiettivo"
        )
        ctk.CTkLabel(
            progress_frame,
            text=progress_text,
            font=font(9, "bold"),
            text_color=COLORS.text_muted,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 3))
        bar = ctk.CTkProgressBar(
            progress_frame,
            height=6,
            corner_radius=3,
            fg_color=COLORS.surface,
            progress_color=COLORS.accent if ratio < 1 else COLORS.green,
        )
        bar.grid(row=1, column=0, sticky="ew")
        bar.set(ratio)

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=14, pady=(6, 10))
        self._action(
            actions,
            "Apri file",
            78,
            lambda value=dict(material): self._open_material(value),
        ).pack(side="left", padx=(0, 4))
        self._action(
            actions,
            "Studia ora",
            86,
            lambda value=dict(material): self._study(value),
            primary=True,
        ).pack(side="left", padx=4)
        self._action(
            actions,
            "Aggiungi ore",
            90,
            lambda material_id=int(material["id"]): self._add_hours(material_id),
        ).pack(side="left", padx=4)
        if slide:
            completed = bool(str(material.get("completed_at") or "").strip())
            self._action(
                actions,
                "Riapri" if completed else "Completata",
                84,
                lambda material_id=int(material["id"]), value=not completed: self._set_completed(
                    material_id, value
                ),
            ).pack(side="left", padx=4)
        self._action(
            actions,
            "Modifica",
            70,
            lambda value=dict(material): self._open_dialog(value),
        ).pack(side="left", padx=4)
        self._action(
            actions,
            "Elimina",
            64,
            lambda value=dict(material): self._delete(value),
            danger=True,
        ).pack(side="right")

    def _action(
        self,
        parent,
        text: str,
        width: int,
        command,
        primary: bool = False,
        danger: bool = False,
    ) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=text,
            height=30,
            width=width,
            font=font(10, "bold" if primary or danger else "normal"),
            fg_color=COLORS.accent_dark if primary else "transparent",
            hover_color=COLORS.red_dark if danger else COLORS.surface_hover,
            border_width=0 if primary else 1,
            border_color=COLORS.border,
            text_color=COLORS.red if danger else COLORS.text if primary else COLORS.text_muted,
            corner_radius=7,
            command=command,
        )

    # --------------------------------------------------------------- actions
    def _open_dialog(self, material: dict[str, Any] | None) -> None:
        MaterialDialog(
            self,
            material,
            self._subject,
            allow_subject_change=self._allow_subject_change,
        )

    def _mark_opened(self, material: dict[str, Any]) -> None:
        """Prima apertura di una slide: passa da «Da iniziare» a «In corso»."""
        if str(material.get("opened_at") or "").strip():
            return
        try:
            self.db.mark_material_opened(int(material["id"]))
        except Exception:
            self.app.logger.debug("Stato 'aperta' della slide non salvato.", exc_info=True)
            return
        self.after(50, self.notify_change)

    def _study(self, material: dict[str, Any]) -> None:
        self._mark_opened(material)
        self.app.start_from_material(material)

    def _set_completed(self, material_id: int, completed: bool) -> None:
        self.db.set_material_completed(material_id, completed)
        self.app.toast("Slide segnata come completata." if completed else "Slide riaperta.")
        self.notify_change()

    def _open_material(self, material: dict[str, Any]) -> None:
        if self._open_file(str(material.get("file_path") or "")):
            self._mark_opened(material)

    def _open_file(self, file_path: str) -> bool:
        if not file_path:
            messagebox.showwarning(
                "File non impostato",
                "Nessun file associato a questo materiale.",
                parent=self.winfo_toplevel(),
            )
            return False
        path = Path(file_path)
        if not path.exists():
            messagebox.showerror(
                "File non trovato",
                f"Il file non esiste piu' nel percorso indicato:\n{file_path}",
                parent=self.winfo_toplevel(),
            )
            return False
        if is_pdf(path):
            # I PDF si aprono nel visualizzatore integrato (slide divise una per volta).
            if open_pdf_window(self, self.app, path) is not None:
                return True
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except (OSError, AttributeError) as exc:
            messagebox.showerror(
                "Impossibile aprire il file",
                str(exc),
                parent=self.winfo_toplevel(),
            )
            return False
        return True

    def _add_hours(self, material_id: int) -> None:
        value = simpledialog.askfloat(
            "Registra ore",
            "Ore da aggiungere (es. 0.5):",
            parent=self.winfo_toplevel(),
            initialvalue=0.5,
            minvalue=0.01,
            maxvalue=24.0,
        )
        if value is None:
            return
        self.db.add_material_hours(material_id, float(value))
        self.app.toast(f"Aggiunte {value:g} ore al materiale.")
        self.notify_change()

    def _delete(self, material: dict[str, Any]) -> None:
        if not messagebox.askyesno(
            "Eliminare il materiale?",
            f"Eliminare '{material.get('title', 'Materiale')}'? "
            "I file su disco (originale e copia nella cartella della materia) non verranno toccati.",
            parent=self.winfo_toplevel(),
        ):
            return
        self.db.delete_material(int(material["id"]))
        self.app.toast("Materiale eliminato.")
        self.notify_change()


class MaterialDialog(ctk.CTkToplevel):
    def __init__(
        self,
        section: MaterialsSection,
        material: dict[str, Any] | None,
        subject: str,
        allow_subject_change: bool = False,
    ):
        super().__init__(section.winfo_toplevel())
        self.section = section
        self.app = section.app
        self.db = section.db
        self.material = material
        self.fixed_subject = str(subject or "")
        self.allow_subject_change = allow_subject_change
        self._event_ids: list[int | None] = [None]
        self._event_labels: list[str] = ["Nessun evento"]
        self.title("Modifica materiale" if material else "Nuovo materiale")
        self.configure(fg_color=COLORS.bg)
        self.resizable(False, False)
        self.transient(section.winfo_toplevel())
        set_window_icon(self, self.app.paths["icon"])
        center_window(self, 640, 720)
        self.grid_columnconfigure(0, weight=1)
        self._build()
        self.grab_set()
        self.after(100, self._populate)
        self.bind("<Escape>", lambda _event: self.destroy())

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        ctk.CTkLabel(
            self,
            text="Modifica materiale" if self.material else "Nuovo materiale",
            font=font(22, "bold"),
            text_color=COLORS.text,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=28, pady=(24, 2))
        ctk.CTkLabel(
            self,
            text="Il materiale resta collegato alla materia e, se vuoi, a un evento.",
            font=font(11),
            text_color=COLORS.text_muted,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=28, pady=(0, 16))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.grid(row=2, column=0, sticky="ew", padx=28)
        form.grid_columnconfigure(0, weight=1)

        self.title_var = ctk.StringVar()
        self.target_var = ctk.StringVar(value="0")
        self.file_var = ctk.StringVar()
        self.subject_var = ctk.StringVar(value=self.fixed_subject)
        self._files: list[str] = []  # piu' file scelti insieme (solo per un nuovo materiale)
        self._files_label = ""
        self.title_entry = self._field(form, 0, "Titolo", self.title_var, "es. Dispense Capitolo 3")

        if self.allow_subject_change:
            self._field(
                form,
                1,
                "Materia *",
                self.subject_var,
                "es. Analisi Matematica",
                hint=(
                    "Obbligatoria: il materiale comparirà nella pagina di questa "
                    "materia. Puoi anche scrivere il nome di una nuova materia."
                ),
            )
        else:
            subject_card = ctk.CTkFrame(
                form,
                fg_color=COLORS.surface_alt,
                corner_radius=9,
                border_width=1,
                border_color=COLORS.border,
            )
            subject_card.grid(row=1, column=0, sticky="ew", pady=(0, 12))
            ctk.CTkLabel(
                subject_card,
                text="MATERIA",
                font=font(9, "bold"),
                text_color=COLORS.text_faint,
                anchor="w",
            ).pack(anchor="w", padx=14, pady=(8, 0))
            ctk.CTkLabel(
                subject_card,
                text=self.fixed_subject or "Senza materia",
                font=font(13, "bold"),
                text_color=COLORS.text,
                anchor="w",
            ).pack(anchor="w", padx=14, pady=(1, 9))

        file_frame = ctk.CTkFrame(form, fg_color="transparent")
        file_frame.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        file_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            file_frame,
            text="File PDF / documento",
            font=font(11, "bold"),
            text_color=COLORS.text_muted,
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 5))
        self.file_entry = ctk.CTkEntry(
            file_frame,
            textvariable=self.file_var,
            height=38,
            fg_color=COLORS.surface_alt,
            border_color=COLORS.border,
            text_color=COLORS.text,
            font=font(12),
            corner_radius=9,
        )
        self.file_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        if not self.material:
            ctk.CTkLabel(
                file_frame,
                text="Con «Sfoglia» puoi selezionare piu' file insieme (Ctrl/Maiusc): "
                "si crea un materiale per ognuno, con il titolo preso dal nome del file.",
                font=font(9),
                text_color=COLORS.text_faint,
                anchor="w",
                wraplength=560,
                justify="left",
            ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ctk.CTkButton(
            file_frame,
            text="Sfoglia",
            width=92,
            height=38,
            font=font(11, "bold"),
            fg_color=COLORS.surface_alt,
            hover_color=COLORS.surface_hover,
            border_width=1,
            border_color=COLORS.border,
            text_color=COLORS.text,
            corner_radius=9,
            command=self._browse,
        ).grid(row=1, column=1)

        row = ctk.CTkFrame(form, fg_color="transparent")
        row.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        row.grid_columnconfigure((0, 1), weight=1)
        target_frame, _entry = self._labeled_entry(
            row, "Ore previste", self.target_var, "es. 8"
        )
        target_frame.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        event_frame = ctk.CTkFrame(row, fg_color="transparent")
        event_frame.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ctk.CTkLabel(
            event_frame,
            text="Evento collegato",
            font=font(11, "bold"),
            text_color=COLORS.text_muted,
            anchor="w",
        ).pack(anchor="w", pady=(0, 5))
        self.event_combo = ctk.CTkComboBox(
            event_frame,
            values=["Nessun evento"],
            height=38,
            fg_color=COLORS.surface_alt,
            border_color=COLORS.border,
            button_color=COLORS.surface_hover,
            button_hover_color=COLORS.border,
            dropdown_fg_color=COLORS.surface,
            dropdown_hover_color=COLORS.surface_hover,
            text_color=COLORS.text,
            font=font(11),
            dropdown_font=font(11),
            corner_radius=9,
        )
        self.event_combo.pack(fill="x")
        self.event_combo.set("Nessun evento")

        year_frame = ctk.CTkFrame(form, fg_color="transparent")
        year_frame.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        ctk.CTkLabel(
            year_frame,
            text="Anno accademico",
            font=font(11, "bold"),
            text_color=COLORS.text_muted,
            anchor="w",
        ).pack(anchor="w", pady=(0, 5))
        try:
            used_years = self.db.list_academic_years()
        except Exception:
            used_years = []
        self.year_combo = ctk.CTkComboBox(
            year_frame,
            values=academic_year_choices(used_years) + [ACADEMIC_YEAR_UNKNOWN],
            height=38,
            width=200,
            fg_color=COLORS.surface_alt,
            border_color=COLORS.border,
            button_color=COLORS.surface_hover,
            button_hover_color=COLORS.border,
            dropdown_fg_color=COLORS.surface,
            dropdown_hover_color=COLORS.surface_hover,
            text_color=COLORS.text,
            font=font(11),
            dropdown_font=font(11),
            corner_radius=9,
        )
        self.year_combo.pack(anchor="w")
        self.year_combo.set(academic_year_for())
        ctk.CTkLabel(
            year_frame,
            text="Anno del corso da cui provengono le slide (es. 2025/26). Si puo' anche scrivere.",
            font=font(9),
            text_color=COLORS.text_faint,
            anchor="w",
        ).pack(anchor="w", pady=(4, 0))

        info = Card(self)
        info.grid(row=3, column=0, sticky="ew", padx=28, pady=(4, 16))
        ctk.CTkLabel(
            info,
            text="Le ore studiate si aggiungono automaticamente quando completi un "
            "Pomodoro avviato da questo materiale. Il file viene copiato nella "
            "cartella della materia (Slide o Documenti); l'originale resta dov'e'.",
            font=font(10),
            text_color=COLORS.text_muted,
            wraplength=560,
            justify="left",
            anchor="w",
        ).pack(anchor="w", padx=16, pady=12)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=4, column=0, sticky="ew", padx=28, pady=(0, 24))
        buttons.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            buttons,
            text="Annulla",
            height=40,
            width=110,
            font=font(12),
            fg_color="transparent",
            hover_color=COLORS.surface_alt,
            border_width=1,
            border_color=COLORS.border,
            text_color=COLORS.text_muted,
            corner_radius=10,
            command=self.destroy,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            buttons,
            text="Salva materiale",
            height=40,
            width=170,
            font=font(12, "bold"),
            fg_color=COLORS.accent,
            hover_color=COLORS.accent_hover,
            text_color=COLORS.bg,
            corner_radius=10,
            command=self._save,
        ).grid(row=0, column=1, sticky="e")

    def _field(
        self,
        parent,
        row: int,
        label: str,
        variable: ctk.StringVar,
        placeholder: str,
        hint: str = "",
    ) -> ctk.CTkEntry:
        frame, entry = self._labeled_entry(parent, label, variable, placeholder)
        if hint:
            ctk.CTkLabel(
                frame,
                text=hint,
                font=font(9),
                text_color=COLORS.text_faint,
                anchor="w",
                wraplength=560,
                justify="left",
            ).pack(anchor="w", pady=(4, 0))
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        return entry

    def _labeled_entry(
        self,
        parent,
        label: str,
        variable: ctk.StringVar,
        placeholder: str,
    ) -> tuple[ctk.CTkFrame, ctk.CTkEntry]:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(
            frame,
            text=label,
            font=font(11, "bold"),
            text_color=COLORS.text_muted,
            anchor="w",
        ).pack(anchor="w", pady=(0, 5))
        entry = ctk.CTkEntry(
            frame,
            textvariable=variable,
            height=38,
            placeholder_text=placeholder,
            fg_color=COLORS.surface_alt,
            border_color=COLORS.border,
            text_color=COLORS.text,
            font=font(12),
            corner_radius=9,
        )
        entry.pack(fill="x")
        return frame, entry

    # ------------------------------------------------------------------ data
    def _populate(self) -> None:
        if self.material:
            self.title_var.set(str(self.material.get("title") or ""))
            self.target_var.set(f"{safe_float(self.material.get('target_hours', 0)):g}")
            self.file_var.set(str(self.material.get("file_path") or ""))
            year = normalize_academic_year(self.material.get("academic_year"))
            self.year_combo.set(year or ACADEMIC_YEAR_UNKNOWN)
            if self.allow_subject_change:
                self.subject_var.set(str(self.material.get("subject") or ""))
        try:
            events = self.db.list_events_for_materials()
        except Exception:
            events = []
        self._event_ids = [None]
        self._event_labels = ["Nessun evento"]
        for event in events:
            label = (
                f"{str(event['title'])[:60]} · "
                f"{format_event_datetime(str(event['start']))}"
            )
            if label in self._event_labels:
                label = f"{label} (#{event['id']})"
            self._event_labels.append(label)
            self._event_ids.append(int(event["id"]))
        linked_id: int | None = None
        if self.material and self.material.get("calendar_event_id"):
            linked_id = int(self.material["calendar_event_id"])
        if linked_id is not None and linked_id not in self._event_ids:
            try:
                linked_event = self.db.get_event(linked_id)
            except Exception:
                linked_event = None
            if linked_event:
                label = (
                    f"{str(linked_event['title'])[:60]} · "
                    f"{format_event_datetime(str(linked_event['start']))}"
                )
                if label in self._event_labels:
                    label = f"{label} (#{linked_event['id']})"
                self._event_labels.append(label)
                self._event_ids.append(linked_id)
        self.event_combo.configure(values=self._event_labels)
        selected = "Nessun evento"
        if linked_id is not None:
            try:
                index = self._event_ids.index(linked_id)
                selected = self._event_labels[index]
            except ValueError:
                selected = "Nessun evento"
        self.event_combo.set(selected)

    def _browse(self) -> None:
        filetypes = [
            ("Documenti", "*.pdf *.doc *.docx *.txt *.md *.ppt *.pptx *.xlsx"),
            ("PDF", "*.pdf"),
            ("Tutti i file", "*.*"),
        ]
        if self.material:
            path = filedialog.askopenfilename(parent=self, title="Seleziona materiale", filetypes=filetypes)
            paths = [path] if path else []
        else:
            paths = list(filedialog.askopenfilenames(
                parent=self, title="Seleziona uno o piu' materiali", filetypes=filetypes
            ) or [])
        if not paths:
            return
        if len(paths) == 1:
            self._set_files([])
            self.file_var.set(paths[0])
            if not self.title_var.get().strip():
                self.title_var.set(Path(paths[0]).stem)
            return
        self._set_files(paths)

    def _set_files(self, paths: list[str]) -> None:
        """Piu' file: il titolo di ognuno e' il nome del file, campi bloccati."""
        self._files = list(paths)
        if self._files:
            names = ", ".join(Path(path).name for path in self._files[:3])
            more = f" e altri {len(self._files) - 3}" if len(self._files) > 3 else ""
            self._files_label = f"{len(self._files)} file: {names}{more}"
            self.file_var.set(self._files_label)
            self.title_var.set("(dal nome di ogni file)")
            self.title_entry.configure(state="disabled")
            self.file_entry.configure(state="disabled")
        else:
            self._files_label = ""
            if self.title_var.get() == "(dal nome di ogni file)":
                self.title_var.set("")
            self.title_entry.configure(state="normal")
            self.file_entry.configure(state="normal")

    def _save_many(self, subject: str, target: float, event_id: int | None, year: str) -> None:
        saved, errors = 0, []
        self.app.subject_folder(subject)
        for path in self._files:
            try:
                file_path = path
                if Path(path).is_file():
                    file_path = str(self.app.subject_folders.copy_material(path, subject))
                if self.db.find_material_by_path(file_path) is not None:
                    errors.append(f"{Path(path).name}: gia' presente, saltato.")
                    continue
                self.db.add_material(Path(path).stem, file_path, subject, target, event_id, academic_year=year)
                saved += 1
            except Exception as exc:
                self.app.logger.exception("Salvataggio materiale non riuscito.")
                errors.append(f"{Path(path).name}: {exc}")
        if errors:
            messagebox.showwarning("Alcuni file non aggiunti", "\n".join(errors[:10]), parent=self)
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        self.app.toast(f"Aggiunti {saved} materiali.")
        self.section.notify_change()

    def _save(self) -> None:
        title = self.title_var.get().strip()
        file_path = self.file_var.get().strip()
        subject = (
            self.subject_var.get().strip()
            if self.allow_subject_change
            else self.fixed_subject.strip()
        )
        target = safe_float(self.target_var.get(), -1)
        many = bool(self._files) and self.file_var.get() == self._files_label
        if many:
            title, file_path = "-", self._files[0]
        if not title:
            messagebox.showerror(
                "Titolo mancante",
                "Inserisci un titolo per il materiale.",
                parent=self,
            )
            return
        if not subject:
            messagebox.showerror(
                "Materia mancante",
                "Seleziona la materia a cui collegare il materiale.",
                parent=self,
            )
            return
        if not file_path:
            messagebox.showerror(
                "File mancante",
                "Seleziona un file PDF o documento.",
                parent=self,
            )
            return
        if not many and not Path(file_path).exists():
            if not messagebox.askyesno(
                "File non trovato",
                "Il file indicato non esiste. Salvarlo comunque?",
                parent=self,
            ):
                return
        if target < 0 or target > 10000:
            messagebox.showerror(
                "Valore non valido",
                "Le ore previste devono essere tra 0 e 10000.",
                parent=self,
            )
            return
        raw_year = self.year_combo.get().strip()
        year = normalize_academic_year(raw_year)
        if raw_year and raw_year != ACADEMIC_YEAR_UNKNOWN and not year:
            messagebox.showerror(
                "Anno non valido",
                "Scrivi l'anno accademico come 2025/26 oppure scegli 'Non indicato'.",
                parent=self,
            )
            return
        label = self.event_combo.get()
        try:
            event_id = self._event_ids[self._event_labels.index(label)]
        except (ValueError, IndexError):
            event_id = None
        if many:
            self._save_many(subject, target, event_id, year)
            return
        try:
            self.app.subject_folder(subject)
            unchanged = bool(self.material) and _same_file(file_path, str(self.material.get("file_path") or ""))
            if Path(file_path).is_file() and not unchanged:
                # Il file scelto viene copiato nella cartella della materia.
                file_path = str(self.app.subject_folders.copy_material(file_path, subject))
            if self.material:
                self.db.update_material(
                    int(self.material["id"]),
                    title,
                    file_path,
                    subject,
                    target,
                    event_id,
                    academic_year=year,
                )
            else:
                self.db.add_material(title, file_path, subject, target, event_id, academic_year=year)
        except Exception as exc:
            self.app.logger.exception("Salvataggio materiale non riuscito.")
            messagebox.showerror("Salvataggio non riuscito", str(exc), parent=self)
            return
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        self.app.toast("Materiale salvato.")
        self.section.notify_change()


def _same_file(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _format_size(size: int) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.1f} GB"
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.1f} MB"
    return f"{max(1, round(size / 1024))} KB"
