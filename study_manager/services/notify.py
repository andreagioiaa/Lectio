"""Notifiche di sistema (toast di Windows) senza dipendenze aggiuntive.

Windows decide se mostrarle: se l'utente ha disattivato le notifiche o attivato
"Non disturbare", il toast viene semplicemente scartato dal sistema.
"""

from __future__ import annotations

import logging
import os
import subprocess

LOGGER = logging.getLogger(__name__)

# shortcut: il toast appare come "Windows PowerShell" perche' Lectio non registra
# un proprio AppUserModelID; registrarlo (collegamento nel menu Start) se serve il nome.
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_SCRIPT = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
    "ContentType=WindowsRuntime] > $null;"
    "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$n = $t.GetElementsByTagName('text');"
    "$n.Item(0).AppendChild($t.CreateTextNode($env:LECTIO_TOAST_TITLE)) > $null;"
    "$n.Item(1).AppendChild($t.CreateTextNode($env:LECTIO_TOAST_BODY)) > $null;"
    f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_APP_ID}')"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($t))"
)


def notify(title: str, message: str) -> None:
    """Mostra un toast di sistema; ogni errore viene solo registrato nel log."""
    if os.name != "nt":
        return
    # Titolo e testo passano per variabili d'ambiente: nessun testo dell'utente nello script.
    env = dict(os.environ, LECTIO_TOAST_TITLE=str(title)[:200], LECTIO_TOAST_BODY=str(message)[:500])
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except OSError:
        LOGGER.exception("Notifica di sistema non inviata.")
