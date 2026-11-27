"""Condivisione di una materia: esporta/importa uno ZIP con tutto il materiale.

Diverso dal backup (``backup.py``), che copia *tutto* Lectio per spostarlo su
un altro PC: lo ZIP materia contiene una sola materia, organizzata in cartelle
leggibili anche da chi non usa Lectio, e si puo' passare ad altri studenti.

Struttura::

    <Materia>.zip
    ├── LEGGIMI.md              "Materiale organizzato con Lectio" + indice
    ├── Slide/2025-26/...       PDF e presentazioni, per anno accademico
    ├── Documenti/              altri documenti
    ├── Appunti/                export di PDF e Slide (.md/.tex/.docx...)
    ├── Riassunto/              Riassunto_corso.md, riassunto_rapido.md, PDF
    ├── Esami/                  temi d'esame PDF e Domande_esame.md
    ├── Trascrizioni/           .txt delle videolezioni
    ├── Flashcard/Flashcard.csv
    └── lectio-materia.json     dati per l'importazione in Lectio

``lectio-materia.json`` ha un numero di formato: versioni future di Lectio
devono continuare a leggere gli ZIP creati oggi.
"""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from ..config import APP_VERSION
from .course_summary import summary_markdown
from .exam_questions import exam_markdown
from .subject_folders import (
    KIND_DOCUMENTS,
    KIND_EXAMS,
    KIND_FLASHCARDS,
    KIND_NOTES,
    KIND_SLIDES,
    KIND_SUMMARY,
    KIND_TRANSCRIPTS,
    ON_DEMAND_SUBFOLDERS,
    SLIDE_EXTENSIONS,
    SUBFOLDERS,
    SubjectFolders,
    _same_content,
    folder_name,
    is_inside,
    unique_target,
)
from .subjects import academic_year_folder

SHARE_FORMAT = "lectio-materia"
SHARE_VERSION = 1
MANIFEST_NAME = "lectio-materia.json"
README_NAME = "LEGGIMI.md"
REPOSITORY_URL = "https://github.com/andreagioiaa/Lectio"

# Voci selezionabili nella finestra di esportazione.
PART_SLIDES = "slides"
PART_NOTES = "notes"
PART_SUMMARY = "summary"
PART_EXAMS = "exams"
PART_TRANSCRIPTS = "transcripts"
PART_FLASHCARDS = "flashcards"
PART_PERSONAL = "personal"
PART_LABELS = {
    PART_SLIDES: "PDF e slide originali",
    PART_NOTES: "Appunti (capitoli e file esportati)",
    PART_SUMMARY: "Riassunto del corso",
    PART_EXAMS: "Domande d'esame e temi PDF",
    PART_TRANSCRIPTS: "Trascrizioni delle videolezioni",
    PART_FLASHCARDS: "Flashcard (senza lo storico dei ripassi)",
    PART_PERSONAL: "Dati personali (ore studiate, note, dubbi)",
}
DEFAULT_PARTS = {part: part != PART_PERSONAL for part in PART_LABELS}

_FOLDER_OF_KIND = {**SUBFOLDERS, **ON_DEMAND_SUBFOLDERS}
ALLOWED_EXTENSIONS = {
    ".pdf", ".ppt", ".pptx", ".odp", ".key", ".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods",
    ".md", ".tex", ".txt", ".csv", ".json", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp",
}
MAX_FILE_BYTES = 2 * 1024 ** 3
MAX_TOTAL_BYTES = 8 * 1024 ** 3
MAX_ENTRIES = 20000


class ShareError(RuntimeError):
    pass


class ShareCancelled(ShareError):
    pass


Progress = Callable[[float, str], None]


def _noop(_fraction: float, _message: str) -> None:
    pass


# ---------------------------------------------------------------------------
# Esportazione
# ---------------------------------------------------------------------------
@dataclass
class ExportPlan:
    subject: str
    files: dict[str, list[tuple[str, Path]]] = field(default_factory=dict)   # parte -> [(percorso zip, file)]
    generated: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # parte -> [(percorso zip, testo)]
    data: dict[str, Any] = field(default_factory=dict)

    def size(self, part: str) -> int:
        total = 0
        for _name, path in self.files.get(part, []):
            try:
                total += path.stat().st_size
            except OSError:
                continue
        total += sum(len(text.encode("utf-8")) for _n, text in self.generated.get(part, []))
        return total

    def count(self, part: str) -> int:
        return len(self.files.get(part, [])) + len(self.generated.get(part, []))


