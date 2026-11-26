"""Modifica diretta nell'anteprima di PDF e Slide: corrispondenza anteprima -> sorgente.

Durante il disegno dell'anteprima ogni pezzo di testo semplice (``run``)
viene cercato nel sorgente (Markdown o LaTeX), in ordine, a partire dalla fine
del pezzo precedente. Se lo si trova identico, il pezzo diventa modificabile:
un carattere scritto o cancellato in posizione ``k`` del pezzo corrisponde al
carattere ``start + k`` del sorgente. I pezzi che non hanno una copia esatta
nel sorgente (formule, numeri di sezione, testo trasformato) restano protetti.

Nessuna dipendenza dalla GUI: la vista Tk usa solo queste funzioni.
"""

from __future__ import annotations

import re

# Oltre questa distanza un "ritrovamento" e' quasi certamente un falso positivo.
MAX_GAP = 4000

_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "%": r"\%",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

_MD_LIST_RE = re.compile(r"^(\s*)([-*+•]|\d{1,3}[.)]|[a-z]\))(\s+)")
_TEX_ITEM_RE = re.compile(r"^(\s*)\\item\b\s*")


def escape_for_source(text: str, fmt: str, verbatim: bool = False) -> str:
    """Testo scritto nell'anteprima -> testo da inserire nel sorgente."""
    if verbatim:
        return text
    if fmt == "latex":
        return "".join(_LATEX_ESCAPES.get(char, char) for char in text)
    # Nel Markdown di Lectio l'unico escape e' il dollaro (le formule sono $...$).
    return text.replace("$", "\\$")


def newline_for_source(source: str, offset: int, fmt: str, verbatim: bool = False) -> str:
    """Testo da inserire nel sorgente quando si preme Invio nell'anteprima.

    In un blocco di codice e' un semplice a capo; in un elenco si apre una
    nuova voce dello stesso tipo; altrove un nuovo paragrafo.
    """
    if verbatim:
        return "\n"
    line_start = source.rfind("\n", 0, offset) + 1
    line = source[line_start:offset]
    if fmt == "latex":
        match = _TEX_ITEM_RE.match(line)
        if match:
            return "\n" + match.group(1) + "\\item "
        return "\n\n"
    match = _MD_LIST_RE.match(line)
    if match:
        indent, marker, gap = match.groups()
        number = re.match(r"(\d+)([.)])", marker)
        if number:
            marker = f"{int(number.group(1)) + 1}{number.group(2)}"
        elif re.fullmatch(r"[a-y]\)", marker):
            marker = f"{chr(ord(marker[0]) + 1)})"
        return "\n" + indent + marker + gap
    return "\n\n"


# Come un carattere visibile nell'anteprima puo' essere scritto nel sorgente.
_SOURCE_FORMS = {
    "latex": {char: _LATEX_ESCAPES[char] for char in "&%$#_{}"},
    "markdown": {"$": "\\$"},
}


def _pattern(display: str, forms: dict[str, str]) -> re.Pattern[str]:
    # Gli a capo del sorgente possono diventare spazi nell'anteprima (e viceversa);
    # i caratteri speciali possono comparire con il loro escape.
    parts = []
    for char in display:
        if char.isspace():
            parts.append(r"\s")
        elif char in forms:
            parts.append(f"(?:{re.escape(forms[char])}|{re.escape(char)})")
        else:
            parts.append(re.escape(char))
    return re.compile("".join(parts))


