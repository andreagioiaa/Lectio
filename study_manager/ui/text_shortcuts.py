"""Scorciatoie di modifica del testo come in Windows, per tutti i campi di testo dell'app.

Tk su Windows non le ha: Ctrl+Canc e Ctrl+Backspace cancellano un solo
carattere invece di una parola, e Ctrl+Shift+Z non ripete l'annullamento. Le
associazioni sono di classe (``Text`` ed ``Entry``), quindi valgono anche per
CTkTextbox e CTkEntry, che usano quei widget al loro interno.
"""

from __future__ import annotations

import tkinter as tk

_INSTALLED_ON: set[str] = set()


def _text_delete_word(event: tk.Event, forward: bool) -> str:
    widget = event.widget
    try:
        if widget.tag_ranges("sel"):
            widget.delete("sel.first", "sel.last")
            return "break"
        if forward:
            # Stessa destinazione di Ctrl+Destra: inizio della parola successiva.
            end = widget.tk.call("tk::TextNextWord", widget._w, "insert")
            widget.delete("insert", end)
        else:
            start = widget.tk.call("tk::TextPrevPos", widget._w, "insert", "tcl_startOfPreviousWord")
            widget.delete(start, "insert")
        widget.see("insert")
    except tk.TclError:
        return ""
    return "break"


def _text_redo(event: tk.Event) -> str:
    try:
        event.widget.edit_redo()
    except tk.TclError:
        pass
    return "break"


def _entry_delete_word(event: tk.Event, forward: bool) -> str:
    widget = event.widget
    try:
        if widget.selection_present():
            widget.delete("sel.first", "sel.last")
            return "break"
        text = widget.get()
        cursor = widget.index("insert")
        if forward:
            end = cursor
            while end < len(text) and not text[end].isspace():
                end += 1
            while end < len(text) and text[end].isspace():
                end += 1
            widget.delete(cursor, end)
        else:
            start = cursor
            while start > 0 and text[start - 1].isspace():
                start -= 1
            while start > 0 and not text[start - 1].isspace():
                start -= 1
            widget.delete(start, cursor)
    except tk.TclError:
        return ""
    return "break"


def install_text_shortcuts(widget: tk.Misc) -> None:
    """Registra le scorciatoie una volta per interprete Tk (chiamate ripetute sono innocue)."""
    key = str(widget.tk)
    if key in _INSTALLED_ON:
        return
    _INSTALLED_ON.add(key)
    widget.bind_class("Text", "<Control-Delete>", lambda e: _text_delete_word(e, True))
    widget.bind_class("Text", "<Control-BackSpace>", lambda e: _text_delete_word(e, False))
    for sequence in ("<Control-Shift-Z>", "<Control-Shift-z>"):
        widget.bind_class("Text", sequence, _text_redo)
    widget.bind_class("Entry", "<Control-Delete>", lambda e: _entry_delete_word(e, True))
    widget.bind_class("Entry", "<Control-BackSpace>", lambda e: _entry_delete_word(e, False))
