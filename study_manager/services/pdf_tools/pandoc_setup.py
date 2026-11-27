"""Pandoc per l'esportazione Word (.docx) quando non e' gia' disponibile.

``Lectio.exe`` pubblicato nelle Release e' un solo file: Pandoc (oltre 200 MB
estratto) non e' incluso, altrimenti andrebbe estratto a ogni avvio. Al primo
export Word l'app propone di scaricare la versione ufficiale da GitHub
(jgm/pandoc) e la salva una volta sola in ``%LOCALAPPDATA%\\Lectio\\pandoc.exe``,
dove ``find_pandoc`` la cerca. Stessa logica dei modelli di trascrizione.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

from ...paths import app_data_dir

LOGGER = logging.getLogger(__name__)

PANDOC_VERSION = "3.9"
PANDOC_URL = (
    f"https://github.com/jgm/pandoc/releases/download/{PANDOC_VERSION}/"
    f"pandoc-{PANDOC_VERSION}-windows-x86_64.zip"
)
PANDOC_DOWNLOAD_MB = 40  # dimensione indicativa dello zip, mostrata all'utente
_MAX_ZIP_BYTES = 300 * 1024 * 1024
_MAX_EXE_BYTES = 400 * 1024 * 1024


class PandocSetupError(RuntimeError):
    pass


class PandocSetupCancelled(PandocSetupError):
    pass


def pandoc_target() -> Path:
    return app_data_dir() / ("pandoc.exe" if os.name == "nt" else "pandoc")


def can_download_pandoc() -> bool:
    """Il download automatico c'e' solo per Windows a 64 bit (la piattaforma di Lectio)."""
    return os.name == "nt"


def _check_version(path: Path) -> str:
    try:
        result = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PandocSetupError(f"Pandoc scaricato ma non avviabile: {exc}") from exc
    first = (result.stdout or "").splitlines()[:1]
    if result.returncode != 0 or not first or not first[0].startswith("pandoc "):
        raise PandocSetupError("Il file scaricato non e' un eseguibile Pandoc valido.")
    return first[0].strip()


def extract_pandoc(zip_path: Path, target: Path) -> Path:
    """Estrae solo ``pandoc.exe`` dallo zip ufficiale e lo verifica."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = [
                info for info in archive.infolist()
                if not info.is_dir() and Path(info.filename).name.lower() == "pandoc.exe"
            ]
            if not members:
                raise PandocSetupError("Lo zip scaricato non contiene pandoc.exe.")
            member = members[0]
            if member.file_size > _MAX_EXE_BYTES:
                raise PandocSetupError("pandoc.exe nello zip ha una dimensione non valida.")
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            with archive.open(member) as source, partial.open("wb") as handle:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
    except zipfile.BadZipFile as exc:
        raise PandocSetupError("Il file scaricato non e' uno zip valido.") from exc
    try:
        _check_version(partial)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def download_pandoc(
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    url: str = PANDOC_URL,
    target: Path | None = None,
) -> Path:
    """Scarica Pandoc nella cartella dati e restituisce il percorso di ``pandoc.exe``."""
    report = progress or (lambda _f, _m: None)
    destination = target or pandoc_target()
    try:
        import requests
    except Exception as exc:  # pragma: no cover - requests e' una dipendenza
        raise PandocSetupError("Il componente per il download non e' disponibile.") from exc
    with tempfile.TemporaryDirectory(prefix="lectio-pandoc-") as temp:
        zip_path = Path(temp) / "pandoc.zip"
        received = 0
        try:
            with requests.get(url, stream=True, timeout=(20, 120),
                              headers={"User-Agent": "Lectio (desktop)"}) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length") or 0)
                if total > _MAX_ZIP_BYTES:
                    raise PandocSetupError("Il file da scaricare e' troppo grande.")
                with zip_path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if cancelled():
                            raise PandocSetupCancelled("Download di Pandoc annullato.")
                        if not chunk:
                            continue
                        handle.write(chunk)
                        received += len(chunk)
                        if received > _MAX_ZIP_BYTES:
                            raise PandocSetupError("Il file scaricato e' troppo grande.")
                        mb = received / 1024 / 1024
                        report(0.9 * received / total if total else 0.0,
                               f"Scarico Pandoc {PANDOC_VERSION}: {mb:.0f} MB"
                               + (f" di {total / 1024 / 1024:.0f} MB" if total else ""))
        except PandocSetupError:
            raise
        except Exception as exc:
            raise PandocSetupError(
                "Download di Pandoc non riuscito. Verifica la connessione Internet e riprova. "
                f"Dettaglio: {exc}"
            ) from exc
        if received <= 0:
            raise PandocSetupError("Il download di Pandoc ha restituito un file vuoto.")
        report(0.95, "Estraggo pandoc.exe…")
        path = extract_pandoc(zip_path, destination)
    report(1.0, "Pandoc pronto")
    LOGGER.info("Pandoc %s installato in %s", PANDOC_VERSION, path)
    return path
