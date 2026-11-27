"""Trova le cartelle di OneDrive, Google Drive e Dropbox gia' presenti su questo PC.

Lectio non parla con i servizi cloud: usa la cartella che il loro programma per
desktop tiene sincronizzata. Qui la si trova senza chiedere niente all'utente:

* **OneDrive**: account collegati in ``HKCU\\Software\\Microsoft\\OneDrive\\Accounts``
  (la variabile ``OneDrive`` da sola non basta: esiste anche senza account);
* **Dropbox**: ``info.json`` in ``%APPDATA%\\Dropbox`` o ``%LOCALAPPDATA%\\Dropbox``;
* **Google Drive per desktop**: in streaming un'unita' virtuale con etichetta
  "Google Drive" (es. ``G:\\Il mio Drive``), in mirroring la cartella "Il mio
  Drive" scritta in ``root_preference_sqlite.db``.

Poi ``prepare_target`` sceglie la sottocartella ``Lectio`` (o riconosce quella
gia' preparata da un altro PC) e ``keep_offline`` chiede di tenerla sempre sul
PC dove il servizio lo permette.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import string
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..paths import DB_FILENAME, SYNC_MARKER_NAME

LOGGER = logging.getLogger(__name__)

ONEDRIVE = "onedrive"
GOOGLE_DRIVE = "google_drive"
DROPBOX = "dropbox"

FOLDER_NAME = "Lectio"
_ALTERNATIVE_NAMES = ("Lectio dati", "Lectio dati 2", "Lectio dati 3")

# "Il mio Drive" nelle lingue piu' comuni (Google Drive per desktop usa la lingua di Windows).
MY_DRIVE_NAMES = (
    "Il mio Drive", "My Drive", "Mi unidad", "Meine Ablage", "Mon Drive", "Meu Drive", "Mijn Drive",
    "Mój dysk", "Min disk", "Mit drev", "Oma Drive", "Můj disk", "Saját meghajtó", "Мой диск",
)
# Cartelle dell'unita' Google Drive che non sono "Il mio Drive".
_DRIVE_OTHER_NAMES = {
    name.casefold() for name in (
        "Drive condivisi", "Shared drives", "Unidades compartidas", "Geteilte Ablagen", "Drives partagés",
        "Drives compartilhados", "Altri computer", "Other computers", "Otros ordenadores", "Weitere Computer",
        "Autres ordinateurs", "Outros computadores", "$RECYCLE.BIN", "System Volume Information",
    )
}
GOOGLE_DRIVE_LABEL = "Google Drive"


@dataclass
class CloudRoot:
    """Cartella principale di un servizio cloud sincronizzata su questo PC."""

    service: str
    label: str
    path: Path
    # True se i file possono restare solo nel cloud (Google Drive in streaming):
    # il database va reso "Disponibile offline" a mano.
    streaming: bool = False

    @property
    def service_name(self) -> str:
        return {ONEDRIVE: "OneDrive", GOOGLE_DRIVE: "Google Drive", DROPBOX: "Dropbox"}.get(self.service, self.service)


@dataclass
class Detection:
    roots: list[CloudRoot] = field(default_factory=list)
    # Servizi installati ma non pronti (es. OneDrive senza account): da spiegare all'utente.
    hints: list[str] = field(default_factory=list)


@dataclass
class Target:
    """Sottocartella di Lectio dentro una ``CloudRoot``."""

    root: CloudRoot
    folder: Path
    # "new": da creare/riempire; "existing": dati di Lectio gia' presenti;
    # "incomplete": c'e' il segno di Lectio ma il database non e' ancora arrivato.
    state: str
    created_by: str = ""


# ---------------------------------------------------------------------------
# Letture di sistema (separate per poterle sostituire nei test)
# ---------------------------------------------------------------------------
def _onedrive_accounts() -> list[dict[str, str]]:
    """Account OneDrive collegati: nome della chiave, cartella, nome visualizzato."""
    try:
        import winreg
    except ImportError:
        return []
    accounts: list[dict[str, str]] = []
    try:
        base = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\OneDrive\Accounts")
    except OSError:
        return []
    with base:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(base, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(base, name) as key:
                    values = {}
                    for value_name in ("UserFolder", "DisplayName", "UserEmail"):
                        try:
                            values[value_name] = str(winreg.QueryValueEx(key, value_name)[0] or "")
                        except OSError:
                            values[value_name] = ""
            except OSError:
                continue
            accounts.append({"key": name, "folder": values["UserFolder"], "display": values["DisplayName"],
                             "email": values["UserEmail"]})
    return accounts


def _registry_key_exists(path: str) -> bool:
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, path))
        return True
    except (ImportError, OSError):
        return False


def _drivefs_preferences() -> dict:
    """``PerAccountPreferences`` di Google Drive per desktop (lettera dell'unita' scelta, ecc.)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\DriveFS") as key:
            raw = winreg.QueryValueEx(key, "PerAccountPreferences")[0]
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ImportError, OSError, ValueError, TypeError):
        return {}


