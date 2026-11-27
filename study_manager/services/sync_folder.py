"""Uso di Lectio su piu' PC tramite una cartella sincronizzata (OneDrive, Google Drive, Dropbox...).

Si presume un PC alla volta. Le protezioni:

* **database in un file solo** (``journal_mode = DELETE``, vedi ``Database``);
* **file di blocco** ``lectio.lock`` con nome del PC e ultimo segnale: se
  Lectio risulta aperto su un altro PC l'utente viene avvisato;
* **copie in conflitto** create dal programma di sincronizzazione segnalate
  all'avvio (es. ``study_manager-PC2.db``);
* **snapshot** ZIP alla chiusura (gli ultimi ``SNAPSHOTS_KEPT``) per tornare indietro.

Attivazione e disattivazione copiano i dati (mai spostano): gli originali
restano come copia di sicurezza.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from ..paths import (
    DB_FILENAME,
    SYNC_MARKER_NAME,
    local_shared_paths,
    set_sync_folder,
    synced_shared_paths,
)

LOGGER = logging.getLogger(__name__)

LOCK_NAME = "lectio.lock"
HEARTBEAT_SECONDS = 60
LOCK_FRESH_MINUTES = 5
SNAPSHOTS_KEPT = 5

Progress = Callable[[float, str], None]


class SyncError(RuntimeError):
    pass


def machine_name() -> str:
    return platform.node() or os.environ.get("COMPUTERNAME", "") or "questo PC"


# ---------------------------------------------------------------------------
# Blocco tra PC
# ---------------------------------------------------------------------------
def read_lock(folder: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((Path(folder) / LOCK_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_lock(folder: Path, now: datetime | None = None) -> None:
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    current = read_lock(folder) or {}
    data = {
        "pc": machine_name(),
        "pid": os.getpid(),
        "started": current.get("started") if current.get("pc") == machine_name() and current.get("pid") == os.getpid()
        else stamp,
        "heartbeat": stamp,
    }
    target = Path(folder) / LOCK_NAME
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, target)


def release_lock(folder: Path) -> None:
    """Toglie il blocco solo se e' di questo PC (e di questo processo)."""
    lock = read_lock(folder)
    if lock is None or (lock.get("pc") == machine_name() and int(lock.get("pid") or 0) == os.getpid()):
        (Path(folder) / LOCK_NAME).unlink(missing_ok=True)


def lock_conflict(folder: Path, now: datetime | None = None,
                  fresh_minutes: int = LOCK_FRESH_MINUTES) -> dict[str, Any] | None:
    """Blocco di un *altro* PC con un segnale recente, altrimenti None."""
    lock = read_lock(folder)
    if not lock or lock.get("pc") == machine_name():
        return None
    try:
        heartbeat = datetime.fromisoformat(str(lock.get("heartbeat")))
    except (TypeError, ValueError):
        return None
    if (now or datetime.now()) - heartbeat > timedelta(minutes=fresh_minutes):
        return None
    return lock


def conflict_copies(folder: Path) -> list[Path]:
    """Copie del database create dal programma di sincronizzazione (es. ``study_manager-PC2.db``)."""
    stem = Path(DB_FILENAME).stem
    found = []
    for path in Path(folder).glob(f"{stem}*.db"):
        if path.name != DB_FILENAME:
            found.append(path)
    return sorted(found)


