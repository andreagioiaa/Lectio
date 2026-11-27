"""Controllo di Pandoc prima di un export Word, con download proposto all'utente."""

from __future__ import annotations

import logging
import threading
from tkinter import messagebox
from typing import Callable

from ..services.pdf_tools.export import find_pandoc
from ..services.pdf_tools.pandoc_setup import (
    PANDOC_DOWNLOAD_MB,
    PANDOC_VERSION,
    PandocSetupCancelled,
    can_download_pandoc,
    download_pandoc,
)
from .summary_view import ProgressDialog, run_in_background

LOGGER = logging.getLogger(__name__)


def ensure_pandoc(app, parent, then: Callable[[], None]) -> None:
    """Esegue ``then`` se Pandoc c'e'; altrimenti propone di scaricarlo e poi lo esegue."""
    if find_pandoc():
        then()
        return
    if not can_download_pandoc():
        messagebox.showerror("Esporta in Word", "Per l'export .docx serve Pandoc nel PATH.", parent=parent)
        return
    if not messagebox.askyesno(
        "Esporta in Word",
        "Per creare i file Word (.docx) con le formule native serve Pandoc, un programma gratuito "
        "che Lectio usa in locale.\n\n"
        f"Scaricarlo ora? Verra' scaricata una sola volta la versione ufficiale {PANDOC_VERSION} "
        f"da github.com/jgm/pandoc (circa {PANDOC_DOWNLOAD_MB} MB) nella cartella dati di Lectio.",
        parent=parent,
    ):
        return
    cancel = threading.Event()
    dialog = ProgressDialog(app, "Scarico Pandoc", "Connessione a GitHub…", on_cancel=cancel.set)

    def report(fraction: float, message: str) -> None:
        app.post(lambda: dialog.update_progress(fraction, message))

    def done(_path) -> None:
        dialog.close()
        app.toast("Pandoc installato: export Word disponibile.")
        _refresh_pdf_backends(app)
        if find_pandoc():
            then()
        else:
            messagebox.showerror("Scarico Pandoc", "Pandoc e' stato scaricato ma non viene trovato.", parent=app)

    def failed(exc: BaseException) -> None:
        dialog.close()
        if isinstance(exc, PandocSetupCancelled):
            app.toast("Download di Pandoc annullato.")
            return
        LOGGER.warning("Download di Pandoc non riuscito: %s", exc)
        messagebox.showerror("Scarico Pandoc", str(exc), parent=app)

    run_in_background(app, lambda: download_pandoc(report, cancel.is_set), done, failed)


def _refresh_pdf_backends(app) -> None:
    view = getattr(app, "views", {}).get("pdf")
    if view is not None and hasattr(view, "_refresh_backends"):
        try:
            view._refresh_backends()
        except Exception:
            LOGGER.debug("Aggiornamento dei motori PDF non riuscito.", exc_info=True)
