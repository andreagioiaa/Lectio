"""Tema e helper tipografici per CustomTkinter.

Due temi: scuro (predefinito) e chiaro. Le viste leggono i colori da
``COLORS``; ``apply_palette`` ne sostituisce i valori e ``recolor_widget_tree``
aggiorna i widget gia' creati sostituendo ogni colore scuro con il
corrispondente chiaro (o viceversa), senza ricostruire l'interfaccia e quindi
senza perdere testo, selezioni o stato delle viste.
"""

from __future__ import annotations

import logging
import tkinter as tk
from dataclasses import fields
from typing import Callable, Iterable

import customtkinter as ctk

from ..config import COLORS, DARK_PALETTE, FONT_FAMILY, LIGHT_PALETTE, MONO_FONT_FAMILY
from .scrolling import install_smooth_scrolling


LOGGER = logging.getLogger(__name__)

THEME_DARK = "dark"
THEME_LIGHT = "light"
THEMES = (THEME_DARK, THEME_LIGHT)

# Colori scritti direttamente nelle viste (fuori dalla palette) e loro
# equivalente nel tema chiaro.
_LITERAL_PAIRS: dict[str, str] = {
    "#FFFFFF": "#273449",   # cursore degli interruttori al passaggio del mouse
    "#04130F": "#F7FFFC",   # testo sui pulsanti color accento
    "#991B1B": "#FCA5A5",   # pulsanti "pericolo" al passaggio del mouse
    "#CBD5E1": "#334155",   # titoli h2 dell'anteprima
    "#E2E8F0": "#1E293B",   # corsivo dell'anteprima
    "#F9A8D4": "#BE185D",   # codice in linea dell'anteprima
    "#111827": "#FFF7ED",   # testo evidenziato su ambra
    "#44320B": "#FEF3C7",   # sfondi tinti
    "#2F2455": "#EDE9FE",
    "#0C3348": "#E0F2FE",
}

_current = THEME_DARK
_color_dicts: list[dict] = []


def configure_theme() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    try:
        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)
    except Exception:
        pass
    install_smooth_scrolling()


def current_theme() -> str:
    return _current


def _palette_pairs() -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in fields(DARK_PALETTE):
        dark = getattr(DARK_PALETTE, item.name).upper()
        light = getattr(LIGHT_PALETTE, item.name).upper()
        pairs.setdefault(dark, light)
    return pairs


def color_map(source: str, target: str, literals_only: bool = False) -> dict[str, str]:
    """Mappa ``colore -> colore`` (esadecimale maiuscolo) dal tema ``source`` a ``target``."""
    if source == target:
        return {}
    pairs = {key.upper(): value.upper() for key, value in _LITERAL_PAIRS.items()}
    if not literals_only:
        pairs = {**_palette_pairs(), **pairs}
    if source == THEME_DARK:
        return pairs
    return {light: dark for dark, light in pairs.items()}


def themed(dark_color: str) -> str:
    """Colore scritto per il tema scuro, convertito per il tema attivo."""
    if _current == THEME_DARK:
        return dark_color
    return color_map(THEME_DARK, THEME_LIGHT).get(dark_color.upper(), dark_color)


def register_color_dict(values: dict) -> dict:
    """Dizionari di colori a livello di modulo da aggiornare al cambio di tema."""
    _color_dicts.append(values)
    if _current != THEME_DARK:
        _remap_dict(values, color_map(THEME_DARK, _current))
    return values


def _remap_dict(values: dict, mapping: dict[str, str]) -> None:
    for key, value in list(values.items()):
        if isinstance(value, str) and value.upper() in mapping:
            values[key] = mapping[value.upper()]


def apply_palette(theme: str) -> dict[str, str]:
    """Attiva il tema: aggiorna ``COLORS``, CustomTkinter e i dizionari registrati.

    Restituisce la mappa dei colori da passare a ``recolor_widget_tree``.
    """
    global _current
    theme = theme if theme in THEMES else THEME_DARK
    mapping = color_map(_current, theme)
    palette = LIGHT_PALETTE if theme == THEME_LIGHT else DARK_PALETTE
    for item in fields(palette):
        object.__setattr__(COLORS, item.name, getattr(palette, item.name))
    for values in _color_dicts:
        _remap_dict(values, mapping)
    _current = theme
    try:
        ctk.set_appearance_mode(theme)
    except Exception:
        LOGGER.exception("Cambio della modalita' di CustomTkinter non riuscito.")
    return mapping


# ---------------------------------------------------------------------------
# Ricolorazione dei widget esistenti
# ---------------------------------------------------------------------------
_CTK_COLOR_OPTIONS = (
    "fg_color", "bg_color", "text_color", "text_color_disabled", "border_color", "hover_color",
    "button_color", "button_hover_color", "progress_color", "selected_color", "selected_hover_color",
    "unselected_color", "unselected_hover_color", "dropdown_fg_color", "dropdown_hover_color",
    "dropdown_text_color", "scrollbar_button_color", "scrollbar_button_hover_color",
    "placeholder_text_color", "checkmark_color", "label_fg_color", "segmented_button_fg_color",
    "segmented_button_selected_color", "segmented_button_selected_hover_color",
    "segmented_button_unselected_color", "segmented_button_unselected_hover_color",
)
_TK_COLOR_OPTIONS = (
    "background", "foreground", "insertbackground", "selectbackground", "selectforeground",
    "highlightbackground", "highlightcolor", "activebackground", "activeforeground",
    "disabledforeground", "troughcolor", "inactiveselectbackground", "selectcolor",
)
_TAG_COLOR_OPTIONS = ("foreground", "background", "selectbackground", "selectforeground")
_CANVAS_COLOR_OPTIONS = ("fill", "outline", "activefill", "activeoutline")


