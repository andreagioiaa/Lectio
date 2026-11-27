"""Controllo e installazione degli aggiornamenti di Lectio.

Ogni release su GitHub contiene, oltre a ``Lectio.exe``, un piccolo
``update.json`` generato dalla CI (``tools/release.py manifest``)::

    {"latest": "0.1.6", "min_version": "0.1.5", "url": "...", "page": "...",
     "sha256": "...", "size": 123, "notes": "..."}

All'avvio Lectio scarica ``releases/latest/download/update.json`` (un normale
download, senza i limiti dell'API GitHub) e lo confronta con ``APP_VERSION``:

- ``APP_VERSION >= latest``: niente da fare;
- ``min_version <= APP_VERSION < latest``: aggiornamento facoltativo;
- ``APP_VERSION < min_version``: aggiornamento obbligatorio.

L'ultima ``min_version`` vista resta salvata nelle impostazioni: una versione
che si sa troppo vecchia resta bloccata anche senza connessione.

Installazione. L'exe one-file di PyInstaller rilegge se stesso *per nome* a ogni
import, quindi non puo' essere rinominato mentre e' in uso. Per questo:

1. il nuovo exe viene scaricato accanto a quello attuale come ``Lectio.new.exe``
   e verificato con lo SHA-256 del manifest;
2. Lectio avvia ``Lectio.new.exe --finish-update`` e si chiude normalmente;
3. il nuovo exe aspetta che il vecchio processo sia uscito, sposta
   ``Lectio.exe`` in ``Lectio.old.exe``, si copia al suo posto e riavvia
   ``Lectio.exe``; se qualcosa va storto rimette a posto il vecchio exe;
4. al successivo avvio Lectio cancella ``Lectio.old.exe`` e ``Lectio.new.exe``.

Questo modulo non importa la GUI: la modalita' ``--finish-update`` parte prima
di CustomTkinter.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..config import APP_VERSION


LOGGER = logging.getLogger(__name__)

REPOSITORY = "andreagioiaa/Lectio"
MANIFEST_URL = f"https://github.com/{REPOSITORY}/releases/latest/download/update.json"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
# Il manifest puo' far scaricare solo file pubblicati nelle release di Lectio.
ALLOWED_DOWNLOAD_PREFIX = f"https://github.com/{REPOSITORY}/releases/download/"
USER_AGENT = f"Lectio-Updater/{APP_VERSION}"

# Chiavi nella tabella ``settings``.
SETTING_KNOWN_MIN = "update_known_min_version"
SETTING_SKIPPED = "update_skipped_version"
SETTING_LAST_MANIFEST = "update_last_manifest"
SETTING_LAST_RUN_VERSION = "update_last_run_version"

NEW_EXE_SUFFIX = ".new.exe"
OLD_EXE_SUFFIX = ".old.exe"
PARTIAL_SUFFIX = ".part"
FINISH_FLAG = "--finish-update"

Progress = Callable[[int, int], None]


class UpdateError(RuntimeError):
    """Errore di aggiornamento mostrabile all'utente."""


# ---------------------------------------------------------------------------
# Versioni e manifest
# ---------------------------------------------------------------------------
def parse_version(text: Any) -> tuple[int, int, int] | None:
    """``"v0.1.5"`` -> ``(0, 1, 5)``; ``None`` se il testo non e' una versione."""
    value = str(text or "").strip().lstrip("vV")
    parts = value.split(".")
    if len(parts) != 3:
        return None
    try:
        numbers = tuple(int(part) for part in parts)
    except ValueError:
        return None
    if any(number < 0 for number in numbers):
        return None
    return numbers  # type: ignore[return-value]


def _higher(first: str, second: str) -> str:
    """La piu' alta tra due versioni valide (stringa vuota se nessuna lo e')."""
    candidates = [v for v in (first, second) if parse_version(v) is not None]
    if not candidates:
        return ""
    return max(candidates, key=lambda v: parse_version(v))  # type: ignore[arg-type, return-value]


@dataclass(frozen=True)
class UpdateInfo:
    latest: str
    min_version: str
    url: str
    page: str
    sha256: str
    size: int
    notes: str
    required: bool


