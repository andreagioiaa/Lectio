"""Domande d'esame: divisione di un tema in domande, domande simili, export Markdown.

Il tema d'esame viene trascritto con la pipeline di PDF e Slide; qui il testo
viene diviso in domande riconoscendo gli inizi piu' comuni:

* ``Esercizio 2``, ``Domanda 3``, ``Quesito 1``, ``Problema 4`` (anche come titolo ``#``);
* elenchi numerati ``1.`` / ``2)`` a inizio riga, solo se in sequenza.

Il testo prima della prima domanda (intestazione del compito) viene scartato.
La proposta si corregge nella schermata di revisione, dove ogni domanda e'
separata da una riga ``=== DOMANDA ===``.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Iterable

from .pdf_tools.document import strip_slide_markers
from .subjects import format_exam_date

QUESTION_SEPARATOR = "=== DOMANDA ==="
EXAM_EXPORT_NAME = "Domande_esame.md"
NO_SESSION = "Senza appello"
NO_TOPIC = "Non indicato"

_KEYWORD_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*(?:esercizio|domanda|quesito|problema|exercise|question)\s*(?:n\.?\s*)?(\d{1,3})\b"
    r"\s*(?:\*\*)?\s*[.:)\-–]?\s*(?:\*\*)?\s*(.*)$",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"^(\d{1,2})\s*[.)]\s+(\S.*)$")
_SEPARATOR_RE = re.compile(r"^\s*=+\s*DOMANDA\s*=+\s*$", re.IGNORECASE)


def _clean_lines(text: str) -> list[str]:
    stripped, _removed = strip_slide_markers(str(text or ""))
    return stripped.replace("\r\n", "\n").split("\n")


def split_exam_text(text: str) -> list[dict[str, Any]]:
    """Propone la divisione del tema in domande: ``[{"number", "text"}]``."""
    lines = _clean_lines(text)
    keyword_hits = [index for index, line in enumerate(lines) if _KEYWORD_RE.match(line)]
    starts: list[tuple[int, int, str]] = []
    if len(keyword_hits) >= 1:
        for index in keyword_hits:
            match = _KEYWORD_RE.match(lines[index])
            starts.append((index, int(match.group(1)), match.group(2).strip()))
    else:
        expected = 1
        for index, line in enumerate(lines):
            match = _NUMBER_RE.match(line)  # solo a inizio riga (niente rientro): elenchi interni esclusi
            if match and int(match.group(1)) == expected:
                starts.append((index, expected, match.group(2).strip()))
                expected += 1
    if not starts:
        body = "\n".join(lines).strip()
        return [{"number": 1, "text": body}] if body else []
    questions: list[dict[str, Any]] = []
    for position, (index, number, first) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        body = [first] if first else []
        body.extend(lines[index + 1:end])
        content = re.sub(r"\n{3,}", "\n\n", "\n".join(body)).strip()
        if content:
            questions.append({"number": number, "text": content})
    return questions


def questions_to_review_text(questions: Iterable[dict[str, Any]]) -> str:
    """Testo della schermata di revisione: domande separate da ``=== DOMANDA ===``."""
    return "\n\n".join(f"{QUESTION_SEPARATOR}\n{str(q['text']).strip()}" for q in questions).strip() + "\n"


def review_text_to_questions(text: str) -> list[dict[str, Any]]:
    """Inverso di ``questions_to_review_text``: numera le domande nell'ordine."""
    chunks: list[list[str]] = []
    current: list[str] | None = None
    for line in str(text or "").replace("\r\n", "\n").split("\n"):
        if _SEPARATOR_RE.match(line):
            current = []
            chunks.append(current)
            continue
        if current is None:  # testo prima del primo separatore: prima domanda
            current = []
            chunks.append(current)
        current.append(line)
    out: list[dict[str, Any]] = []
    for chunk in chunks:
        body = "\n".join(chunk).strip()
        if body:
            out.append({"number": len(out) + 1, "text": body})
    return out


def split_pasted_questions(text: str) -> list[dict[str, Any]]:
    """Piu' domande incollate insieme.

    Con almeno una riga ``=== DOMANDA ===`` si divide come nella revisione dei temi;
    altrimenti ogni blocco separato da una riga vuota e' una domanda.
    """
    clean = str(text or "").replace("\r\n", "\n")
    if any(_SEPARATOR_RE.match(line) for line in clean.split("\n")):
        return review_text_to_questions(clean)
    out: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", clean):
        body = block.strip()
        if body:
            out.append({"number": len(out) + 1, "text": body})
    return out