def _mapped(value, mapping: dict[str, str]):
    if isinstance(value, str):
        return mapping.get(value.upper())
    if isinstance(value, (tuple, list)) and value and all(isinstance(item, str) for item in value):
        converted = [mapping.get(item.upper(), item) for item in value]
        if converted != list(value):
            return type(value)(converted)
    return None


def _plan_ctk(widget, mapping: dict[str, str]) -> list[Callable[[], None]]:
    changes = {}
    for option in _CTK_COLOR_OPTIONS:
        try:
            value = widget.cget(option)
        except Exception:
            continue
        new = _mapped(value, mapping)
        if new is not None:
            changes[option] = new
    if not changes:
        return []

    def apply() -> None:
        try:
            widget.configure(**changes)
        except Exception:
            for option, value in changes.items():
                try:
                    widget.configure(**{option: value})
                except Exception:
                    pass

    return [apply]


def _plan_tk(widget, mapping: dict[str, str]) -> list[Callable[[], None]]:
    actions: list[Callable[[], None]] = []
    try:
        keys = set(widget.keys())
    except Exception:
        keys = set()
    changes = {}
    for option in _TK_COLOR_OPTIONS:
        if option not in keys:
            continue
        try:
            new = _mapped(str(widget.cget(option)), mapping)
        except tk.TclError:
            continue
        if new is not None:
            changes[option] = new
    if changes:
        actions.append(lambda: widget.configure(**changes))
    if isinstance(widget, tk.Text):
        for tag in widget.tag_names():
            for option in _TAG_COLOR_OPTIONS:
                try:
                    new = _mapped(str(widget.tag_cget(tag, option)), mapping)
                except tk.TclError:
                    continue
                if new is not None:
                    actions.append(lambda tag=tag, option=option, new=new: widget.tag_configure(tag, **{option: new}))
    elif isinstance(widget, tk.Canvas):
        for item in widget.find_all():
            for option in _CANVAS_COLOR_OPTIONS:
                try:
                    new = _mapped(str(widget.itemcget(item, option)), mapping)
                except tk.TclError:
                    continue
                if new is not None:
                    actions.append(
                        lambda item=item, option=option, new=new: widget.itemconfigure(item, **{option: new})
                    )
    return actions


def _iter_tree(root) -> Iterable:
    stack = [root]
    while stack:
        widget = stack.pop()
        yield widget
        try:
            children = widget.winfo_children()
        except tk.TclError:
            continue
        stack.extend(reversed(children))


def recolor_widget_tree(root, mapping: dict[str, str]) -> int:
    """Sostituisce i colori di ``root`` e di tutti i suoi discendenti; restituisce i widget visitati.

    Prima si leggono *tutti* i colori e solo dopo si applicano le modifiche:
    configurare un widget CTk aggiorna subito i suoi widget interni, e alcuni
    colori del tema chiaro coincidono con colori diversi del tema scuro
    (es. lo sfondo chiaro dei campi e il testo scuro), quindi rileggerli dopo
    li convertirebbe una seconda volta.
    """
    if not mapping:
        return 0
    internal_canvas = getattr(ctk, "CTkCanvas", None)
    actions: list[Callable[[], None]] = []
    scrollables: list = []
    count = 0
    for widget in _iter_tree(root):
        count += 1
        try:
            if isinstance(widget, ctk.CTkScrollableFrame):
                # Lo sfondo dell'area interna deriva dal frame contenitore (ricolorato
                # a parte) e si aggiorna solo riconfigurando fg_color: si fa alla fine.
                scrollables.append(widget)
            elif isinstance(widget, (ctk.CTkBaseClass, ctk.CTk, ctk.CTkToplevel)):
                actions.extend(_plan_ctk(widget, mapping))
            elif internal_canvas is not None and isinstance(widget, internal_canvas):
                continue  # canvas interni di CustomTkinter: li ridisegna il widget proprietario
            else:
                actions.extend(_plan_tk(widget, mapping))
        except Exception:
            LOGGER.debug("Lettura colori non riuscita per %r", widget, exc_info=True)
    for action in actions:
        try:
            action()
        except Exception:
            LOGGER.debug("Ricolorazione non riuscita", exc_info=True)
    for widget in scrollables:
        try:
            widget.configure(fg_color=widget._parent_frame.cget("fg_color"))
        except Exception:
            LOGGER.debug("Ricolorazione non riuscita per %r", widget, exc_info=True)
    return count


def font(size: int = 13, weight: str = "normal", family: str = FONT_FAMILY) -> ctk.CTkFont:
    return ctk.CTkFont(family=family, size=size, weight=weight)


def mono(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=MONO_FONT_FAMILY, size=size, weight=weight)


def phase_color(phase: str) -> str:
    if phase == "short_break":
        return COLORS.blue
    if phase == "long_break":
        return COLORS.purple
    return COLORS.accent


def status_color(status: str) -> str:
    return {
        "running": COLORS.accent,
        "paused": COLORS.amber,
        "idle": COLORS.text_muted,
    }.get(status, COLORS.text_muted)
