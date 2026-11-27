"""Percorsi applicativi e risoluzione delle risorse in modalita' sorgente o PyInstaller."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


APP_DIR_NAME = "Lectio"
# Cartella dati usata prima del cambio nome: viene migrata al primo avvio.
LEGACY_APP_DIR_NAME = "StudyFlow"


def project_root() -> Path:
    """Directory del progetto (utile in modalita' sorgente)."""
    return Path(__file__).resolve().parent.parent


def resource_path(relative: str | Path) -> Path:
    """Restituisce una risorsa inclusa nell'exe one-file o nel progetto sorgente."""
    base = Path(getattr(sys, "_MEIPASS", project_root()))
    return base / Path(relative)


def app_data_dir() -> Path:
    """Cartella dati persistente, sempre scrivibile e separata da Program Files."""
    override = os.environ.get("LECTIO_DATA_DIR", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
    elif os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(root) / APP_DIR_NAME
        _migrate_legacy_dir(Path(root) / LEGACY_APP_DIR_NAME, path)
    else:
        path = Path.home() / f".{APP_DIR_NAME.lower()}"
        _migrate_legacy_dir(Path.home() / f".{LEGACY_APP_DIR_NAME.lower()}", path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _migrate_legacy_dir(legacy: Path, target: Path) -> None:
    """Rinomina la vecchia cartella dati se quella nuova non esiste ancora."""
    if target.exists() or not legacy.is_dir():
        return
    try:
        legacy.rename(target)
    except OSError:
        return  # es. file in uso: si parte da una cartella nuova, la vecchia resta intatta.
    old_log = target / "logs" / "studyflow.log"
    new_log = target / "logs" / "lectio.log"
    if old_log.exists() and not new_log.exists():
        try:
            old_log.rename(new_log)
        except OSError:
            pass


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".write-test-{os.getpid()}"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def sounds_dir(data: Path) -> Path:
    """Cartella ``sounds`` accanto all'app, con fallback nella cartella dati.

    In modalita' sorgente usa la radice del progetto; nell'exe usa la cartella
    che contiene Lectio.exe. Se quella posizione non e' scrivibile (ad esempio
    Program Files), usa ``%LOCALAPPDATA%\\Lectio\\sounds``.
    """
    override = os.environ.get("LECTIO_SOUNDS_DIR", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
    elif os.environ.get("LECTIO_DATA_DIR", "").strip():
        path = data / "sounds"
    elif is_frozen():
        path = executable_path().parent / "sounds"
    else:
        path = project_root() / "sounds"
    if _is_writable(path):
        return path
    fallback = data / "sounds"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def subjects_dir(data: Path) -> Path:
    """Cartella ``Materie`` nella radice di funzionamento dell'app.

    Come per ``sounds``: in modalita' sorgente e' la radice del progetto,
    nell'exe la cartella che contiene Lectio.exe. Se non e' scrivibile
    (es. Program Files) si usa ``%LOCALAPPDATA%\\Lectio\\Materie``.
    ``LECTIO_SUBJECTS_DIR`` permette di scegliere un percorso diverso.
    """
    override = os.environ.get("LECTIO_SUBJECTS_DIR", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
    elif os.environ.get("LECTIO_DATA_DIR", "").strip():
        path = data / "Materie"
    elif is_frozen():
        path = executable_path().parent / "Materie"
    else:
        path = project_root() / "Materie"
    if _is_writable(path):
        return path
    fallback = data / "Materie"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


# ---------------------------------------------------------------------------
# Cartella sincronizzata (OneDrive, Google Drive, Dropbox...)
# ---------------------------------------------------------------------------
# Il percorso scelto si salva in un file *locale* (``sync.json`` nella cartella
# dati di questo PC): ogni PC sa dove trovare i dati condivisi. Nella cartella
# condivisa vanno database, ``Materie``, trascrizioni e immagini degli appunti;
# log, modelli di trascrizione, suoni e strumenti (Pandoc, Tesseract) restano
# locali. ``lectio-sync.json`` segna una cartella preparata da Lectio: se manca
# (es. OneDrive non ancora collegato) la cartella non viene usata, cosi' Lectio
# non crea per sbaglio un database vuoto al posto di quello sincronizzato.
SYNC_CONFIG_NAME = "sync.json"
SYNC_MARKER_NAME = "lectio-sync.json"
DB_FILENAME = "study_manager.db"


def sync_config_path() -> Path:
    return app_data_dir() / SYNC_CONFIG_NAME


def sync_folder() -> Path | None:
    """Cartella sincronizzata scelta su questo PC, se c'e'."""
    import json

    try:
        raw = json.loads(sync_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    folder = str(raw.get("folder") or "").strip() if isinstance(raw, dict) else ""
    return Path(folder) if folder else None


def set_sync_folder(folder: str | Path | None) -> None:
    """Salva (o toglie, con ``None``) la cartella sincronizzata di questo PC."""
    import json

    path = sync_config_path()
    if folder is None:
        path.unlink(missing_ok=True)
        return
    path.write_text(json.dumps({"folder": str(Path(folder))}, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_folder_ready(folder: Path | None) -> bool:
    return folder is not None and (folder / SYNC_MARKER_NAME).is_file()


def shared_data_dir() -> Path:
    """Cartella dei dati condivisi: quella sincronizzata se pronta, altrimenti quella locale."""
    folder = sync_folder()
    return folder if sync_folder_ready(folder) else app_data_dir()


def local_shared_paths(data: Path) -> dict[str, Path]:
    """Database, Materie, trascrizioni e immagini quando i dati sono solo su questo PC."""
    return {
        "db": data / DB_FILENAME,
        "subjects": subjects_dir(data),
        "transcripts": data / "transcripts",
        "pdf_images": data / "pdf_images",
    }


def synced_shared_paths(folder: Path) -> dict[str, Path]:
    return {
        "db": folder / DB_FILENAME,
        "subjects": folder / "Materie",
        "transcripts": folder / "transcripts",
        "pdf_images": folder / "pdf_images",
    }


def ensure_data_layout() -> dict[str, Path]:
    """Crea e restituisce tutti i percorsi usati dall'applicazione."""
    data = app_data_dir()
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    sounds = sounds_dir(data)
    models = data / "models"
    models.mkdir(parents=True, exist_ok=True)
    folder = sync_folder()
    synced = sync_folder_ready(folder)
    shared = synced_shared_paths(folder) if synced and folder is not None else local_shared_paths(data)
    shared["transcripts"].mkdir(parents=True, exist_ok=True)
    shared["subjects"].mkdir(parents=True, exist_ok=True)
    transcripts = shared["transcripts"]
    subjects = shared["subjects"]
    paths = {
        "data": data,
        "shared": folder if synced and folder is not None else data,
        "synced": synced,  # type: ignore[dict-item]
        "sync_folder": folder,  # type: ignore[dict-item]  # scelta su questo PC (anche se non raggiungibile)
        "pdf_images": shared["pdf_images"],
        "snapshots": (folder / "Snapshot") if synced and folder is not None else data / "backups",
        "logs": logs,
        "sounds": sounds,
        "transcripts": transcripts,
        "models": models,
        "subjects": subjects,
        "db": shared["db"],
        "log": logs / "lectio.log",
        "guard_log": logs / "hosts_guard.log",
        "hosts_control": data / "hosts_guard_control.json",
        "hosts_status": data / "hosts_guard_status.json",
        "hosts_backup": data / "hosts_original.backup",
        "icon": resource_path("assets/app.ico"),
        "logo": resource_path("assets/logo_mark.png"),
    }
    return paths


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def executable_path() -> Path:
    return Path(sys.executable).resolve()


# ---------------------------------------------------------------------------
# Percorsi salvati nel database
# ---------------------------------------------------------------------------
# I file che stanno dentro una cartella gestita da Lectio (``Materie`` o la
# cartella dati) vengono salvati come ``<materie>/Analisi/Slide/lezione.pdf``:
# cosi' il database resta valido se la cartella viene spostata, sincronizzata
# su un altro PC o condivisa. ``<`` e ``>`` non sono ammessi nei nomi di file
# di Windows, quindi un percorso relativo non si confonde mai con uno reale.
# I file fuori da queste cartelle restano con il percorso assoluto.
PATH_ROOT_SUBJECTS = "materie"
PATH_ROOT_DATA = "dati"
_STORED_ROOT_RE = re.compile(r"^<([a-z]+)>(?:[\\/](.*))?$")


class StoredPaths:
    """Converte i percorsi tra la forma salvata (relativa) e quella reale (assoluta)."""

    def __init__(self, roots: dict[str, str | Path] | None = None):
        self.roots: dict[str, Path] = {}
        for name, folder in (roots or {}).items():
            if folder:
                self.roots[name] = Path(os.path.abspath(str(folder)))

    def _ordered(self) -> list[tuple[str, Path]]:
        # la radice piu' lunga vince: ``Materie`` puo' stare dentro la cartella dati
        return sorted(self.roots.items(), key=lambda item: len(str(item[1])), reverse=True)

    def to_stored(self, path: str | Path | None) -> str:
        value = str(path or "").strip()
        if not value or not self.roots or _STORED_ROOT_RE.match(value):
            return value
        absolute = os.path.abspath(value)
        normalized = os.path.normcase(absolute)
        for name, root in self._ordered():
            prefix = os.path.normcase(str(root)).rstrip("\\/")
            if normalized == prefix:
                return f"<{name}>"
            if normalized.startswith(prefix + os.sep):
                relative = absolute[len(prefix):].lstrip("\\/").replace("\\", "/")
                return f"<{name}>/{relative}"
        return value

    def to_real(self, value: str | Path | None) -> str:
        text = str(value or "").strip()
        match = _STORED_ROOT_RE.match(text)
        if not match:
            return text
        root = self.roots.get(match.group(1))
        if root is None:
            return text
        relative = match.group(2) or ""
        return str(root / Path(relative)) if relative else str(root)

    @staticmethod
    def is_stored_relative(value: str | Path | None) -> bool:
        return bool(_STORED_ROOT_RE.match(str(value or "").strip()))
