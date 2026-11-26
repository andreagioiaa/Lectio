"""Dialogo «Collega a materia»: trasforma un documento di PDF e Slide in un capitolo di appunti.

Il capitolo (blocco di appunti) ha una materia, un numero e un titolo: nella
pagina della materia i capitoli compaiono in ordine crescente di numero
(1. Introduzione, 2. Esempio, ...).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any

import customtkinter as ctk

from ..config import COLORS
from .theme import font


class NoteBlockDialog(ctk.CTkToplevel):
    """Dialogo modale: ``show()`` restituisce None se annullato, altrimenti
    ``{"subject", "position", "title"}``."""

    def __init__(
        self,
        parent,
        subjects: list[str],
        subject: str,
        position: int,
        title: str,
        editing: bool = False,
        next_position=None,
    ):
        super().__init__(parent)
        self.parent = parent
        self.result: dict[str, Any] | None = None
        self._next_position = next_position
        self._position_touched = editing
        self.title("Capitolo di appunti" if editing else "Collega a una materia")
        self.configure(fg_color=COLORS.bg)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        body = ctk.CTkFrame(self, fg_color=COLORS.surface, corner_radius=14, border_width=1,
                            border_color=COLORS.border)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        self.body = body
        ctk.CTkLabel(
            body, text="Modifica capitolo" if editing else "Collega a una materia", font=font(13, "bold"),
            text_color=COLORS.text, anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 2))
        ctk.CTkLabel(
            body,
            text="Il documento diventa un capitolo degli appunti della materia: nella sua pagina i capitoli "
                 "sono ordinati per numero e si riaprono qui con slide e testo.",
            font=font(10), text_color=COLORS.text_faint, anchor="w", justify="left", wraplength=440,
        ).pack(fill="x", padx=16, pady=(0, 10))

        self._label(body, "Materia")
        self.subject_combo = ctk.CTkComboBox(
            body, values=subjects or [""], height=34, font=font(12), dropdown_font=font(12),
            fg_color=COLORS.surface_alt, border_color=COLORS.border, button_color=COLORS.surface_hover,
            button_hover_color=COLORS.border, dropdown_fg_color=COLORS.surface,
            dropdown_hover_color=COLORS.surface_hover, text_color=COLORS.text, corner_radius=9,
            command=lambda _v: self._on_subject_change(),
        )
        self.subject_combo.set(subject)
        self.subject_combo.pack(fill="x", padx=16, pady=(0, 10))
        self.subject_combo.bind("<FocusOut>", lambda _e: self._on_subject_change())

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 10))
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text="N.", font=font(10, "bold"), text_color=COLORS.text_muted, anchor="w").grid(
            row=0, column=0, sticky="w")
        ctk.CTkLabel(row, text="Titolo del capitolo", font=font(10, "bold"), text_color=COLORS.text_muted,
                     anchor="w").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.position_var = ctk.StringVar(value=str(position))
        self.position_entry = ctk.CTkEntry(
            row, textvariable=self.position_var, width=60, height=34, font=font(12),
            fg_color=COLORS.surface_alt, border_color=COLORS.border, text_color=COLORS.text,
        )
        self.position_entry.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.position_entry.bind("<Key>", lambda _e: setattr(self, "_position_touched", True))
        self.title_var = ctk.StringVar(value=title)
        self.title_entry = ctk.CTkEntry(
            row, textvariable=self.title_var, height=34, font=font(12),
            fg_color=COLORS.surface_alt, border_color=COLORS.border, text_color=COLORS.text,
        )
        self.title_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(8, 14))
        buttons.grid_columnconfigure(0, weight=1)
        self._button(buttons, "Annulla", self.cancel).grid(row=0, column=1, padx=(0, 6))
        self._button(buttons, "Salva" if editing else "Collega", self.confirm, primary=True).grid(row=0, column=2)

        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda _e: self.cancel())
        self.bind("<Return>", lambda _e: self.confirm())
        self._place()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.after(60, lambda: self.title_entry.focus_set() if self.winfo_exists() else None)

    def _label(self, parent, text: str) -> None:
        ctk.CTkLabel(parent, text=text, font=font(10, "bold"), text_color=COLORS.text_muted, anchor="w").pack(
            fill="x", padx=16, pady=(0, 4))

    def _button(self, parent, text: str, command, primary: bool = False) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, height=32, width=110, font=font(11, "bold"), corner_radius=9,
            fg_color=COLORS.accent if primary else COLORS.surface_alt,
            hover_color=COLORS.accent_dark if primary else COLORS.surface_hover,
            text_color=COLORS.text, border_width=0 if primary else 1, border_color=COLORS.border, command=command,
        )

    def _place(self) -> None:
        try:
            self.update_idletasks()
            width = max(500, self.body.winfo_reqwidth() + 24)
            height = self.body.winfo_reqheight() + 24
            top = self.parent.winfo_toplevel()
            x = top.winfo_rootx() + max(0, (top.winfo_width() - width) // 2)
            y = top.winfo_rooty() + max(0, (top.winfo_height() - height) // 3)
            self.geometry(f"{width}x{height}+{x}+{y}")
        except tk.TclError:
            pass

    def _on_subject_change(self) -> None:
        """Propone il numero successivo all'ultimo capitolo della materia scelta."""
        if self._position_touched or self._next_position is None:
            return
        subject = self.subject_combo.get().strip()
        if subject:
            try:
                self.position_var.set(str(self._next_position(subject)))
            except Exception:
                pass

    def confirm(self) -> None:
        subject = self.subject_combo.get().strip()
        title = self.title_var.get().strip()
        if not subject:
            messagebox.showwarning("Collega a una materia", "Scegli o scrivi il nome della materia.", parent=self)
            return
        try:
            position = int(self.position_var.get().strip())
            if position < 1:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Collega a una materia", "Il numero del capitolo deve essere un intero ≥ 1.",
                                   parent=self)
            return
        if not title:
            messagebox.showwarning("Collega a una materia", "Scrivi il titolo del capitolo.", parent=self)
            return
        self.result = {"subject": subject, "position": position, "title": title}
        self._close()

    def cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self) -> dict[str, Any] | None:
        self.wait_window(self)
        return self.result