class _Names:
    """Nomi unici dentro lo ZIP (case-insensitive, come su Windows)."""

    def __init__(self) -> None:
        self.used: set[str] = set()

    def take(self, wanted: str) -> str:
        path = PurePosixPath(wanted)
        candidate, index = str(path), 2
        while candidate.casefold() in self.used:
            candidate = str(path.with_name(f"{path.stem} ({index}){path.suffix}"))
            index += 1
        self.used.add(candidate.casefold())
        return candidate


def _rel_in_subject(folders: SubjectFolders, subject: str, path: Path) -> str | None:
    root = folders.path_for(subject)
    if root is None or not is_inside(path, root):
        return None
    return Path(os.path.relpath(os.path.abspath(path), os.path.abspath(root))).as_posix()


def plan_export(db, folders: SubjectFolders, subject: str) -> ExportPlan:
    """Raccoglie file e dati della materia; la scelta delle parti avviene dopo (``write_export``)."""
    plan = ExportPlan(subject=subject)
    names = _Names()
    names.take(MANIFEST_NAME)
    names.take(README_NAME)
    for part in PART_LABELS:
        plan.files[part] = []
        plan.generated[part] = []
    root = folders.path_for(subject)

    # --- materiali: slide per anno, documenti, file generati (Appunti)
    material_keys: dict[int, str] = {}
    key_by_path: dict[str, str] = {}
    materials_data: list[dict[str, Any]] = []
    material_files: set[str] = set()
    for row in db.list_materials_for_subject(subject):
        if str(row.get("file_path") or "").strip():
            key_by_path[os.path.normcase(os.path.abspath(str(row["file_path"])))] = f"m{row['id']}"
        path = Path(str(row.get("file_path") or ""))
        item = {
            "key": f"m{row['id']}", "title": row.get("title") or path.stem, "file": "",
            "target_hours": row.get("target_hours") or 0, "academic_year": row.get("academic_year") or "",
            "logged_hours": row.get("logged_hours") or 0,
        }
        material_keys[int(row["id"])] = item["key"]
        if str(row.get("file_path") or "").strip() and path.is_file():
            inside = _rel_in_subject(folders, subject, path)
            notes_folder = _FOLDER_OF_KIND[KIND_NOTES]
            if inside and inside.split("/")[0] == notes_folder:
                part, wanted = PART_NOTES, inside
            elif path.suffix.lower() in SLIDE_EXTENSIONS:
                year = academic_year_folder(row.get("academic_year"))
                part = PART_SLIDES
                wanted = "/".join(p for p in (_FOLDER_OF_KIND[KIND_SLIDES], year, path.name) if p)
            else:
                part, wanted = PART_SLIDES, f"{_FOLDER_OF_KIND[KIND_DOCUMENTS]}/{path.name}"
            item["file"] = names.take(wanted)
            item["part"] = part
            plan.files[part].append((item["file"], path))
            material_files.add(os.path.normcase(os.path.abspath(path)))
        materials_data.append(item)

    def add_folder(part: str, kind: str, skip: Callable[[Path], bool] = lambda _p: False) -> None:
        if root is None:
            return
        folder = root / _FOLDER_OF_KIND[kind]
        if not folder.is_dir():
            return
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS or skip(path):
                continue
            if os.path.normcase(os.path.abspath(path)) in material_files:
                continue
            relative = path.relative_to(root).as_posix()
            plan.files[part].append((names.take(relative), path))

    add_folder(PART_NOTES, KIND_NOTES)
    add_folder(PART_TRANSCRIPTS, KIND_TRANSCRIPTS)
    add_folder(PART_SUMMARY, KIND_SUMMARY, skip=lambda p: p.name.lower() == "riassunto_corso.md")
    exam_name = "domande_esame.md"
    add_folder(PART_EXAMS, KIND_EXAMS, skip=lambda p: p.name.lower() == exam_name)

    # --- capitoli di appunti (testo in PDF e Slide)
    block_keys: dict[int, str] = {}
    blocks_data = []
    for row in db.list_note_blocks(subject):
        block = db.get_note_block(int(row["id"])) or {}
        key = f"b{row['id']}"
        block_keys[int(row["id"])] = key
        meta = {}
        try:
            meta = json.loads(block.get("meta") or "{}")
        except (TypeError, ValueError):
            meta = {}
        doubts = meta.pop("doubts", []) if isinstance(meta, dict) else []
        pdf = str(block.get("pdf_path") or "").strip()
        pdf_key = key_by_path.get(os.path.normcase(os.path.abspath(pdf)), "") if pdf else ""
        blocks_data.append({
            "key": key, "position": block.get("position") or 1, "title": block.get("title") or "",
            "source": block.get("source") or "", "fmt": block.get("fmt") or "markdown",
            "meta": meta, "doubts": doubts, "material": pdf_key,
        })

    # --- riassunto del corso
    chapters = db.list_summary_chapters(subject, with_source=True)
    chapters_data = [{
        "position": c.get("position") or i, "title": c.get("title") or "", "source": c.get("source") or "",
        "fmt": c.get("fmt") or "markdown", "block": block_keys.get(int(c["note_block_id"] or 0), ""),
    } for i, c in enumerate(chapters, 1)]
    if chapters:
        plan.generated[PART_SUMMARY].append(
            (names.take(f"{_FOLDER_OF_KIND[KIND_SUMMARY]}/Riassunto_corso.md"), summary_markdown(subject, chapters)))

    # --- esame
    sessions = db.list_exam_sessions(subject)
    questions = db.list_exam_questions(subject)
    exam_files = {os.path.normcase(os.path.abspath(path)): name for name, path in plan.files[PART_EXAMS]}
    sessions_data = []
    for session in sessions:
        pdf = str(session.get("pdf_path") or "")
        archive = exam_files.get(os.path.normcase(os.path.abspath(pdf)), "") if pdf else ""
        if pdf and not archive and Path(pdf).is_file():
            archive = names.take(f"{_FOLDER_OF_KIND[KIND_EXAMS]}/{Path(pdf).name}")
            plan.files[PART_EXAMS].append((archive, Path(pdf)))
        sessions_data.append({"key": f"s{session['id']}", "exam_date": session.get("exam_date") or "",
                              "title": session.get("title") or "", "notes": session.get("notes") or "",
                              "pdf": archive})
    questions_data = [{
        "session": f"s{q['session_id']}" if q.get("session_id") is not None else "",
        "number": q.get("number") or 1, "text": q.get("text") or "", "solution": q.get("solution") or "",
        "notes": q.get("notes") or "", "material": material_keys.get(int(q["material_id"] or 0), ""),
        "group": f"g{q['group_id']}" if q.get("group_id") is not None else "",
    } for q in questions]
    if questions:
        plan.generated[PART_EXAMS].append(
            (names.take(f"{_FOLDER_OF_KIND[KIND_EXAMS]}/Domande_esame.md"),
             exam_markdown(subject, sessions, questions)))

    # --- flashcard (senza storico)
    cards = db.list_flashcards(subject=subject, limit=100000)
    cards_data = [{"front": c.get("front") or "", "back": c.get("back") or "", "notes": c.get("notes") or "",
                   "block": block_keys.get(int(c.get("note_block_id") or 0), "")} for c in cards]
    if cards:
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(["Fronte", "Retro", "Note"])
        for card in cards_data:
            writer.writerow([card["front"], card["back"], card["notes"]])
        plan.generated[PART_FLASHCARDS].append(
            (names.take(f"{_FOLDER_OF_KIND[KIND_FLASHCARDS]}/Flashcard.csv"), "﻿" + buffer.getvalue()))

    details = db.get_subject(subject) or {}
    plan.data = {
        "subject": {"name": subject, "teacher_name": details.get("teacher_name") or "",
                    "teacher_email": details.get("teacher_email") or "", "cfu": details.get("cfu") or 0,
                    "exam_date": details.get("exam_date") or "", "notes": details.get("notes") or ""},
        "materials": materials_data, "note_blocks": blocks_data, "summary_chapters": chapters_data,
        "exam_sessions": sessions_data, "exam_questions": questions_data, "flashcards": cards_data,
    }
    return plan