def _fixed_drives() -> list[str]:
    """Lettere delle unita' locali (salta quelle di rete, che possono bloccare a lungo)."""
    if os.name != "nt":
        return []
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    mask = kernel32.GetLogicalDrives()
    drives = []
    for index, letter in enumerate(string.ascii_uppercase):
        if mask & (1 << index):
            root = f"{letter}:\\"
            if kernel32.GetDriveTypeW(root) == 3:  # DRIVE_FIXED: anche l'unita' di Google Drive
                drives.append(root)
    return drives


def _volume_label(root: str) -> str:
    if os.name != "nt":
        return ""
    import ctypes

    label = ctypes.create_unicode_buffer(261)
    fs_name = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
        root, label, 261, None, None, None, fs_name, 261)
    return label.value if ok else ""


def _google_drive_volumes() -> list[Path]:
    volumes = [Path(root) for root in _fixed_drives() if _volume_label(root) == GOOGLE_DRIVE_LABEL]
    # Lettera scelta nelle preferenze ("mount_point_path"), anche se l'etichetta fosse diversa.
    for entry in _drivefs_preferences().get("per_account_preferences", []) or []:
        value = entry.get("value") if isinstance(entry, dict) else None
        mount = str((value or {}).get("mount_point_path") or "").strip().rstrip("\\/")
        if mount:
            # "G", "G:" o un percorso completo
            path = Path(f"{mount[0]}:\\") if len(mount) <= 2 else Path(mount)
            if path.is_dir() and path not in volumes:
                volumes.append(path)
    return volumes


def _drivefs_mirror_roots(drivefs_dir: Path) -> list[Path]:
    """Cartelle "Il mio Drive" in mirroring (tabella ``roots`` con ``is_my_drive = 1``)."""
    db = drivefs_dir / "root_preference_sqlite.db"
    if not db.is_file():
        return []
    try:
        connection = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=1)
        try:
            rows = connection.execute(
                "SELECT last_seen_absolute_path FROM roots WHERE is_my_drive = 1").fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        LOGGER.debug("Preferenze di Google Drive non leggibili", exc_info=True)
        return []
    return [Path(absolute) for (absolute,) in rows if absolute and Path(absolute).is_dir()]


# ---------------------------------------------------------------------------
# Rilevamento
# ---------------------------------------------------------------------------
def _is_hidden(path: Path) -> bool:
    if path.name.startswith("."):
        return True
    try:
        attributes = getattr(path.stat(), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attributes & 0x2)  # FILE_ATTRIBUTE_HIDDEN


def find_my_drive(volume: Path) -> Path | None:
    """Cartella "Il mio Drive" in un'unita' di Google Drive (anche con piu' account)."""
    for depth_root in [volume] + _visible_dirs(volume):
        for name in MY_DRIVE_NAMES:
            candidate = depth_root / name
            if candidate.is_dir():
                return candidate
    # Lingua non prevista: l'unica cartella visibile che non e' "Drive condivisi"/"Altri computer".
    others = [path for path in _visible_dirs(volume) if path.name.casefold() not in _DRIVE_OTHER_NAMES]
    return others[0] if len(others) == 1 else None


def _visible_dirs(folder: Path) -> list[Path]:
    try:
        return sorted(path for path in folder.iterdir() if path.is_dir() and not _is_hidden(path))
    except OSError:
        return []


def _onedrive_label(account: dict[str, str]) -> str:
    if account["key"].casefold() == "personal":
        return "OneDrive personale"
    name = account.get("display") or account.get("email") or "lavoro o scuola"
    return f"OneDrive - {name}"


def _detect_onedrive(env: dict[str, str], detection: Detection) -> None:
    accounts = [account for account in _onedrive_accounts() if account.get("folder")]
    for account in accounts:
        folder = Path(account["folder"])
        if folder.is_dir():
            detection.roots.append(CloudRoot(ONEDRIVE, _onedrive_label(account), folder))
    if not accounts and env.get("ONEDRIVE") and Path(env["ONEDRIVE"]).is_dir():
        detection.hints.append("OneDrive e' installato ma non e' collegato a un account: accedi da OneDrive "
                               "(icona a forma di nuvola vicino all'orologio) e riprova.")