class PreviewSourceMap:
    """Pezzi dell'anteprima ritrovati nel sorgente: ``runs[id] = [start, end]``."""

    def __init__(self, source: str, fmt: str = "markdown"):
        self.source = source
        self.cursor = 0
        self.runs: dict[int, list[int]] = {}
        self.verbatim: set[int] = set()
        # Per i pezzi con escape: offset relativo nel sorgente di ogni posizione del pezzo.
        self.offsets: dict[int, list[int]] = {}
        self._forms = _SOURCE_FORMS.get(fmt, {})
        self._next = 0

    def locate(self, display: str, verbatim: bool = False) -> int | None:
        """Cerca il pezzo dopo quello precedente; restituisce l'id o None se non mappabile."""
        if not display:
            return None
        forms = {} if verbatim else self._forms
        match = _pattern(display, forms).search(self.source, self.cursor)
        if match is None or match.start() - self.cursor > MAX_GAP:
            return None
        run_id = self._next
        self._next += 1
        self.runs[run_id] = [match.start(), match.end()]
        if match.end() - match.start() != len(display):
            table, position = [0], match.start()
            for char in display:
                form = forms.get(char)
                position += len(form) if form and self.source.startswith(form, position) else 1
                table.append(position - match.start())
            self.offsets[run_id] = table
        if verbatim:
            self.verbatim.add(run_id)
        self.cursor = match.end()
        return run_id

    def offset(self, run_id: int, position: int) -> int | None:
        """Offset nel sorgente del carattere ``position`` del pezzo (0 = inizio, len = fine)."""
        span = self.runs.get(run_id)
        table = self.offsets.get(run_id)
        if span is None or position < 0:
            return None
        if table is not None:
            return span[0] + table[position] if position < len(table) else None
        if position > span[1] - span[0]:
            return None
        return span[0] + position

    def escaped(self, run_id: int | None) -> bool:
        """True se il pezzo contiene escape: dopo una modifica va ridisegnato subito."""
        return run_id is not None and run_id in self.offsets

    def find(self, offset: int) -> tuple[int, int] | None:
        """``(run_id, posizione nel pezzo)`` per un offset del sorgente, o None."""
        best: tuple[int, int] | None = None
        for run_id, (start, end) in self.runs.items():
            if start <= offset <= end:
                table = self.offsets.get(run_id)
                relative = offset - start
                position = relative if table is None else max(
                    index for index, value in enumerate(table) if value <= relative
                )
                if offset < end:
                    return run_id, position
                if best is None:
                    best = (run_id, position)
        return best

    def nearest(self, offset: int) -> tuple[int, int] | None:
        """Come :meth:`find`, ma per un offset fuori da ogni pezzo (marcatori, formule,
        righe vuote) restituisce il punto mappato piu' vicino: la fine del pezzo che
        lo precede o l'inizio di quello che lo segue, scegliendo il piu' vicino."""
        found = self.find(offset)
        if found is not None:
            return found
        before: tuple[int, int] | None = None  # (fine, run_id)
        after: tuple[int, int] | None = None  # (inizio, run_id)
        for run_id, (start, end) in self.runs.items():
            if end <= offset and (before is None or end > before[0]):
                before = (end, run_id)
            elif start > offset and (after is None or start < after[0]):
                after = (start, run_id)
        if after is not None and (before is None or after[0] - offset < offset - before[0]):
            return after[1], 0
        if before is not None:
            run_id = before[1]
            table = self.offsets.get(run_id)
            start, end = self.runs[run_id]
            return run_id, (len(table) - 1 if table is not None else end - start)
        return None

    def replace(self, start: int, end: int, text: str, owner: int | None = None) -> None:
        """Applica ``source[start:end] = text`` e aggiorna i pezzi.

        ``owner`` e' il pezzo che riceve il testo inserito (quello in cui
        l'utente sta scrivendo); gli altri pezzi si spostano o si accorciano.
        """
        self.source = self.source[:start] + text + self.source[end:]
        delta = len(text) - (end - start)
        after = start + len(text)
        if owner is not None:
            self.offsets.pop(owner, None)
        for run_id, span in self.runs.items():
            s0, s1 = span
            if run_id == owner:
                span[1] = s1 + delta if s1 >= end else after
            elif s0 >= end and not (s0 == start == end and s1 == s0):
                span[0], span[1] = s0 + delta, s1 + delta
            elif s1 <= start:
                continue
            else:
                # Pezzo cancellato in parte (o del tutto) dalla modifica.
                span[0] = s0 if s0 < start else after
                span[1] = s1 + delta if s1 > end else (start if s0 < start else after)