def _filtered_data(plan: ExportPlan, parts: dict[str, bool]) -> dict[str, Any]:
    data = json.loads(json.dumps(plan.data))
    personal = parts.get(PART_PERSONAL, False)
    if not personal:
        data["subject"]["notes"] = ""
    included = {name for part, files in plan.files.items() if parts.get(part) for name, _p in files}
    for material in data["materials"]:
        if material.get("file") not in included:
            material["file"] = ""
        if not personal:
            material["logged_hours"] = 0
        material.pop("part", None)
    if not parts.get(PART_SLIDES):
        data["materials"] = [m for m in data["materials"] if m["file"]]
    if parts.get(PART_NOTES):
        for block in data["note_blocks"]:
            if not personal:
                block["doubts"] = []
    else:
        data["note_blocks"] = []
    if not parts.get(PART_SUMMARY):
        data["summary_chapters"] = []
    if not parts.get(PART_EXAMS):
        data["exam_sessions"], data["exam_questions"] = [], []
    else:
        for session in data["exam_sessions"]:
            if session["pdf"] not in included:
                session["pdf"] = ""
    if not parts.get(PART_FLASHCARDS):
        data["flashcards"] = []
    keys = {m["key"] for m in data["materials"]}
    blocks = {b["key"] for b in data["note_blocks"]}
    for block in data["note_blocks"]:
        if block["material"] not in keys:
            block["material"] = ""
    for item in data["summary_chapters"] + data["flashcards"]:
        if item.get("block") not in blocks:
            item["block"] = ""
    for question in data["exam_questions"]:
        if question["material"] not in keys:
            question["material"] = ""
    return data