_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](20\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(20\d{2})[\-_.](\d{1,2})[\-_.](\d{1,2})(?!\d)"),
)


def guess_exam_date(*texts: str) -> str:
    """Prima data plausibile (``GG/MM/AAAA``) trovata nel nome file o nel testo del tema."""
    for text in texts:
        for index, pattern in enumerate(_DATE_PATTERNS):
            for match in pattern.finditer(str(text or "")):
                if index == 0:
                    day, month, year = (int(group) for group in match.groups())
                else:
                    year, month, day = (int(group) for group in match.groups())
                if 1 <= month <= 12 and 1 <= day <= 31:
                    return f"{day:02d}/{month:02d}/{year}"
    return ""


# ---------------------------------------------------------------------------
# Domande simili (ricorrenze tra appelli)
# ---------------------------------------------------------------------------
def normalize_question(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    value = re.sub(r"\$[^$]*\$", " formula ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def similarity(a: str, b: str) -> float:
    """Somiglianza 0-1 calcolata sulle parole (sulle lettere sarebbe troppo permissiva)."""
    left, right = normalize_question(a).split(), normalize_question(b).split()
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def similar_questions(question: dict[str, Any], candidates: Iterable[dict[str, Any]], threshold: float = 0.45,
                      limit: int = 8) -> list[tuple[float, dict[str, Any]]]:
    """Domande di altri appelli piu' simili a ``question`` (punteggio 0-1, decrescente)."""
    scored: list[tuple[float, dict[str, Any]]] = []
    for other in candidates:
        if int(other["id"]) == int(question["id"]):
            continue
        if question.get("session_id") is not None and other.get("session_id") == question.get("session_id"):
            continue
        score = similarity(question.get("text") or "", other.get("text") or "")
        if score >= threshold:
            scored.append((score, other))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:limit]


# ---------------------------------------------------------------------------
# Export Markdown
# ---------------------------------------------------------------------------
def session_label(session: dict[str, Any] | None) -> str:
    if not session:
        return NO_SESSION
    date = format_exam_date(session.get("exam_date")) if session.get("exam_date") else ""
    title = str(session.get("title") or session.get("session_title") or "").strip()
    if date and title:
        return f"Appello {date} - {title}"
    if date:
        return f"Appello {date}"
    return title or "Appello senza data"


def exam_markdown(subject: str, sessions: list[dict[str, Any]], questions: list[dict[str, Any]],
                  include_solutions: bool = True) -> str:
    """``Domande_esame.md``: domande divise per appello, con argomento, ricorrenze e soluzioni."""
    lines = [f"# Domande d'esame - {str(subject or '').strip()}", ""]
    total = len(questions)
    recurring = sum(1 for q in questions if int(q.get("occurrences") or 1) > 1)
    lines.append(f"{total} domande in {len(sessions)} appelli" + (f", {recurring} ricorrenti." if recurring else "."))
    by_session: dict[Any, list[dict[str, Any]]] = {}
    for question in questions:
        by_session.setdefault(question.get("session_id"), []).append(question)
    ordered: list[tuple[Any, dict[str, Any] | None]] = [(s["id"], s) for s in sessions]
    if None in by_session:
        ordered.append((None, None))
    for session_id, session in ordered:
        items = sorted(by_session.get(session_id, []), key=lambda q: (int(q.get("number") or 0), int(q["id"])))
        if not items:
            continue
        lines.extend(["", f"## {session_label(session)}", ""])
        for question in items:
            tags = [str(question.get("topic") or NO_TOPIC)]
            occurrences = int(question.get("occurrences") or 1)
            if occurrences > 1:
                tags.append(f"uscita {occurrences} volte")
            text = str(question.get("text") or "").strip()
            first, _sep, rest = text.partition("\n")
            lines.append(f"{int(question.get('number') or 0)}. {first} *({', '.join(tags)})*")
            for extra in rest.split("\n") if rest else []:
                lines.append(f"   {extra}" if extra.strip() else "")
            if include_solutions and str(question.get("solution") or "").strip():
                lines.append("")
                lines.append("   **Soluzione:**")
                for extra in str(question["solution"]).strip().split("\n"):
                    lines.append(f"   {extra}" if extra.strip() else "")
            if str(question.get("notes") or "").strip():
                lines.append(f"   > Note: {str(question['notes']).strip()}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
