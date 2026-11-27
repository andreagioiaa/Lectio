"""Metadati delle materie, CFU e countdown per gli esami.

In Italia un CFU corrisponde a 25 ore di impegno complessivo per lo studente
(D.M. 270/2004 e, in precedenza, D.M. 509/1999). Il monte ore mostrato
dall'applicazione usa quindi sempre ``CFU x 25`` e non distingue tra lezioni
frontali e studio individuale.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any

from ..utils import now_local


CFU_HOURS = 25
MAX_CFU = 999


def cfu_to_hours(cfu: int | float) -> int:
    """Converte i CFU nel monte ore complessivo previsto dalla normativa."""
    try:
        value = float(cfu)
    except (TypeError, ValueError):
        value = 0.0
    return max(0, int(round(value))) * CFU_HOURS


def format_cfu(cfu: int | float) -> str:
    try:
        value = max(0, int(round(float(cfu))))
    except (TypeError, ValueError):
        value = 0
    return f"{value} CFU" if value else "CFU non indicati"


def format_hours_planned(cfu: int | float) -> str:
    hours = cfu_to_hours(cfu)
    return f"{hours} ore" if hours else "Ore non calcolabili"


def parse_exam_date(value: str | None) -> str:
    """Normalizza una data d'esame in formato ISO ``YYYY-MM-DD``.

    Accetta i formati piu' comuni usati in Italia. Una stringa vuota indica
    "data non ancora definita" ed e' un valore valido.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError("Data esame non valida: usa il formato GG/MM/AAAA.")


def format_exam_date(value: str | None) -> str:
    normalized = parse_exam_date(value)
    if not normalized:
        return ""
    return date.fromisoformat(normalized).strftime("%d/%m/%Y")


def exam_countdown(
    exam_date: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Restituisce lo stato del countdown per la data d'esame.

    Il countdown e' calcolato fino alla mezzanotte che apre il giorno
    dell'esame. Durante il giorno dell'esame viene mostrato ``Esame oggi``;
    dopo la data viene indicato da quanti giorni e' passata.
    """
    normalized = parse_exam_date(exam_date)
    if not normalized:
        return {
            "defined": False,
            "status": "undefined",
            "label": "Data non ancora definita",
            "short_label": "Da definire",
            "date_label": "",
            "days": None,
            "hours": None,
            "minutes": None,
        }
    exam = date.fromisoformat(normalized)
    moment = (now or now_local()).replace(microsecond=0)
    days = (exam - moment.date()).days
    date_label = exam.strftime("%d/%m/%Y")

    if days < 0:
        elapsed = abs(days)
        label = (
            "Data superata"
            if elapsed == 0
            else f"Data superata da {elapsed} giorn{'o' if elapsed == 1 else 'i'}"
        )
        return {
            "defined": True,
            "status": "past",
            "label": label,
            "short_label": "Superata",
            "date_label": date_label,
            "days": days,
            "hours": 0,
            "minutes": 0,
        }

    if days == 0:
        return {
            "defined": True,
            "status": "today",
            "label": "Esame oggi",
            "short_label": "Oggi",
            "date_label": date_label,
            "days": 0,
            "hours": 0,
            "minutes": 0,
        }

    target = datetime.combine(exam, time.min)
    remaining = target - moment
    hours = remaining.seconds // 3600
    minutes = (remaining.seconds % 3600) // 60
    if days == 1:
        label = "Domani"
        short_label = "Domani"
    else:
        label = f"Mancano {days} giorni"
        short_label = f"Tra {days} g"
    if hours and days <= 7:
        label += f" e {hours} h"
    elif minutes and days <= 2:
        label += f" e {minutes} min"
    return {
        "defined": True,
        "status": "upcoming",
        "label": label,
        "short_label": short_label,
        "date_label": date_label,
        "days": days,
        "hours": hours,
        "minutes": minutes,
    }


# ---------------------------------------------------------------------------
# Anno accademico dei materiali
# ---------------------------------------------------------------------------
# L'anno accademico italiano inizia a settembre/ottobre: da settembre in poi
# si e' gia' nel nuovo anno (es. 15/09/2025 -> 2025/26).
ACADEMIC_YEAR_START_MONTH = 9
ACADEMIC_YEAR_UNKNOWN = "Non indicato"


def academic_year_for(day: date | datetime | None = None) -> str:
    """Anno accademico che contiene ``day`` (default: oggi), nel formato ``2025/26``."""
    current = day or now_local()
    start = current.year if current.month >= ACADEMIC_YEAR_START_MONTH else current.year - 1
    return f"{start}/{(start + 1) % 100:02d}"


def normalize_academic_year(value: Any) -> str:
    """``2025/26``, ``2025-2026``, ``2025/2026``, ``25/26`` -> ``2025/26``; vuoto se non valido."""
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.fullmatch(r"(\d{2}|\d{4})\s*[/\-–]\s*(\d{2}|\d{4})", text)
    if not match:
        return ""
    first = int(match.group(1))
    if first < 100:
        first += 2000
    second = int(match.group(2)) % 100
    if second != (first + 1) % 100 or not 1950 <= first <= 2200:
        return ""
    return f"{first}/{second:02d}"


def academic_year_choices(existing: Any = (), around: date | datetime | None = None, back: int = 6) -> list[str]:
    """Anni proposti nel menu: l'anno corrente, i precedenti e quelli gia' usati."""
    current = academic_year_for(around)
    start = int(current[:4])
    values = {f"{year}/{(year + 1) % 100:02d}" for year in range(start - back, start + 2)}
    values.update(year for year in (normalize_academic_year(item) for item in existing) if year)
    return sorted(values, reverse=True)


def academic_year_folder(value: Any) -> str:
    """Nome di cartella per un anno (``2025/26`` -> ``2025-26``); vuoto se non indicato."""
    year = normalize_academic_year(value)
    return year.replace("/", "-") if year else ""