def readme_text(subject: str, data: dict[str, Any], counts: dict[str, int]) -> str:
    stamp = datetime.now().strftime("%d/%m/%Y")
    lines = [
        f"# {subject}",
        "",
        f"Materiale della materia **{subject}**, organizzato con **Lectio**, "
        "un'app desktop gratuita per organizzare lo studio universitario.",
        "",
        f"- Lectio: {REPOSITORY_URL}",
        f"- Esportato il {stamp} con Lectio {APP_VERSION}",
        "",
        "## Contenuto",
        "",
    ]
    folders = [
        ("Slide/", "PDF e presentazioni delle lezioni, divisi per anno accademico", PART_SLIDES),
        ("Appunti/", "appunti esportati da PDF e Slide", PART_NOTES),
        ("Riassunto/", "riassunto del corso e riassunto totale", PART_SUMMARY),
        ("Esami/", "temi d'esame e Domande_esame.md, divise per appello", PART_EXAMS),
        ("Trascrizioni/", "trascrizioni delle videolezioni", PART_TRANSCRIPTS),
        ("Flashcard/", "Flashcard.csv (apribile con Excel, importabile in Anki)", PART_FLASHCARDS),
    ]
    for folder, text, part in folders:
        if counts.get(part):
            lines.append(f"- `{folder}` {text} ({counts[part]} file)")
    lines.extend([
        "",
        "## Aprire in Lectio",
        "",
        "In Lectio: **Materie → Importa materia (ZIP)** e scegli questo file. Materiali, capitoli di "
        f"appunti, riassunto, domande d'esame e flashcard tornano collegati alla materia (`{MANIFEST_NAME}`).",
        "",
        "Senza Lectio le cartelle si aprono normalmente: i file `.md` sono testo leggibile da qualunque "
        "editor (formule in LaTeX tra `$`).",
    ])
    return "\n".join(lines) + "\n"


