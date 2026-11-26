"""Finestra "Dubbi" della vista PDF e Slide.

Elenco delle domande che lo studente vuole risolvere da solo su un blocco di
slide estratto, prima di trasformarle in flashcard. Ogni dubbio e' un
dizionario ``{"text": str, "card": bool}`` salvato nel ``DocState`` (bozza e
capitolo collegato). Tasto destro su un dubbio: crea la flashcard, modifica,
elimina.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import simpledialog
from typing import TYPE_CHECKING

import customtkinter as ctk

from ..config import COLORS
from .theme import font

if TYPE_CHECKING:  # pragma: no cover
    from .pdf_view import DocState, PdfView

DONE_MARK = "✓ "


class DoubtsDialog(ctk.CTkToplevel):
    def __init__(self, view: "PdfView", doc: "DocState", initial: str = "",
                 heading: str = "Dubbi su questo blocco di slide"):
        super().__init__(view)
        self.view = view
        self.doc = doc
        self.title(f"Dubbi · {doc.title or doc.path.stem}")
        self.configure(fg_color=COLORS.bg)
        self.minsize(460, 380)
        self.geometry("520x460")
        self.transient(view.winfo_toplevel())
        self._build(heading)
        if initial:
            self.entry.insert(0, initial)
        self.refresh()
        self.after(60, self.entry.focus_set)
        self.bind("<Escape>", lambda _e: self.destroy())

    def _build(self, heading: str) -> None:
        body = ctk.CTkFrame(self, fg_color=COLORS.surface, corner_radius=14, border_width=1,
                            border_color=COLORS.border)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(body, text=heading, font=font(12, "bold"),
                     text_color=COLORS.text, anchor="w").grid(row=0, column=0, columnspan=2, sticky="ew",
                                                              padx=16, pady=(14, 2))
        ctk.CTkLabel(
            body, text="Annota le domande da risolvere da solo. Tasto destro su un dubbio per crearne la flashcard.",
            font=font(10), text_color=COLORS.text_faint, anchor="w", justify="left", wraplength=440,
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))

        self.entry = ctk.CTkEntry(body, placeholder_text="Scrivi un dubbio e premi Invio…", font=font(12),
                                  fg_color=COLORS.surface_alt, border_color=COLORS.border, text_color=COLORS.text)
        self.entry.grid(row=2, column=0, sticky="ew", padx=(16, 6))
        self.entry.bind("<Return>", lambda _e: self.add())
        ctk.CTkButton(
            body, text="Aggiungi", width=90, height=30, font=font(11, "bold"), corner_radius=9,
            fg_color=COLORS.accent, hover_color=COLORS.accent_hover, text_color="#04130F", command=self.add,
        ).grid(row=2, column=1, sticky="e", padx=(0, 16))

        self.listbox = tk.Listbox(
            body, activestyle="none", font=font(12), bg=COLORS.surface_alt, fg=COLORS.text,
            selectbackground=COLORS.accent_dark, selectforeground=COLORS.text, highlightthickness=0,
            borderwidth=0, relief=tk.FLAT,
        )
        self.listbox.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=16, pady=(10, 6))
        self.listbox.bind("<Button-3>", self._open_menu)
        self.listbox.bind("<Double-Button-1>", lambda _e: self.edit())
        self.listbox.bind("<Delete>", lambda _e: self.delete())

        self.count_label = ctk.CTkLabel(body, text="", font=font(10), text_color=COLORS.text_muted, anchor="w")
        self.count_label.grid(row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 12))

        self.menu = tk.Menu(
            self.listbox, tearoff=0, bg=COLORS.surface_alt, fg=COLORS.text, activebackground=COLORS.accent_dark,
            activeforeground=COLORS.text, disabledforeground=COLORS.text_faint, bd=0, relief=tk.FLAT,
        )
        self.menu.add_command(label="＋  Crea flashcard da questa domanda", command=self.make_flashcard)
        self.menu.add_command(label="Modifica…", command=self.edit)
        self.menu.add_separator()
        self.menu.add_command(label="Elimina", command=self.delete)

    # ------------------------------------------------------------------ elenco
    def refresh(self) -> None:
        self.listbox.delete(0, "end")
        for doubt in self.doc.doubts:
            self.listbox.insert("end", (DONE_MARK if doubt.get("card") else "•  ") + doubt["text"])
            if doubt.get("card"):
                self.listbox.itemconfigure("end", fg=COLORS.text_faint)
        open_count = sum(1 for doubt in self.doc.doubts if not doubt.get("card"))
        total = len(self.doc.doubts)
        self.count_label.configure(
            text="Nessun dubbio annotato." if not total
            else f"{open_count} da risolvere · {total - open_count} gia' trasformati in flashcard"
        )

    def _selected(self) -> int | None:
        selection = self.listbox.curselection()
        return int(selection[0]) if selection else None

    def _open_menu(self, event) -> None:
        index = self.listbox.nearest(event.y)
        if index < 0 or index >= len(self.doc.doubts):
            return
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        self.menu.entryconfigure(0, state="disabled" if self.doc.doubts[index].get("card") else "normal")
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _changed(self) -> None:
        self.refresh()
        self.view.doubts_changed(self.doc)

    # ------------------------------------------------------------------ azioni
    def add(self) -> None:
        text = " ".join(self.entry.get().split())
        if not text:
            return
        self.doc.doubts.append({"text": text, "card": False})
        self.entry.delete(0, "end")
        self._changed()
        self.listbox.see("end")

    def edit(self) -> None:
        index = self._selected()
        if index is None:
            return
        value = simpledialog.askstring("Dubbi", "Modifica il dubbio:", initialvalue=self.doc.doubts[index]["text"],
                                       parent=self)
        text = " ".join((value or "").split())
        if text:
            self.doc.doubts[index]["text"] = text
            self._changed()

    def delete(self) -> None:
        index = self._selected()
        if index is None:
            return
        del self.doc.doubts[index]
        self._changed()

    def make_flashcard(self) -> None:
        index = self._selected()
        if index is None:
            return
        doubt = self.doc.doubts[index]

        def created(_card_id: int) -> None:
            doubt["card"] = True
            if self.winfo_exists():
                self._changed()
            else:
                self.view.doubts_changed(self.doc)

        self.view.create_chapter_flashcard(front=doubt["text"], doc=self.doc, on_created=created)