# ---------------------------------------------------------------------------
# Snapshot alla chiusura
# ---------------------------------------------------------------------------
def write_snapshot(db, paths: dict[str, Any], keep: int = SNAPSHOTS_KEPT) -> Path:
    """Backup ZIP leggero (database e trascrizioni) nella cartella Snapshot; tiene gli ultimi ``keep``."""
    from .backup import export_backup

    folder = Path(paths["snapshots"])
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    pc = "".join(ch for ch in machine_name() if ch.isalnum() or ch in "-_") or "PC"
    result = export_backup(db, paths, folder / f"lectio-{stamp}-{pc}.zip")
    snapshots = sorted(folder.glob("lectio-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in snapshots[max(1, keep):]:
        try:
            old.unlink()
        except OSError:
            LOGGER.debug("Snapshot non eliminato: %s", old, exc_info=True)
    return Path(result["path"])


# ---------------------------------------------------------------------------
# Copia dei dati
# ---------------------------------------------------------------------------
def _copy_database(source: Path, target: Path) -> None:
    """Copia consistente anche con il database aperto (API di backup di SQLite)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".copy")
    temp.unlink(missing_ok=True)
    src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(temp))
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode = DELETE")
        finally:
            dst.close()
    finally:
        src.close()
    os.replace(temp, target)
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(target) + suffix).unlink(missing_ok=True)


def _files_in(folder: Path) -> list[Path]:
    return [path for path in folder.rglob("*") if path.is_file()] if folder.is_dir() else []


def _copy_tree(source: Path, target: Path, progress: Progress, start: float, span: float, label: str) -> int:
    """Copia i file mancanti o diversi (non cancella nulla nella destinazione)."""
    files = _files_in(source)
    copied = 0
    for index, path in enumerate(files):
        destination = target / path.relative_to(source)
        try:
            same = destination.is_file() and destination.stat().st_size == path.stat().st_size \
                and int(destination.stat().st_mtime) >= int(path.stat().st_mtime)
        except OSError:
            same = False
        if not same:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied += 1
        if index % 10 == 0:
            progress(start + span * index / max(1, len(files)), f"{label}: {index + 1}/{len(files)}")
    return copied


def folder_has_data(folder: Path) -> bool:
    return (Path(folder) / SYNC_MARKER_NAME).is_file() and (Path(folder) / DB_FILENAME).is_file()


def check_target(folder: Path, local: dict[str, Path]) -> None:
    folder = Path(folder)
    if not folder.is_dir():
        raise SyncError("La cartella scelta non esiste.")
    data = Path(local["db"]).parent
    resolved = os.path.normcase(os.path.abspath(folder))
    for blocked in (data, Path(local["subjects"])):
        other = os.path.normcase(os.path.abspath(blocked))
        if resolved == other or resolved.startswith(other + os.sep) or other.startswith(resolved + os.sep):
            raise SyncError("Scegli una cartella diversa da quelle che Lectio usa gia' (dati o Materie).")
    probe = folder / f".lectio-write-test-{os.getpid()}"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise SyncError(f"Non posso scrivere nella cartella scelta: {exc}") from exc


def _write_marker(folder: Path) -> None:
    (Path(folder) / SYNC_MARKER_NAME).write_text(json.dumps({
        "app": "Lectio", "created_by": machine_name(), "created_at": datetime.now().isoformat(timespec="seconds"),
        "note": "Cartella dati condivisa di Lectio: non spostare o rinominare i file qui dentro.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def enable_sync(current: dict[str, Path], folder: str | Path, use_existing: bool = False,
                progress: Progress = lambda _f, _m: None) -> dict[str, Any]:
    """Porta i dati di questo PC nella cartella sincronizzata (o usa quelli gia' presenti).

    ``current`` sono i percorsi in uso (db, subjects, transcripts, pdf_images).
    """
    target = Path(folder)
    check_target(target, current)
    shared = synced_shared_paths(target)
    summary = {"folder": str(target), "used_existing": False, "files": 0}
    if folder_has_data(target):
        if not use_existing:
            raise SyncError("La cartella contiene gia' i dati di Lectio di un altro PC.")
        summary["used_existing"] = True
    else:
        progress(0.02, "Copio il database…")
        _copy_database(Path(current["db"]), shared["db"])
        for key, label, start in (("subjects", "Materie", 0.1), ("transcripts", "Trascrizioni", 0.75),
                                  ("pdf_images", "Immagini degli appunti", 0.85)):
            summary["files"] += _copy_tree(Path(current[key]), shared[key], progress, start, 0.1 if start > 0.5 else 0.65,
                                           label)
        _write_marker(target)
    set_sync_folder(target)
    progress(1.0, "Cartella sincronizzata pronta")
    return summary


def disable_sync(current: dict[str, Path], local_data: Path,
                 progress: Progress = lambda _f, _m: None) -> dict[str, Any]:
    """Riporta i dati della cartella sincronizzata su questo PC e smette di usarla."""
    local = local_shared_paths(Path(local_data))
    progress(0.02, "Copio il database su questo PC…")
    old_db = Path(local["db"])
    if old_db.is_file():
        backup = old_db.with_name(f"study_manager-prima-della-sync-{datetime.now():%Y%m%d-%H%M%S}.db")
        shutil.copy2(old_db, backup)
    _copy_database(Path(current["db"]), old_db)
    files = 0
    for key, label, start in (("subjects", "Materie", 0.1), ("transcripts", "Trascrizioni", 0.75),
                              ("pdf_images", "Immagini degli appunti", 0.85)):
        files += _copy_tree(Path(current[key]), Path(local[key]), progress, start, 0.1 if start > 0.5 else 0.65, label)
    set_sync_folder(None)
    progress(1.0, "Dati riportati su questo PC")
    return {"files": files}