def write_export(plan: ExportPlan, parts: dict[str, bool], target: str | Path,
                 progress: Progress = _noop, cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Scrive lo ZIP con le parti scelte (prima in un file temporaneo, poi lo rinomina)."""
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    chosen = [part for part in PART_LABELS if parts.get(part)]
    files = [(name, path) for part in chosen for name, path in plan.files.get(part, [])]
    generated = [(name, text) for part in chosen for name, text in plan.generated.get(part, [])]
    data = _filtered_data(plan, parts)
    counts = {part: plan.count(part) for part in chosen}
    manifest = {
        "format": SHARE_FORMAT, "version": SHARE_VERSION, "app_version": APP_VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"), "parts": chosen,
        "counts": {
            "materials": len(data["materials"]), "note_blocks": len(data["note_blocks"]),
            "summary_chapters": len(data["summary_chapters"]), "exam_sessions": len(data["exam_sessions"]),
            "exam_questions": len(data["exam_questions"]), "flashcards": len(data["flashcards"]),
            "files": len(files) + len(generated),
        },
        **data,
    }
    total = sum(max(1, _size(path)) for _n, path in files) or 1
    done = 0
    temp = target_path.with_name(target_path.name + ".part")
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr(README_NAME, readme_text(plan.subject, data, counts))
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, text in generated:
                archive.writestr(name, text)
            for name, path in files:
                if cancelled():
                    raise ShareCancelled("Esportazione annullata.")
                progress(done / total, f"Aggiungo {path.name}")
                # PDF e immagini sono gia' compressi: si salvano senza ricomprimere.
                compress = zipfile.ZIP_STORED if path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".pptx", ".docx"} \
                    else zipfile.ZIP_DEFLATED
                archive.write(path, name, compress_type=compress)
                done += max(1, _size(path))
        os.replace(temp, target_path)
    finally:
        temp.unlink(missing_ok=True)
    progress(1.0, "ZIP creato")
    return target_path


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Importazione
# ---------------------------------------------------------------------------
def _safe_name(name: str) -> PurePosixPath | None:
    """Percorso relativo sicuro dentro lo ZIP (niente assoluti, ``..`` o unita')."""
    text = str(name or "").replace("\\", "/")
    if not text or text.startswith("/"):
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts or ":" in text:
        return None
    if any(part in ("", ".", "..") for part in path.parts):
        return None
    return path


def inspect_share(path: str | Path) -> dict[str, Any]:
    """Legge e valida ``lectio-materia.json``; solleva ``ShareError`` se lo ZIP non e' di Lectio."""
    source = Path(path)
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise ShareError("Lo ZIP contiene troppi file.")
            try:
                raw = archive.read(MANIFEST_NAME)
            except KeyError as exc:
                raise ShareError(
                    "Questo ZIP non e' stato creato con «Esporta materia» di Lectio "
                    f"(manca {MANIFEST_NAME})."
                ) from exc
            total = sum(info.file_size for info in infos)
    except zipfile.BadZipFile as exc:
        raise ShareError("Il file scelto non e' uno ZIP valido.") from exc
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ShareError(f"{MANIFEST_NAME} non e' leggibile.") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != SHARE_FORMAT:
        raise ShareError("Lo ZIP non contiene una materia di Lectio.")
    if int(manifest.get("version") or 0) > SHARE_VERSION:
        raise ShareError("Lo ZIP e' stato creato con una versione piu' recente di Lectio: aggiorna l'app.")
    if total > MAX_TOTAL_BYTES:
        raise ShareError("Lo ZIP e' troppo grande per essere importato.")
    subject = manifest.get("subject") if isinstance(manifest.get("subject"), dict) else {}
    if not str(subject.get("name") or "").strip():
        raise ShareError("Nello ZIP manca il nome della materia.")
    for key in ("materials", "note_blocks", "summary_chapters", "exam_sessions", "exam_questions", "flashcards"):
        if not isinstance(manifest.get(key, []), list):
            raise ShareError(f"Dati non validi nello ZIP ({key}).")
        manifest.setdefault(key, [])
    manifest["total_bytes"] = total
    return manifest


def _extract(archive: zipfile.ZipFile, name: str, destination: Path) -> Path:
    """Estrae un file dello ZIP in ``destination`` riusando un file identico gia' presente."""
    info = archive.getinfo(name)
    if info.file_size > MAX_FILE_BYTES:
        raise ShareError(f"{name}: file troppo grande.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.import")
    try:
        with archive.open(info) as source, temp.open("wb") as handle:
            shutil.copyfileobj(source, handle, 1024 * 1024)
        if destination.is_file() and _same_content(temp, destination):
            return destination
        final = destination if not destination.exists() else unique_target(destination.parent, destination.name)
        os.replace(temp, final)
        return final
    finally:
        temp.unlink(missing_ok=True)


def import_share(db, folders: SubjectFolders, path: str | Path, subject: str,
                 progress: Progress = _noop, cancelled: Callable[[], bool] = lambda: False) -> dict[str, Any]:
    """Importa lo ZIP nella materia ``subject`` (nuova o esistente: in quel caso unisce).

    Unione: i file identici non vengono duplicati e i dati gia' presenti (stesso
    titolo/testo) non vengono ricreati. Restituisce un riepilogo con gli avvisi.
    """
    manifest = inspect_share(path)
    name = str(subject or "").strip()
    if not name or not folder_name(name):
        raise ShareError("Nome della materia non valido.")
    info = manifest["subject"]
    if db.get_subject(name) is None:
        db.save_subject(name, str(info.get("teacher_name") or ""), str(info.get("teacher_email") or ""),
                        info.get("cfu") or 0, str(info.get("exam_date") or ""), str(info.get("notes") or ""))
    root = folders.ensure(name)
    if root is None:
        raise ShareError("Cartella della materia non disponibile.")
    summary = {"subject": name, "files": 0, "skipped": 0, "materials": 0, "note_blocks": 0,
               "summary_chapters": 0, "exam_sessions": 0, "exam_questions": 0, "flashcards": 0, "warnings": []}
    extracted: dict[str, Path] = {}
    with zipfile.ZipFile(path) as archive:
        entries = [i for i in archive.infolist() if not i.is_dir() and i.filename not in (MANIFEST_NAME, README_NAME)]
        total = sum(max(1, i.file_size) for i in entries) or 1
        done = 0
        for info_entry in entries:
            if cancelled():
                raise ShareCancelled("Importazione annullata.")
            safe = _safe_name(info_entry.filename)
            if safe is None or Path(safe.name).suffix.lower() not in ALLOWED_EXTENSIONS:
                summary["skipped"] += 1
                summary["warnings"].append(f"{info_entry.filename}: tipo di file non ammesso, saltato.")
                continue
            parts = list(safe.parts)
            # Le slide per anno (Slide/2025-26/x.pdf) tornano nella cartella Slide della materia.
            if parts[0] == _FOLDER_OF_KIND[KIND_SLIDES] and len(parts) > 2:
                parts = [parts[0], *parts[2:]]
            destination = root.joinpath(*parts)
            if not is_inside(destination, root):
                summary["skipped"] += 1
                continue
            progress(done / total * 0.9, f"Estraggo {safe.name}")
            extracted[info_entry.filename] = _extract(archive, info_entry.filename, destination)
            summary["files"] += 1
            done += max(1, info_entry.file_size)

    progress(0.92, "Collego i dati alla materia")
    material_ids: dict[str, int] = {}
    existing_by_title = {str(m.get("title") or "").casefold(): m for m in db.list_materials_for_subject(name)}
    for item in manifest["materials"]:
        file_path = extracted.get(str(item.get("file") or ""))
        found = db.find_material_by_path(str(file_path)) if file_path else None
        if found is None and not file_path:
            found = existing_by_title.get(str(item.get("title") or "").casefold())
        if found is not None:
            material_ids[str(item.get("key"))] = int(found["id"])
            continue
        material_ids[str(item.get("key"))] = db.add_material(
            str(item.get("title") or "Materiale"), str(file_path or ""), name, float(item.get("target_hours") or 0),
            None, academic_year=str(item.get("academic_year") or ""),
        )
        if float(item.get("logged_hours") or 0) > 0:
            db.set_material_hours(material_ids[str(item.get("key"))], float(item["logged_hours"]))
        summary["materials"] += 1

    block_ids: dict[str, int] = {}
    existing_blocks = {(str(b.get("title") or "").casefold()): b for b in db.list_note_blocks(name)}
    for item in manifest["note_blocks"]:
        current = existing_blocks.get(str(item.get("title") or "").casefold())
        if current is not None:
            block_ids[str(item.get("key"))] = int(current["id"])
            continue
        material_id = material_ids.get(str(item.get("material") or ""))
        material = db.get_material(material_id) if material_id else None
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        if item.get("doubts"):
            meta["doubts"] = item["doubts"]
        block_id = db.add_note_block(
            name, str(item.get("title") or "Capitolo"), int(item.get("position") or 0) or None,
            pdf_path=str(material.get("file_path") or "") if material else "", source=str(item.get("source") or ""),
            fmt=str(item.get("fmt") or "markdown"), meta=json.dumps(meta, ensure_ascii=False),
        )
        db.update_note_block(block_id, status="closed")
        block_ids[str(item.get("key"))] = block_id
        summary["note_blocks"] += 1

    existing_chapters = {str(c.get("title") or "").casefold() for c in db.list_summary_chapters(name)}
    for item in sorted(manifest["summary_chapters"], key=lambda c: int(c.get("position") or 0)):
        if str(item.get("title") or "").casefold() in existing_chapters:
            continue
        db.add_summary_chapter(name, str(item.get("title") or "Capitolo"), str(item.get("source") or ""),
                               str(item.get("fmt") or "markdown"),
                               note_block_id=block_ids.get(str(item.get("block") or "")))
        summary["summary_chapters"] += 1

    session_ids: dict[str, int] = {}
    existing_sessions = {(s.get("exam_date") or "", str(s.get("title") or "").casefold()): s
                         for s in db.list_exam_sessions(name)}
    for item in manifest["exam_sessions"]:
        key = (str(item.get("exam_date") or ""), str(item.get("title") or "").casefold())
        if key in existing_sessions:
            session_ids[str(item.get("key"))] = int(existing_sessions[key]["id"])
            continue
        pdf = extracted.get(str(item.get("pdf") or ""))
        try:
            session_ids[str(item.get("key"))] = db.add_exam_session(
                name, str(item.get("exam_date") or ""), str(item.get("title") or ""), str(pdf or ""),
                str(item.get("notes") or ""))
        except ValueError:
            session_ids[str(item.get("key"))] = db.add_exam_session(name, "", str(item.get("title") or ""), str(pdf or ""))
        summary["exam_sessions"] += 1

    existing_questions = {(q.get("session_id"), " ".join(str(q.get("text") or "").split()).casefold())
                          for q in db.list_exam_questions(name)}
    group_first: dict[str, int] = {}
    for item in manifest["exam_questions"]:
        session_id = session_ids.get(str(item.get("session") or ""))
        text = str(item.get("text") or "").strip()
        if not text or (session_id, " ".join(text.split()).casefold()) in existing_questions:
            continue
        question_id = db.add_exam_question(
            name, text, session_id, int(item.get("number") or 0) or None,
            material_ids.get(str(item.get("material") or "")), str(item.get("solution") or ""),
            str(item.get("notes") or ""))
        group = str(item.get("group") or "")
        if group:
            if group in group_first:
                db.link_exam_questions(group_first[group], question_id)
            else:
                group_first[group] = question_id
        summary["exam_questions"] += 1

    existing_cards = {" ".join(str(c.get("front") or "").split()).casefold()
                      for c in db.list_flashcards(subject=name, limit=100000)}
    for item in manifest["flashcards"]:
        front, back = str(item.get("front") or "").strip(), str(item.get("back") or "").strip()
        if not front or not back or " ".join(front.split()).casefold() in existing_cards:
            continue
        db.add_flashcard(name, front, back, str(item.get("notes") or ""),
                         note_block_id=block_ids.get(str(item.get("block") or "")))
        existing_cards.add(" ".join(front.split()).casefold())
        summary["flashcards"] += 1
    progress(1.0, "Materia importata")
    return summary


def suggest_import_name(db, wanted: str) -> str:
    """Nome libero per «Importa come nuova materia»: ``Analisi (importata)``, ``(importata 2)``…"""
    base = f"{str(wanted or '').strip()} (importata)"
    if db.get_subject(base) is None:
        return base
    index = 2
    while db.get_subject(f"{wanted} (importata {index})") is not None:
        index += 1
    return f"{wanted} (importata {index})"
