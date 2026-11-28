"""Scheda «Sincronizzazione tra PC» delle Impostazioni."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from ..config import COLORS
from ..paths import SYNC_MARKER_NAME, app_data_dir
from ..services import sync_folder as sync
from .share_view import format_size
from .summary_view import ProgressDialog, run_in_background
from .theme import font
from .widgets import Card, info_button

LOGGER = logging.getLogger(__name__)


def _folder_size(*folders: Path) -> int:
    total = 0
    for folder in folders:
        if Path(folder).is_dir():
            for path in Path(folder).rglob("*"):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
        elif Path(folder).is_file():
            total += Path(folder).stat().st_size
    return total


def _button(parent, text: str, command, primary: bool = False, width: int = 190) -> ctk.CTkButton:
    return ctk.CTkButton(
        parent, text=text, height=38, width=width, font=font(11, "bold"), corner_radius=9,
        fg_color=COLORS.accent if primary else COLORS.surface_alt,
        hover_color=COLORS.accent_hover if primary else COLORS.surface_hover,
        border_width=0 if primary else 1, border_color=COLORS.border,
        text_color=COLORS.bg if primary else COLORS.text, command=command,
    )


class SyncCard(Card):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.paths = app.paths
        self.grid_columnconfigure(0, weight=1)
        title = ctk.CTkFrame(self, fg_color="transparent")
        title.grid(row=0, column=0, sticky="w", padx=22, pady=(18, 4))
        ctk.CTkLabel(title, text="Sincronizzazione tra PC", font=font(16, "bold"), text_color=COLORS.text).pack(
            side="left")
        info_button(
            title, "Sincronizzazione tra PC",
            "Sposta database, materie, trascrizioni e immagini degli appunti in una cartella "
            "sincronizzata dal cloud (OneDrive, Google Drive, Dropbox), cosi' piu' PC usano gli "
            "stessi dati.\n\nApri Lectio su un solo PC alla volta: due istanze aperte insieme "
            "possono sovrascriversi a vicenda. \"Torna ai dati su questo PC\" copia i dati in "
            "locale senza cancellare la cartella sincronizzata. Dopo ogni cambio Lectio si chiude.",
        ).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            self,
            text=("Per usare Lectio su piu' PC, metti i dati in una cartella di OneDrive, Google Drive o Dropbox. "
                  "Nella cartella vanno il database, la cartella Materie (PDF, appunti, riassunti, esami), le "
                  "trascrizioni e le immagini degli appunti. Su ogni PC restano modelli di trascrizione, suoni, "
                  "log e strumenti. Usa Lectio su un PC alla volta: se e' aperto altrove, Lectio te lo segnala."),
            font=font(11), text_color=COLORS.text_muted, anchor="w", justify="left", wraplength=760,
        ).grid(row=1, column=0, sticky="w", padx=22)
        self.status = ctk.CTkLabel(self, text="", font=font(11, "bold"), anchor="w", justify="left", wraplength=760)
        self.status.grid(row=2, column=0, sticky="w", padx=22, pady=(10, 12))
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 18))
        folder = self.paths.get("sync_folder")
        if self.paths.get("synced"):
            self.status.configure(text=f"● Sincronizzata: {folder}", text_color=COLORS.green)
            _button(buttons, "Apri cartella", self._open_folder, width=140).pack(side="left", padx=(0, 8))
            _button(buttons, "Torna ai dati su questo PC", self.disable).pack(side="left")
        elif folder is not None:
            self.status.configure(text=f"● Cartella scelta ma non raggiungibile: {folder}. Stai usando i dati di "
                                       "questo PC.", text_color=COLORS.amber)
            _button(buttons, "Scegli un'altra cartella…", self.enable, primary=True).pack(side="left", padx=(0, 8))
            _button(buttons, "Usa solo questo PC", self._forget).pack(side="left")
        else:
            self.status.configure(text="● Dati solo su questo PC", text_color=COLORS.text_faint)
            _button(buttons, "Scegli cartella sincronizzata…", self.enable, primary=True, width=230).pack(side="left")

    # -------------------------------------------------------------- azioni
    def _open_folder(self) -> None:
        try:
            os.startfile(str(self.paths["shared"]))  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            messagebox.showinfo("Cartella sincronizzata", str(self.paths["shared"]), parent=self.winfo_toplevel())

    def _forget(self) -> None:
        sync.set_sync_folder(None)
        messagebox.showinfo("Sincronizzazione", "Lectio usera' solo i dati di questo PC.", parent=self.winfo_toplevel())
        self.app.paths["sync_folder"] = None

    def _current(self) -> dict[str, Path]:
        return {key: Path(self.paths[key]) for key in ("db", "subjects", "transcripts", "pdf_images")}

    def enable(self) -> None:
        folder = filedialog.askdirectory(parent=self.winfo_toplevel(),
                                         title="Cartella sincronizzata (es. OneDrive\\Lectio)")
        if not folder:
            return
        target = Path(folder)
        use_existing = False
        if sync.folder_has_data(target):
            try:
                marker = json.loads((target / SYNC_MARKER_NAME).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                marker = {}
            if not messagebox.askyesno(
                "Dati gia' presenti",
                f"La cartella contiene gia' i dati di Lectio (preparati da «{marker.get('created_by', 'un altro PC')}»)."
                "\n\nUsare quei dati su questo PC? I dati attuali di questo PC non vengono cancellati: restano "
                "in locale come copia di sicurezza.",
                parent=self.winfo_toplevel(),
            ):
                return
            use_existing = True
        else:
            current = self._current()
            size = _folder_size(current["db"], current["subjects"], current["transcripts"], current["pdf_images"])
            if not messagebox.askyesno(
                "Sincronizza i dati",
                f"Copiare i dati di Lectio ({format_size(size)}) in\n{target}?\n\nGli originali restano su questo "
                "PC come copia di sicurezza. Alla fine Lectio si chiude: riaprilo per usare la cartella "
                "sincronizzata. Sugli altri PC scegli la stessa cartella dalle Impostazioni.",
                parent=self.winfo_toplevel(),
            ):
                return
        self._run(lambda report: sync.enable_sync(self._current(), target, use_existing, report),
                  "Sincronizzazione attivata", "Lectio usera' la cartella sincronizzata dal prossimo avvio.")

    def disable(self) -> None:
        if not messagebox.askyesno(
            "Torna ai dati su questo PC",
            "Copiare su questo PC i dati della cartella sincronizzata e smettere di usarla?\n\nLa cartella "
            "sincronizzata non viene cancellata. Alla fine Lectio si chiude: riaprilo.",
            parent=self.winfo_toplevel(),
        ):
            return
        self._run(lambda report: sync.disable_sync(self._current(), app_data_dir(), report),
                  "Dati riportati su questo PC", "Lectio usera' i dati di questo PC dal prossimo avvio.")

    def _run(self, work, title: str, message: str) -> None:
        app = self.app
        progress = ProgressDialog(app, "Sincronizzazione tra PC", "Preparo la copia…")
        progress.cancel_button.configure(state="disabled")

        def report(fraction: float, text: str) -> None:
            app.post(lambda: progress.update_progress(fraction, text))

        def done(_result) -> None:
            progress.close()
            messagebox.showinfo(title, f"{message}\n\nOra Lectio si chiude: riaprilo.", parent=app)
            app.after(50, app.on_close)  # dopo la fine di questo callback

        def failed(exc: BaseException) -> None:
            progress.close()
            LOGGER.warning("Operazione di sincronizzazione non riuscita: %s", exc)
            messagebox.showerror("Sincronizzazione tra PC", str(exc), parent=app)

        run_in_background(app, lambda: work(report), done, failed)
