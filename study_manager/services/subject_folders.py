"""Cartelle su disco per ogni materia.

Ogni materia ha una cartella nella radice di funzionamento dell'app
(``<root>/Materie/<Nome materia>``) con sottocartelle dedicate ai materiali
generati da Lectio:

* ``Trascrizioni`` - i .txt prodotti dalle videolezioni;
* ``Appunti``      - TXT/LaTeX/Word esportati dal modulo PDF e Slide;
* ``Flashcard``    - export CSV delle flashcard della materia;
* ``Slide``        - copia dei PDF e delle presentazioni aggiunti come materiali;
* ``Documenti``    - copia degli altri documenti (creata solo quando serve);
* ``Riassunto``    - riassunto del corso e riassunto totale (creata solo quando serve);
* ``Esami``        - PDF dei temi d'esame e ``Domande_esame.md`` (creata solo quando serve).

Dalla v1.5 i materiali aggiunti vengono **copiati** nella cartella della materia
(l'originale resta dove era): cosi' la cartella ``Materie`` contiene tutto il
necessario per essere sincronizzata su un altro PC o condivisa. I video delle
lezioni restano invece dove sono, perche' troppo pesanti.
"""

from __future__ import annotations

import csv
import filecmp
import logging
import os
import re
import shutil
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Iterable


LOGGER = logging.getLogger(__name__)

KIND_TRANSCRIPTS = "transcripts"
KIND_NOTES = "notes"
KIND_FLASHCARDS = "flashcards"
KIND_SLIDES = "slides"
KIND_DOCUMENTS = "documents"
KIND_SUMMARY = "summary"
KIND_EXAMS = "exams"

SUBFOLDERS: dict[str, str] = {
    KIND_TRANSCRIPTS: "Trascrizioni",
    KIND_NOTES: "Appunti",
    KIND_FLASHCARDS: "Flashcard",
    KIND_SLIDES: "Slide",
}
# Sottocartelle create solo quando ci finisce dentro un file.
ON_DEMAND_SUBFOLDERS: dict[str, str] = {
    KIND_DOCUMENTS: "Documenti",
    KIND_SUMMARY: "Riassunto",
    KIND_EXAMS: "Esami",
}
SLIDE_EXTENSIONS = {".pdf", ".ppt", ".pptx", ".odp", ".key"}

FLASHCARD_EXPORT_NAME = "Flashcard.csv"

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def folder_name(subject: str, max_length: int = 80) -> str:
    """Nome di cartella valido su Windows a partire dal nome della materia."""
    value = unicodedata.normalize("NFKC", str(subject or ""))
    value = _INVALID.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        return ""
    if value.split(".", 1)[0].strip().upper() in _RESERVED:
        value = f"_{value}"
    return value[:max_length].rstrip(" .")


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def is_inside(path: str | Path, folder: str | Path) -> bool:
    """``True`` se ``path`` si trova dentro ``folder`` (confronto case-insensitive su Windows)."""
    child = os.path.normcase(os.path.abspath(str(path)))
    parent = os.path.normcase(os.path.abspath(str(folder)))
    return child == parent or child.startswith(parent.rstrip("\\/") + os.sep)


