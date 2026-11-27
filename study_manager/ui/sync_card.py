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
from ..services import cloud_folders as cloud
from ..services import sync_folder as sync
from .share_view import format_size
from .summary_view import ProgressDialog, run_in_background
from .theme import font
from .widgets import Card

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
        ctk.CTkLabel(self, text="Sincronizzazione tra PC", font=font(16, "bold"), text_color=COLORS.text).grid(
            row=0, column=0, sticky="w", padx=22, pady=(18, 4))
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
            _button(buttons, "Configura automaticamente…", self.auto_setup, primary=True, width=220).pack(
                side="left", padx=(0, 8))
            _button(buttons, "Scegli un'altra cartella…", self.enable).pack(side="left", padx=(0, 8))
            _button(buttons, "Usa solo questo PC", self._forget).pack(side="left")
        else:
            self.status.configure(text="● Dati solo su questo PC", text_color=COLORS.text_faint)
            _button(buttons, "Configura automaticamente…", self.auto_setup, primary=True, width=220).pack(
                side="left", padx=(0, 8))
            _button(buttons, "Scegli la cartella a mano…", self.enable, width=210).pack(side="left")

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
        self._confirm_and_enable(Path(folder))

    def auto_setup(self) -> None:
        """Trova OneDrive/Google Drive/Dropbox su questo PC e prepara da solo la cartella ``Lectio``."""
        parent = self.winfo_toplevel()
        detection = cloud.detect_cloud_roots()
        if not detection.roots:
            hints = "\n\n".join(detection.hints) or (
                "Installa OneDrive, Google Drive per desktop o Dropbox, accedi con il tuo account e riprova.")
            if messagebox.askyesno(
                "Nessuna cartella cloud trovata",
                f"Su questo PC non ho trovato cartelle di OneDrive, Google Drive o Dropbox pronte.\n\n{hints}"
                "\n\nVuoi scegliere una cartella a mano?",
                parent=parent,
            ):
                self.enable()
            return
        CloudChooser(parent, detection, self._use_cloud_root)

    def _use_cloud_root(self, root: cloud.CloudRoot) -> None:
        parent = self.winfo_toplevel()
        try:
            target = cloud.prepare_target(root)
        except OSError as exc:
            messagebox.showerror("Sincronizzazione tra PC", str(exc), parent=parent)
            return
        if target.state == "incomplete":
            messagebox.showwarning(
                "Dati non ancora arrivati",
                f"In {target.folder} ci sono i dati di Lectio (preparati da «{target.created_by or 'un altro PC'}»), "
                f"ma {root.service_name} non ha ancora scaricato il database.\n\nAspetta che la sincronizzazione "
                "finisca e riprova tra qualche minuto.",
                parent=parent,
            )
            return
        self._confirm_and_enable(target.folder, target)

    def _confirm_and_enable(self, target: Path, cloud_target: cloud.Target | None = None) -> None:
        parent = self.winfo_toplevel()
        use_existing = False
        streaming = bool(cloud_target and cloud_target.root.streaming)
        if sync.folder_has_data(target):
            try:
                marker = json.loads((target / SYNC_MARKER_NAME).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                marker = {}
            if not messagebox.askyesno(
                "Dati gia' presenti",
                f"{target} contiene gia' i dati di Lectio (preparati da «{marker.get('created_by', 'un altro PC')}»)."
                "\n\nUsare quei dati su questo PC? I dati attuali di questo PC non vengono cancellati: restano "
                "in locale come copia di sicurezza.",
                parent=parent,
            ):
                return
            use_existing = True
        else:
            current = self._current()
            size = _folder_size(current["db"], current["subjects"], current["transcripts"], current["pdf_images"])
            where = (f"nella cartella {target}\n(la crea Lectio in {cloud_target.root.label})"
                     if cloud_target else f"in\n{target}")
            if not messagebox.askyesno(
                "Sincronizza i dati",
                f"Copiare i dati di Lectio ({format_size(size)}) {where}?\n\nGli originali restano su questo "
                "PC come copia di sicurezza. Alla fine Lectio si chiude: riaprilo per usare la cartella "
                "sincronizzata. Sugli altri PC usa «Configura automaticamente» o scegli la stessa cartella.",
                parent=parent,
            ):
                return
        message = "Lectio usera' la cartella sincronizzata dal prossimo avvio."
        if streaming:
            message += ("\n\nImportante: Google Drive e' in modalita' streaming, quindi i file possono restare "
                        f"solo online. In Esplora file fai clic destro sulla cartella «{target.name}» → "
                        "Accesso offline → Disponibile offline, cosi' Lectio funziona anche senza internet.")

        def work(report):
            if cloud_target is not None:
                cloud.create_target(cloud_target)
            result = sync.enable_sync(self._current(), target, use_existing, report)
            if cloud_target is not None:
                report(1.0, "Tengo i file sempre su questo PC…")
                cloud.keep_offline(cloud_target)
            return result

        self._run(work, "Sincronizzazione attivata", message)

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


class CloudChooser(ctk.CTkToplevel):
    """Elenco dei servizi cloud trovati su questo PC: l'utente ne sceglie uno."""

    def __init__(self, master, detection: cloud.Detection, on_choose):
        super().__init__(master)
        self.title("Configura la sincronizzazione")
        self.configure(fg_color=COLORS.bg)
        self.resizable(False, False)
        self.transient(master)
        self._on_choose = on_choose
        self._roots = detection.roots
        self._choice = ctk.IntVar(value=0)
        body = ctk.CTkFrame(self, fg_color=COLORS.surface, corner_radius=14, border_width=1,
                            border_color=COLORS.border)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        ctk.CTkLabel(body, text="Dove vuoi sincronizzare i dati?", font=font(13, "bold"), text_color=COLORS.text,
                     anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        ctk.CTkLabel(body, text="Lectio crea la cartella «Lectio» nel servizio scelto. Se un altro PC l'ha gia' "
                                "preparata, usa i dati che ci sono.",
                     font=font(10), text_color=COLORS.text_muted, anchor="w", justify="left",
                     wraplength=480).pack(fill="x", padx=16, pady=(0, 10))
        for index, root in enumerate(self._roots):
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=(0, 8))
            ctk.CTkRadioButton(row, text=root.label, variable=self._choice, value=index, font=font(11, "bold"),
                               text_color=COLORS.text, fg_color=COLORS.accent,
                               hover_color=COLORS.accent_hover).pack(anchor="w")
            detail = str(root.path)
            if root.streaming:
                detail += "\nStreaming: dopo la configurazione rendi la cartella «Disponibile offline»."
            ctk.CTkLabel(row, text=detail, font=font(10), text_color=COLORS.text_faint, anchor="w",
                         justify="left", wraplength=450).pack(fill="x", padx=(28, 0))
        for hint in detection.hints:
            ctk.CTkLabel(body, text=f"• {hint}", font=font(10), text_color=COLORS.amber, anchor="w",
                         justify="left", wraplength=480).pack(fill="x", padx=16, pady=(0, 6))
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(6, 14))
        _button(buttons, "Continua", self._confirm, primary=True, width=120).pack(side="right")
        _button(buttons, "Annulla", self.destroy, width=100).pack(side="right", padx=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        try:
            self.update_idletasks()
            width, height = 540, body.winfo_reqheight() + 24
            x = master.winfo_rootx() + max(0, (master.winfo_width() - width) // 2)
            y = master.winfo_rooty() + max(0, (master.winfo_height() - height) // 3)
            self.geometry(f"{width}x{height}+{x}+{y}")
            self.grab_set()
        except Exception:  # finestra gia' chiusa o grab non disponibile
            LOGGER.debug("Finestra di scelta del cloud non centrata", exc_info=True)

    def _confirm(self) -> None:
        root = self._roots[self._choice.get()]
        self.destroy()
        self._on_choose(root)
