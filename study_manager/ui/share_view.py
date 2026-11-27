"""Dialoghi «Esporta materia (ZIP)» e «Importa materia (ZIP)»."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Callable

import customtkinter as ctk

from ..config import COLORS
from ..services.subject_folders import folder_name
from ..services.subject_share import (
    DEFAULT_PARTS,
    PART_LABELS,
    PART_PERSONAL,
    ShareCancelled,
    ShareError,
    import_share,
    inspect_share,
    plan_export,
    suggest_import_name,
    write_export,
)
from .summary_view import ProgressDialog, _Modal, _plural, run_in_background
from .theme import font

LOGGER = logging.getLogger(__name__)


def format_size(size: int) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.1f} GB"
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.1f} MB"
    return f"{max(0, round(size / 1024))} KB" if size else "0 KB"


def _default_folder() -> str:
    for candidate in (Path.home() / "Desktop", Path.home() / "Documents", Path.home()):
        if candidate.is_dir():
            return str(candidate)
    return str(Path.home())


def _show_in_folder(path: Path) -> None:
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(path)])  # noqa: S603,S607 - percorso scelto dall'utente
        else:
            os.startfile(str(path.parent))  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        LOGGER.debug("Apertura della cartella non riuscita.", exc_info=True)


# ---------------------------------------------------------------------------
# Esporta
# ---------------------------------------------------------------------------
class ExportSubjectDialog(_Modal):
    """Sceglie le parti da includere (con la dimensione) e crea lo ZIP della materia."""

    def __init__(self, parent, app, subject: str):
        super().__init__(parent, "Esporta materia (ZIP)")
        self.app = app
        self.subject = subject
        self.plan = plan_export(app.db, app.subject_folders, subject)
        self.heading(
            f"Esporta «{subject}»",
            "Uno ZIP con le cartelle della materia e un LEGGIMI.md, da passare ad altri studenti. Chi usa "
            "Lectio lo apre con «Importa materia (ZIP)» e ritrova tutto collegato.",
            wrap=520,
        )
        self.vars: dict[str, tk.BooleanVar] = {}
        rows = ctk.CTkFrame(self.body, fg_color="transparent")
        rows.pack(fill="x", padx=16, pady=(0, 6))
        rows.grid_columnconfigure(0, weight=1)
        for row, (part, label) in enumerate(PART_LABELS.items()):
            count = self.plan.count(part)
            if part == PART_PERSONAL:
                detail = "solo per te: non serve a chi riceve lo ZIP"
            else:
                pieces = [self._data_label(part)] if self._data_label(part) else []
                if count:
                    pieces.append(f"{_plural(count, 'file', 'file')} · {format_size(self.plan.size(part))}")
                detail = " · ".join(pieces) or "vuoto"
            var = tk.BooleanVar(value=DEFAULT_PARTS[part] and (count > 0 or part == PART_PERSONAL or
                                                               self._has_data(part)))
            self.vars[part] = var
            ctk.CTkCheckBox(rows, text=label, variable=var, font=font(11), text_color=COLORS.text,
                            command=self._update_total, checkbox_width=18, checkbox_height=18).grid(
                row=row, column=0, sticky="w", pady=3)
            ctk.CTkLabel(rows, text=detail, font=font(10), text_color=COLORS.text_faint, anchor="e").grid(
                row=row, column=1, sticky="e", padx=(12, 0))
        self.total_label = ctk.CTkLabel(self.body, text="", font=font(11, "bold"), text_color=COLORS.accent,
                                        anchor="w")
        self.total_label.pack(fill="x", padx=16, pady=(6, 0))
        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.pack(side="bottom", fill="x", padx=16, pady=(10, 14))
        buttons.grid_columnconfigure(0, weight=1)
        self.action(buttons, "Annulla", self.cancel).grid(row=0, column=1, padx=(0, 6))
        self.action(buttons, "Salva ZIP…", self.confirm, primary=True).grid(row=0, column=2)
        self._update_total()
        self.place(600)

    def _data_label(self, part: str) -> str:
        data = self.plan.data
        count, one, many = {
            "notes": (len(data["note_blocks"]), "capitolo", "capitoli"),
            "summary": (len(data["summary_chapters"]), "capitolo", "capitoli"),
            "exams": (len(data["exam_questions"]), "domanda", "domande"),
            "flashcards": (len(data["flashcards"]), "scheda", "schede"),
        }.get(part, (0, "", ""))
        return _plural(count, one, many) if count else ""

    def _has_data(self, part: str) -> bool:
        data = self.plan.data
        return {"notes": bool(data["note_blocks"]), "summary": bool(data["summary_chapters"]),
                "exams": bool(data["exam_questions"] or data["exam_sessions"]),
                "flashcards": bool(data["flashcards"])}.get(part, False)

    def _parts(self) -> dict[str, bool]:
        return {part: var.get() for part, var in self.vars.items()}

    def _update_total(self) -> None:
        parts = self._parts()
        size = sum(self.plan.size(part) for part, on in parts.items() if on)
        self.total_label.configure(text=f"Dimensione stimata: {format_size(size)}")

    def confirm(self) -> None:
        parts = self._parts()
        if not any(on for part, on in parts.items() if part != PART_PERSONAL):
            messagebox.showwarning("Esporta materia", "Scegli almeno una parte da esportare.", parent=self)
            return
        target = filedialog.asksaveasfilename(
            parent=self, title="Salva lo ZIP della materia", defaultextension=".zip",
            initialdir=_default_folder(), initialfile=f"{folder_name(self.subject) or 'Materia'}.zip",
            filetypes=[("Archivio ZIP", "*.zip")],
        )
        if not target:
            return
        self.close()
        app, plan = self.app, self.plan
        cancel = threading.Event()
        progress = ProgressDialog(app, "Esporta materia", f"Creo {Path(target).name}…", on_cancel=cancel.set)

        def report(fraction: float, message: str) -> None:
            app.post(lambda: progress.update_progress(fraction, message))

        def done(path: Path) -> None:
            progress.close()
            app.toast(f"Materia esportata: {path.name} ({format_size(path.stat().st_size)})")
            _show_in_folder(path)

        def failed(exc: BaseException) -> None:
            progress.close()
            if isinstance(exc, ShareCancelled):
                app.toast("Esportazione annullata.")
                return
            LOGGER.warning("Esportazione della materia non riuscita: %s", exc)
            messagebox.showerror("Esporta materia", f"Esportazione non riuscita:\n{exc}", parent=app)

        run_in_background(app, lambda: write_export(plan, parts, target, report, cancel.is_set), done, failed)


# ---------------------------------------------------------------------------
# Importa
# ---------------------------------------------------------------------------
class ImportSubjectDialog(_Modal):
    """Anteprima dello ZIP e scelta: nuova materia, unione o nome diverso.

    ``show()`` -> nome della materia di destinazione o None.
    """

    def __init__(self, parent, db, manifest: dict[str, Any], file_name: str):
        super().__init__(parent, "Importa materia (ZIP)")
        self.db = db
        wanted = str(manifest["subject"]["name"]).strip()
        exists = db.get_subject(wanted) is not None
        counts = manifest.get("counts") or {}
        exported = str(manifest.get("exported_at") or "")[:10]
        self.heading(
            f"Importa «{wanted}»",
            f"{file_name} · {format_size(int(manifest.get('total_bytes') or 0))}"
            + (f" · esportato il {exported[8:10]}/{exported[5:7]}/{exported[:4]}" if len(exported) == 10 else "")
            + (f" con Lectio {manifest.get('app_version')}" if manifest.get("app_version") else ""),
            wrap=520,
        )
        lines = [
            ("Materiali (PDF, documenti)", counts.get("materials", len(manifest["materials"]))),
            ("Capitoli di appunti", counts.get("note_blocks", len(manifest["note_blocks"]))),
            ("Capitoli del riassunto", counts.get("summary_chapters", len(manifest["summary_chapters"]))),
            ("Appelli d'esame", counts.get("exam_sessions", len(manifest["exam_sessions"]))),
            ("Domande d'esame", counts.get("exam_questions", len(manifest["exam_questions"]))),
            ("Flashcard (nuove, da studiare)", counts.get("flashcards", len(manifest["flashcards"]))),
            ("File in totale", counts.get("files", 0)),
        ]
        grid = ctk.CTkFrame(self.body, fg_color=COLORS.surface_alt, corner_radius=10)
        grid.pack(fill="x", padx=16, pady=(0, 10))
        grid.grid_columnconfigure(0, weight=1)
        for row, (label, value) in enumerate(lines):
            ctk.CTkLabel(grid, text=label, font=font(11), text_color=COLORS.text_muted, anchor="w").grid(
                row=row, column=0, sticky="w", padx=12, pady=(6 if row == 0 else 1, 6 if row == len(lines) - 1 else 1))
            ctk.CTkLabel(grid, text=str(value), font=font(11, "bold"), text_color=COLORS.text, anchor="e").grid(
                row=row, column=1, sticky="e", padx=12)
        self.mode_var = tk.StringVar(value="merge" if exists else "new")
        self.name_var = ctk.StringVar(value=suggest_import_name(db, wanted) if exists else wanted)
        self.wanted = wanted
        if exists:
            ctk.CTkLabel(self.body, text=f"La materia «{wanted}» esiste gia'.", font=font(11, "bold"),
                         text_color=COLORS.amber, anchor="w").pack(fill="x", padx=16, pady=(0, 4))
            ctk.CTkRadioButton(self.body, text="Unisci: aggiunge solo i file e i dati che mancano",
                               variable=self.mode_var, value="merge", font=font(11), text_color=COLORS.text,
                               command=self._sync).pack(anchor="w", padx=16, pady=3)
            ctk.CTkRadioButton(self.body, text="Importa come nuova materia:", variable=self.mode_var, value="new",
                               font=font(11), text_color=COLORS.text, command=self._sync).pack(anchor="w", padx=16,
                                                                                                pady=3)
        else:
            self.label(self.body, "Nome della materia").pack(fill="x", padx=16)
        self.name_entry = ctk.CTkEntry(self.body, textvariable=self.name_var, height=34, font=font(12),
                                       fg_color=COLORS.surface_alt, border_color=COLORS.border,
                                       text_color=COLORS.text)
        self.name_entry.pack(fill="x", padx=36 if exists else 16, pady=(2, 8))
        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.pack(side="bottom", fill="x", padx=16, pady=(8, 14))
        buttons.grid_columnconfigure(0, weight=1)
        self.action(buttons, "Annulla", self.cancel).grid(row=0, column=1, padx=(0, 6))
        self.action(buttons, "Importa", self.confirm, primary=True).grid(row=0, column=2)
        self._sync()
        self.place(560)

    def _sync(self) -> None:
        self.name_entry.configure(state="normal" if self.mode_var.get() == "new" else "disabled")

    def confirm(self) -> None:
        if self.mode_var.get() == "merge":
            self.result = self.wanted
            self.close()
            return
        name = self.name_var.get().strip()
        if not name or not folder_name(name):
            messagebox.showwarning("Importa materia", "Scrivi un nome valido per la materia.", parent=self)
            return
        if self.db.get_subject(name) is not None and name.casefold() != self.wanted.casefold():
            if not messagebox.askyesno("Importa materia",
                                       f"«{name}» esiste gia': unire il contenuto dello ZIP a quella materia?",
                                       parent=self):
                return
        self.result = name
        self.close()


def import_subject_flow(app, parent, on_done: Callable[[str], None]) -> None:
    """Sceglie lo ZIP, mostra l'anteprima e importa in background."""
    path = filedialog.askopenfilename(parent=parent, title="Importa materia (ZIP)", initialdir=_default_folder(),
                                      filetypes=[("Archivio ZIP", "*.zip")])
    if not path:
        return
    try:
        manifest = inspect_share(path)
    except ShareError as exc:
        messagebox.showerror("Importa materia", str(exc), parent=parent)
        return
    subject = ImportSubjectDialog(parent, app.db, manifest, Path(path).name).show()
    if not subject:
        return
    cancel = threading.Event()
    progress = ProgressDialog(app, "Importa materia", f"Importo in «{subject}»…", on_cancel=cancel.set)

    def report(fraction: float, message: str) -> None:
        app.post(lambda: progress.update_progress(fraction, message))

    def done(result: dict[str, Any]) -> None:
        progress.close()
        parts = [f"{result['files']} file"]
        for key, one, many in (("materials", "materiale", "materiali"),
                               ("note_blocks", "capitolo di appunti", "capitoli di appunti"),
                               ("summary_chapters", "capitolo del riassunto", "capitoli del riassunto"),
                               ("exam_questions", "domanda d'esame", "domande d'esame"),
                               ("flashcards", "flashcard", "flashcard")):
            if result.get(key):
                parts.append(_plural(int(result[key]), one, many))
        message = f"Importati in «{result['subject']}»: " + ", ".join(parts) + "."
        if result.get("warnings"):
            message += "\n\nSaltati:\n" + "\n".join(f"• {line}" for line in result["warnings"][:8])
        messagebox.showinfo("Importa materia", message, parent=app)
        on_done(str(result["subject"]))

    def failed(exc: BaseException) -> None:
        progress.close()
        if isinstance(exc, ShareCancelled):
            app.toast("Importazione annullata: i file gia' copiati restano nella cartella della materia.")
            return
        LOGGER.warning("Importazione della materia non riuscita: %s", exc)
        messagebox.showerror("Importa materia", f"Importazione non riuscita:\n{exc}", parent=app)

    run_in_background(app, lambda: import_share(app.db, app.subject_folders, path, subject, report, cancel.is_set),
                      done, failed)