def _same_content(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def files_size(paths: Iterable[str | Path]) -> int:
    """Dimensione totale in byte dei file esistenti."""
    total = 0
    for path in paths:
        try:
            total += Path(path).stat().st_size
        except OSError:
            continue
    return total


def unique_target(folder: Path, name: str) -> Path:
    """Percorso libero in ``folder``: aggiunge `` (2)``, `` (3)``... se serve."""
    target = folder / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    index = 2
    while True:
        candidate = folder / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def move_file_into(source: str | Path, folder: str | Path) -> Path:
    """Sposta un file in ``folder`` senza sovrascrivere nulla e restituisce il nuovo percorso."""
    src = Path(source)
    dest_dir = Path(folder)
    if _same_path(src.parent, dest_dir):
        return src
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = unique_target(dest_dir, src.name)
    shutil.move(str(src), str(target))
    return target


class SubjectFolders:
    """Gestisce la cartella ``Materie`` e le sottocartelle di ogni materia."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    # ----------------------------------------------------------------- percorsi
    def path_for(self, subject: str) -> Path | None:
        """Cartella della materia (senza crearla); ``None`` se il nome e' vuoto."""
        name = folder_name(subject)
        return self.root / name if name else None

    def ensure(self, subject: str) -> Path | None:
        """Crea (se mancano) la cartella della materia e le sue sottocartelle."""
        folder = self.path_for(subject)
        if folder is None:
            return None
        try:
            for sub in SUBFOLDERS.values():
                (folder / sub).mkdir(parents=True, exist_ok=True)
        except OSError:
            LOGGER.exception("Impossibile creare la cartella della materia '%s'.", subject)
            return None
        return folder

    def subfolder(self, subject: str, kind: str) -> Path | None:
        """Sottocartella di un tipo di materiale (creata se manca)."""
        folder = self.ensure(subject)
        if folder is None:
            return None
        if kind in ON_DEMAND_SUBFOLDERS:
            target = folder / ON_DEMAND_SUBFOLDERS[kind]
            target.mkdir(parents=True, exist_ok=True)
            return target
        return folder / SUBFOLDERS[kind]

    # ----------------------------------------------------------- materiali
    def is_in_subject(self, path: str | Path, subject: str) -> bool:
        """``True`` se il file sta gia' dentro la cartella della materia."""
        folder = self.path_for(subject)
        return bool(path) and folder is not None and is_inside(path, folder)

    def material_kind(self, path: str | Path) -> str:
        return KIND_SLIDES if Path(path).suffix.lower() in SLIDE_EXTENSIONS else KIND_DOCUMENTS

    def copy_material(self, source: str | Path, subject: str, kind: str | None = None) -> Path:
        """Copia un materiale nella cartella della materia e restituisce il percorso della copia.

        - se il file e' gia' nella cartella della materia non viene copiato;
        - se nella destinazione c'e' gia' un file identico viene riusato;
        - se c'e' un file diverso con lo stesso nome, la copia riceve `` (2)``, `` (3)``...

        L'originale non viene mai spostato ne' modificato.
        """
        src = Path(source)
        if self.is_in_subject(src, subject):
            return src
        if not src.is_file():
            raise FileNotFoundError(f"File non trovato: {src}")
        folder = self.subfolder(subject, kind or self.material_kind(src))
        if folder is None:
            raise OSError("Cartella della materia non disponibile.")
        folder.mkdir(parents=True, exist_ok=True)
        existing = folder / src.name
        if existing.is_file() and _same_content(src, existing):
            return existing
        target = unique_target(folder, src.name)
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.part")
        try:
            shutil.copy2(src, temp)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return target

    def ensure_all(self, subjects: Iterable[str]) -> int:
        created = 0
        for subject in subjects:
            folder = self.path_for(subject)
            if folder is None:
                continue
            existed = folder.is_dir()
            if self.ensure(subject) is not None and not existed:
                created += 1
        return created

    def subject_of(self, path: str | Path) -> str | None:
        """Nome della cartella-materia che contiene ``path``, se ce n'e' una."""
        if not path or not is_inside(path, self.root):
            return None
        try:
            relative = Path(os.path.abspath(str(path))).relative_to(Path(os.path.abspath(self.root)))
        except ValueError:
            return None
        return relative.parts[0] if relative.parts else None

    # ------------------------------------------------------------------ rinomina
    def rename(self, old_subject: str, new_subject: str) -> tuple[Path | None, Path | None]:
        """Rinomina la cartella di una materia.

        Restituisce ``(vecchia, nuova)``. Se la nuova cartella esiste gia', i
        contenuti vengono uniti senza sovrascrivere file.
        """
        old = self.path_for(old_subject)
        new = self.path_for(new_subject)
        if new is None:
            return old, None
        if old is None or not old.is_dir():
            return old, self.ensure(new_subject)
        if old.name == new.name:
            return old, self.ensure(new_subject)
        if _same_path(old, new):
            # Cambia solo maiuscole/minuscole: su Windows serve un passaggio intermedio.
            temp = old.with_name(f".{old.name}.{uuid.uuid4().hex[:8]}")
            old.rename(temp)
            temp.rename(new)
        elif not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
        else:
            self._merge(old, new)
        self.ensure(new_subject)
        return old, new

    def _merge(self, source: Path, target: Path) -> None:
        for entry in list(source.iterdir()):
            dest = target / entry.name
            if entry.is_dir() and dest.is_dir():
                self._merge(entry, dest)
                continue
            if dest.exists():
                dest = unique_target(target, entry.name)
            shutil.move(str(entry), str(dest))
        try:
            source.rmdir()
        except OSError:
            LOGGER.debug("Cartella %s non vuota dopo l'unione.", source)

    def remove_if_empty(self, subject: str) -> bool:
        """Rimuove la cartella della materia solo se non contiene file."""
        folder = self.path_for(subject)
        if folder is None or not folder.is_dir():
            return False
        if any(path.is_file() for path in folder.rglob("*")):
            return False
        for path in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            try:
                path.rmdir()
            except OSError:
                return False
        try:
            folder.rmdir()
        except OSError:
            return False
        return True

    # ------------------------------------------------------------------- export
    def export_flashcards(self, subject: str, cards: list[dict[str, Any]]) -> Path | None:
        """Scrive ``Flashcard/Flashcard.csv`` con le schede della materia.

        Formato CSV con ``;`` e BOM UTF-8, apribile direttamente in Excel e
        importabile in Anki. Senza schede il file viene rimosso.
        """
        folder = self.subfolder(subject, KIND_FLASHCARDS)
        if folder is None:
            return None
        target = folder / FLASHCARD_EXPORT_NAME
        if not cards:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                LOGGER.debug("Impossibile rimuovere %s.", target, exc_info=True)
            return None
        temp = target.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["Fronte", "Retro", "Note"])
            for card in sorted(cards, key=lambda value: int(value.get("id", 0) or 0)):
                writer.writerow(
                    [
                        str(card.get("front") or ""),
                        str(card.get("back") or ""),
                        str(card.get("notes") or ""),
                    ]
                )
        os.replace(temp, target)
        return target