def evaluate(manifest: dict[str, Any] | None, current: str = APP_VERSION, known_min: str = "") -> UpdateInfo | None:
    """Confronta il manifest con la versione in uso.

    Restituisce ``None`` se Lectio e' gia' aggiornato o il manifest non e'
    valido. ``known_min`` e' l'ultima versione minima vista in passato.
    """
    if not isinstance(manifest, dict):
        return None
    latest = str(manifest.get("latest", "")).strip().lstrip("vV")
    current_v = parse_version(current)
    latest_v = parse_version(latest)
    if current_v is None or latest_v is None or latest_v <= current_v:
        return None
    url = str(manifest.get("url", "")).strip()
    sha256 = str(manifest.get("sha256", "")).strip().lower()
    if not url.startswith(ALLOWED_DOWNLOAD_PREFIX) or len(sha256) != 64:
        LOGGER.warning("Manifest di aggiornamento non valido: %s", manifest)
        return None
    minimum = _higher(str(manifest.get("min_version", "")).strip().lstrip("vV"), known_min)
    minimum_v = parse_version(minimum)
    try:
        size = int(manifest.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    return UpdateInfo(
        latest=latest,
        min_version=minimum,
        url=url,
        page=str(manifest.get("page") or RELEASES_PAGE),
        sha256=sha256,
        size=max(0, size),
        notes=str(manifest.get("notes") or "").strip(),
        required=minimum_v is not None and current_v < minimum_v,
    )


def is_below_minimum(current: str, known_min: str) -> bool:
    current_v, minimum_v = parse_version(current), parse_version(known_min)
    return current_v is not None and minimum_v is not None and current_v < minimum_v


def fetch_manifest(timeout: float = 8.0) -> dict[str, Any]:
    """Scarica ``update.json`` dell'ultima release. Solleva :class:`UpdateError`."""
    import requests

    try:
        response = requests.get(MANIFEST_URL, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    except requests.RequestException as exc:
        raise UpdateError(f"GitHub non raggiungibile: {exc}") from exc
    if response.status_code == 404:
        raise UpdateError("Nessuna release con update.json pubblicata.")
    if response.status_code != 200:
        raise UpdateError(f"Risposta inattesa da GitHub ({response.status_code}).")
    try:
        data = response.json()
    except ValueError as exc:
        raise UpdateError("update.json non leggibile.") from exc
    if not isinstance(data, dict):
        raise UpdateError("update.json non valido.")
    return data


def remember_manifest(db, manifest: dict[str, Any]) -> None:
    """Salva il manifest e alza (mai abbassa) la versione minima nota."""
    known = _higher(db.get_setting(SETTING_KNOWN_MIN, ""), str(manifest.get("min_version", "")).lstrip("vV"))
    db.set_settings({SETTING_LAST_MANIFEST: json.dumps(manifest), SETTING_KNOWN_MIN: known})


def cached_update(db, current: str = APP_VERSION) -> UpdateInfo | None:
    """Aggiornamento noto dall'ultimo controllo riuscito (funziona anche offline)."""
    try:
        manifest = json.loads(db.get_setting(SETTING_LAST_MANIFEST, "") or "null")
    except ValueError:
        manifest = None
    return evaluate(manifest, current, db.get_setting(SETTING_KNOWN_MIN, ""))


# ---------------------------------------------------------------------------
# Exe in uso
# ---------------------------------------------------------------------------
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def current_exe() -> Path:
    return Path(sys.executable).resolve()


def _sibling(exe: Path, suffix: str) -> Path:
    return exe.with_name(exe.stem + suffix)


def can_self_update() -> bool:
    """Solo l'exe di Windows si aggiorna da solo, e solo se la sua cartella e' scrivibile."""
    if not is_frozen() or os.name != "nt":
        return False
    folder = current_exe().parent
    probe = folder / f".lectio-update-test-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def cleanup_previous(exe: Path | None = None) -> None:
    """Cancella i file lasciati da un aggiornamento precedente (best effort)."""
    if exe is None:
        if not is_frozen():
            return
        exe = current_exe()
    for leftover in (
        _sibling(exe, OLD_EXE_SUFFIX),
        _sibling(exe, NEW_EXE_SUFFIX),
        _sibling(exe, NEW_EXE_SUFFIX + PARTIAL_SUFFIX),
    ):
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            LOGGER.debug("File di aggiornamento non cancellato: %s", leftover, exc_info=True)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def download(
    info: UpdateInfo,
    exe: Path,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
    timeout: float = 30.0,
) -> Path:
    """Scarica il nuovo exe accanto a ``exe`` e ne verifica lo SHA-256."""
    import requests

    target = _sibling(exe, NEW_EXE_SUFFIX)
    partial = target.with_name(target.name + PARTIAL_SUFFIX)
    digest = hashlib.sha256()
    received = 0
    try:
        with requests.get(info.url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=timeout) as response:
            if response.status_code != 200:
                raise UpdateError(f"Download non riuscito ({response.status_code}).")
            total = int(response.headers.get("Content-Length") or info.size or 0)
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if cancel is not None and cancel.is_set():
                        raise UpdateError("Download annullato.")
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if progress is not None:
                        progress(received, total)
    except requests.RequestException as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Download interrotto: {exc}") from exc
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    if info.size and received != info.size:
        partial.unlink(missing_ok=True)
        raise UpdateError("Il file scaricato e' incompleto. Riprova.")
    if digest.hexdigest() != info.sha256:
        partial.unlink(missing_ok=True)
        raise UpdateError("Il file scaricato non corrisponde a quello pubblicato (SHA-256 diverso). Riprova.")
    os.replace(partial, target)
    return target


# ---------------------------------------------------------------------------
# Installazione
# ---------------------------------------------------------------------------
def processes_to_wait() -> list[int]:
    """Il processo attuale e, per l'exe one-file, il bootloader che lo ha avviato."""
    pids = [os.getpid()]
    try:
        import psutil

        parent = psutil.Process(os.getpid()).parent()
        if parent is not None and Path(parent.exe()).resolve() == current_exe():
            pids.append(parent.pid)
    except Exception:
        LOGGER.debug("Processo padre non determinato.", exc_info=True)
    return pids


def launch_finisher(new_exe: Path, target: Path, wait_pids: list[int]) -> None:
    """Avvia il nuovo exe in modalita' ``--finish-update`` (prima di chiudere Lectio)."""
    arguments = [str(new_exe), FINISH_FLAG, "--target", str(target)]
    for pid in wait_pids:
        arguments += ["--wait-pid", str(pid)]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(arguments, close_fds=True, creationflags=flags, cwd=str(target.parent))


def _wait_for_exit(pids: list[int], timeout: float) -> None:
    try:
        import psutil
    except ImportError:
        time.sleep(3)
        return
    deadline = time.monotonic() + timeout
    for pid in pids:
        try:
            psutil.Process(pid).wait(timeout=max(0.1, deadline - time.monotonic()))
        except psutil.NoSuchProcess:
            continue
        except psutil.TimeoutExpired:
            raise UpdateError("Lectio non si e' chiuso in tempo: aggiornamento annullato.")


def _retry(action: Callable[[], None], attempts: int = 40, delay: float = 0.25) -> None:
    for attempt in range(attempts):
        try:
            action()
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def replace_executable(source: Path, target: Path) -> None:
    """Sostituisce ``target`` con una copia di ``source``, tenendo il vecchio in ``.old``.

    Se la copia non riesce il vecchio exe torna al suo posto.
    """
    old = _sibling(target, OLD_EXE_SUFFIX)
    staging = target.with_name(target.name + ".tmp")
    shutil.copy2(source, staging)
    try:
        if old.exists():
            _retry(old.unlink)
        _retry(lambda: os.replace(target, old))
        try:
            _retry(lambda: os.replace(staging, target))
        except OSError:
            os.replace(old, target)
            raise
    finally:
        staging.unlink(missing_ok=True)


def finish_update(arguments: list[str]) -> int:
    """Modalita' ``--finish-update``: eseguita dal nuovo exe, senza GUI."""
    import argparse

    parser = argparse.ArgumentParser(prog="Lectio --finish-update")
    parser.add_argument(FINISH_FLAG, action="store_true")
    parser.add_argument("--target", required=True)
    parser.add_argument("--wait-pid", type=int, action="append", default=[])
    options, _unknown = parser.parse_known_args(arguments)
    target = Path(options.target)
    error = ""
    try:
        _wait_for_exit(options.wait_pid, timeout=90)
        replace_executable(current_exe(), target)
    except Exception as exc:  # noqa: BLE001 - qualunque errore va mostrato
        LOGGER.exception("Aggiornamento non completato.")
        error = str(exc) or exc.__class__.__name__
    try:
        subprocess.Popen([str(target)], close_fds=True, cwd=str(target.parent))
    except OSError as exc:
        error = error or f"Riavvio non riuscito: {exc}"
    if error:
        _message_box(
            "Lectio - aggiornamento non riuscito",
            f"L'aggiornamento non e' stato installato:\n{error}\n\n"
            "Lectio resta alla versione precedente. Puoi scaricare la nuova versione da:\n"
            f"{RELEASES_PAGE}",
        )
        return 1
    return 0


def _message_box(title: str, text: str) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, title, 0x00000030)
            return
        except (AttributeError, OSError):
            pass
    print(f"{title}: {text}", file=sys.stderr)