def _detect_dropbox(env: dict[str, str], detection: Detection) -> None:
    seen: set[str] = set()
    for base in (env.get("APPDATA"), env.get("LOCALAPPDATA")):
        if not base:
            continue
        info = Path(base) / "Dropbox" / "info.json"
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for kind, label in (("personal", "Dropbox"), ("business", "Dropbox lavoro")):
            path = str((data.get(kind) or {}).get("path") or "") if isinstance(data, dict) else ""
            if path and os.path.normcase(path) not in seen and Path(path).is_dir():
                seen.add(os.path.normcase(path))
                detection.roots.append(CloudRoot(DROPBOX, label, Path(path)))


def _detect_google_drive(env: dict[str, str], detection: Detection) -> None:
    found = 0
    for volume in _google_drive_volumes():
        my_drive = find_my_drive(volume)
        if my_drive is not None:
            letter = str(volume)[:2]
            detection.roots.append(CloudRoot(GOOGLE_DRIVE, f"Google Drive ({letter})", my_drive, streaming=True))
            found += 1
    local = env.get("LOCALAPPDATA")
    if local:
        for mirror in _drivefs_mirror_roots(Path(local) / "Google" / "DriveFS"):
            if all(os.path.normcase(str(root.path)) != os.path.normcase(str(mirror)) for root in detection.roots):
                detection.roots.append(CloudRoot(GOOGLE_DRIVE, "Google Drive (mirroring)", mirror))
                found += 1
    if not found and _registry_key_exists(r"Software\Google\DriveFS"):
        detection.hints.append("Google Drive per desktop e' installato ma l'unita' di Google Drive non e' "
                               "visibile: aprilo, accedi con il tuo account e riprova.")


def detect_cloud_roots(env: dict[str, str] | None = None) -> Detection:
    """Tutte le cartelle cloud pronte su questo PC. Non solleva eccezioni."""
    # chiavi in maiuscolo: su Windows dict(os.environ) le restituisce cosi'
    env = {key.upper(): value for key, value in (os.environ if env is None else env).items()}
    detection = Detection()
    for step in (_detect_onedrive, _detect_google_drive, _detect_dropbox):
        try:
            step(env, detection)
        except Exception:  # un servizio che non si lascia leggere non blocca gli altri
            LOGGER.warning("Rilevamento cartella cloud non riuscito (%s)", step.__name__, exc_info=True)
    return detection


# ---------------------------------------------------------------------------
# Cartella di Lectio dentro il servizio
# ---------------------------------------------------------------------------
def _read_marker(folder: Path) -> dict:
    try:
        data = json.loads((folder / SYNC_MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _is_empty(folder: Path) -> bool:
    try:
        return not any(folder.iterdir())
    except OSError:
        return False


def prepare_target(root: CloudRoot, names: Iterable[str] | None = None) -> Target:
    """Sceglie la cartella di Lectio in ``root`` senza toccare nulla.

    Prima cerca una cartella gia' preparata da Lectio (altro PC); poi la prima
    tra ``Lectio``, ``Lectio dati``... che non esiste o e' vuota, cosi' non si
    mescolano i dati con una cartella "Lectio" dell'utente.
    """
    candidates = [Path(root.path) / name for name in (names or (FOLDER_NAME, *_ALTERNATIVE_NAMES))]
    for folder in candidates:
        if (folder / SYNC_MARKER_NAME).is_file():
            marker = _read_marker(folder)
            state = "existing" if (folder / DB_FILENAME).is_file() else "incomplete"
            return Target(root, folder, state, str(marker.get("created_by") or ""))
    for folder in candidates:
        if not folder.exists() or (folder.is_dir() and _is_empty(folder)):
            return Target(root, folder, "new")
    raise OSError(f"In {root.path} ci sono gia' cartelle chiamate «{candidates[0].name}» con altri file: "
                  "scegli la cartella a mano.")


def create_target(target: Target) -> Path:
    target.folder.mkdir(parents=True, exist_ok=True)
    keep_offline(target)
    return target.folder


def keep_offline(target: Target) -> bool:
    """Chiede di tenere i file sempre su questo PC (OneDrive e Dropbox: attributo "pinned").

    Google Drive in streaming non offre un modo da programma: va fatto
    dall'utente (tasto destro → Accesso offline → Disponibile offline).
    """
    if os.name != "nt" or target.root.service not in (ONEDRIVE, DROPBOX) or not target.folder.is_dir():
        return False
    try:
        result = subprocess.run(
            ["attrib", "+P", "-U", str(target.folder), "/S", "/D"], capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            LOGGER.info("attrib +P non riuscito su %s: %s", target.folder, result.stdout.strip())
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        LOGGER.info("Impossibile tenere offline %s", target.folder, exc_info=True)
        return False
