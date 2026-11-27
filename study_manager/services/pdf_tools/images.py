"""Immagini estratte dai PDF: nomi, cartella dati e risoluzione dei riferimenti.

Nel sorgente un'immagine e' una riga ``![memoriavirtuale_imm3](memoriavirtuale-1a2b3c/memoriavirtuale_imm3.png)``:
il percorso e' relativo a ``<cartella dati>/pdf_images``, cosi' il testo resta
leggibile e la bozza continua a funzionare. La sottocartella ha un breve hash
del percorso del PDF: due capitoli con lo stesso nome in corsi diversi non si
sovrascrivono.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

from ...paths import shared_data_dir


def images_root() -> Path:
    # Nella cartella condivisa (se sincronizzata): gli appunti si aprono con le immagini su ogni PC.
    return shared_data_dir() / "pdf_images"


def chapter_slug(stem: str) -> str:
    """``09. Memoria Virtuale`` -> ``memoriavirtuale``."""
    text = re.sub(r"^[\d\s._-]+", "", stem) or stem
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", text.lower()) or "documento"


def image_folder(source: Path) -> str:
    digest = hashlib.sha1(str(Path(source).resolve()).lower().encode("utf-8")).hexdigest()[:6]
    return f"{chapter_slug(Path(source).stem)}-{digest}"


def image_name(source: Path, number: int) -> str:
    return f"{chapter_slug(Path(source).stem)}_imm{number}"


def image_markdown(ref: str) -> str:
    return f"![{Path(ref).stem}]({ref})"


def resolve_image(ref: str) -> Path | None:
    """Percorso del file di un riferimento (relativo alla cartella immagini o assoluto)."""
    path = Path(ref)
    if not path.is_absolute():
        path = images_root() / ref
    return path if path.is_file() else None
