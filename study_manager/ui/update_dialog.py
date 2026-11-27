"""Finestra "Aggiornamento disponibile" (facoltativo o obbligatorio).

La finestra mostra versione e novita', poi l'avanzamento del download. Tutta la
logica (download, backup, riavvio) sta in ``LectioApp``: qui ci sono solo i
widget e le callback.

Con un aggiornamento obbligatorio la finestra e' modale e non si puo' chiudere:
restano solo "Aggiorna ora" e "Chiudi Lectio".
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from ..config import APP_VERSION, COLORS
from ..services.updater import UpdateInfo
from .theme import font
from .widgets import center_window


class UpdateDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        info: UpdateInfo,
        *,
        can_install: bool,
        on_update: Callable[["UpdateDialog"], None],
        on_skip: Callable[[UpdateInfo], None],
        on_quit: Callable[[], None],
    ):
        super().__init__(master)
        self.info = info
        self._on_update = on_update
        self._on_skip = on_skip
        self._on_quit = on_quit
        self.busy = False
        self.title("Aggiornamento obbligatorio" if info.required else "Aggiornamento disponibile")
        self.configure(fg_color=COLORS.bg)
        self.resizable(False, False)
        self.transient(master)
        self._build(can_install)
        center_window(self, 520, 420 if info.notes else 260)
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)
        if info.required:
            self.after(80, self._make_modal)
        else:
            self.bind("<Escape>", lambda _e: self._on_window_close())
        self.after(80, self.lift)

    # ------------------------------------------------------------------ layout
    def _build(self, can_install: bool) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=22, pady=20)
        heading = (
            f"Serve Lectio {self.info.latest}"
            if self.info.required
            else f"E' disponibile Lectio {self.info.latest}"
        )
        ctk.CTkLabel(body, text=heading, font=font(17, "bold"), text_color=COLORS.text, anchor="w").pack(
            fill="x"
        )
        if self.info.required:
            detail = (
                f"Stai usando la v{APP_VERSION}, che non e' piu' supportata "
                f"(minima: v{self.info.min_version}). Aggiorna per continuare a usare Lectio: "
                "prima dell'installazione viene fatto un backup automatico dei dati."
            )
        else:
            detail = f"Stai usando la v{APP_VERSION}. L'aggiornamento richiede un riavvio di Lectio."
        if not can_install:
            detail += "\n\nQuesta copia non puo' aggiornarsi da sola: si aprira' la pagina di download."
        ctk.CTkLabel(
            body, text=detail, font=font(11), text_color=COLORS.text_muted, anchor="w", justify="left",
            wraplength=470,
        ).pack(fill="x", pady=(6, 10))

        if self.info.notes:
            ctk.CTkLabel(body, text="Novita'", font=font(11, "bold"), text_color=COLORS.text, anchor="w").pack(
                fill="x"
            )
            notes = ctk.CTkTextbox(
                body, height=150, font=font(11), fg_color=COLORS.surface, border_color=COLORS.border,
                border_width=1, text_color=COLORS.text, wrap="word",
            )
            notes.pack(fill="both", expand=True, pady=(4, 10))
            notes.insert("1.0", self.info.notes)
            notes.configure(state="disabled")

        self.status = ctk.CTkLabel(body, text="", font=font(10), text_color=COLORS.text_muted, anchor="w")
        self.status.pack(fill="x")
        self.progress = ctk.CTkProgressBar(body, height=8, progress_color=COLORS.accent)
        self.progress.set(0)

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", side="bottom", pady=(10, 0))
        self.update_button = ctk.CTkButton(
            buttons,
            text="Aggiorna ora" if can_install else "Apri la pagina di download",
            height=36, font=font(12, "bold"), fg_color=COLORS.accent, hover_color=COLORS.accent_hover,
            corner_radius=18, command=self._update_clicked,
        )
        self.update_button.pack(side="right")
        secondary = dict(
            height=36, font=font(11), fg_color="transparent", hover_color=COLORS.surface_alt,
            text_color=COLORS.text_muted, border_width=1, border_color=COLORS.border, corner_radius=18,
        )
        if self.info.required:
            self.quit_button = ctk.CTkButton(buttons, text="Chiudi Lectio", command=self._on_quit, **secondary)
            self.quit_button.pack(side="right", padx=(0, 8))
        else:
            self.later_button = ctk.CTkButton(buttons, text="Piu' tardi", command=self.close, **secondary)
            self.later_button.pack(side="right", padx=(0, 8))
            self.skip_button = ctk.CTkButton(
                buttons, text="Salta questa versione", command=self._skip_clicked, **secondary
            )
            self.skip_button.pack(side="left")

    def _make_modal(self) -> None:
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ azioni
    def _update_clicked(self) -> None:
        if not self.busy:
            self._on_update(self)

    def _skip_clicked(self) -> None:
        self._on_skip(self.info)
        self.close()

    def _on_window_close(self) -> None:
        if self.busy:
            return
        if self.info.required:
            self._on_quit()
        else:
            self.close()

    def close(self) -> None:
        if self.busy:
            return
        try:
            self.grab_release()
            self.destroy()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ stato
    def set_busy(self, busy: bool, message: str = "") -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for name in ("update_button", "quit_button", "later_button", "skip_button"):
            button = getattr(self, name, None)
            if button is not None:
                button.configure(state=state)
        if busy:
            self.progress.pack(fill="x", pady=(6, 0), before=self.status)
        self.set_status(message)

    def set_status(self, message: str, error: bool = False) -> None:
        try:
            self.status.configure(text=message, text_color=COLORS.red if error else COLORS.text_muted)
        except tk.TclError:
            pass

    def set_progress(self, received: int, total: int) -> None:
        try:
            if total > 0:
                self.progress.set(min(1.0, received / total))
                self.set_status(f"Download in corso... {received / 1048576:.1f} di {total / 1048576:.1f} MB")
            else:
                self.set_status(f"Download in corso... {received / 1048576:.1f} MB")
        except tk.TclError:
            pass
