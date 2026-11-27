"""Sezione «Esame» della pagina materia: appelli, domande d'esame, ricorrenze ed export.

- ``ExamSection``: domande divise per appello, filtri, export ``Domande_esame.md``.
- ``ExamSessionDialog``: crea/modifica un appello (data, titolo, note).
- ``ExamQuestionDialog``: crea/modifica una domanda (appello, numero, testo, argomento, soluzione).
- ``ExamReviewDialog``: revisione della divisione in domande di un tema PDF.
- ``SimilarQuestionsDialog``: collega la domanda alle stesse domande di altri appelli.
"""

from __future__ import annotations

import logging
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from typing import Any, Callable

import customtkinter as ctk

from ..config import COLORS
from ..services.course_summary import convert_pdf_source, slide_materials
from ..services.exam_questions import (
    EXAM_EXPORT_NAME,
    NO_SESSION,
    NO_TOPIC,
    QUESTION_SEPARATOR,
    exam_markdown,
    guess_exam_date,
    questions_to_review_text,
    review_text_to_questions,
    session_label,
    similar_questions,
    split_exam_text,
    split_pasted_questions,
)
from ..services.subject_folders import KIND_EXAMS
from ..services.subjects import format_exam_date, parse_exam_date
from ..utils import atomic_write_text
from .subject_content import _SectionBase
from .summary_view import ProgressDialog, _Modal, _plural, run_in_background
from .theme import font

LOGGER = logging.getLogger(__name__)
ALL_SESSIONS = "Tutti gli appelli"
ALL_TOPICS = "Tutti gli argomenti"
NO_SESSION_KEY = "__none__"


def _text_box(parent, height: int = 110) -> ctk.CTkTextbox:
    return ctk.CTkTextbox(parent, height=height, font=font(12), fg_color=COLORS.surface_alt,
                          border_color=COLORS.border, border_width=1, text_color=COLORS.text, wrap="word")


def _combo(parent, values: list[str], width: int = 0, command=None) -> ctk.CTkComboBox:
    options = dict(values=values or [""], height=34, font=font(12), dropdown_font=font(12),
                   fg_color=COLORS.surface_alt, border_color=COLORS.border, button_color=COLORS.surface_hover,
                   button_hover_color=COLORS.border, dropdown_fg_color=COLORS.surface,
                   dropdown_hover_color=COLORS.surface_hover, text_color=COLORS.text, corner_radius=9,
                   state="readonly", command=command)
    if width:
        options["width"] = width
    return ctk.CTkComboBox(parent, **options)


def _entry(parent, variable: ctk.StringVar, width: int = 0, placeholder: str = "") -> ctk.CTkEntry:
    options = dict(textvariable=variable, height=34, font=font(12), fg_color=COLORS.surface_alt,
                   border_color=COLORS.border, text_color=COLORS.text, placeholder_text=placeholder)
    if width:
        options["width"] = width
    return ctk.CTkEntry(parent, **options)


def _open_file(widget, path: str) -> None:
    if not path or not Path(path).is_file():
        messagebox.showerror("File non trovato", f"Il file non esiste piu':\n{path}", parent=widget.winfo_toplevel())
        return
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except (OSError, AttributeError) as exc:
        messagebox.showerror("Impossibile aprire il file", str(exc), parent=widget.winfo_toplevel())


# ---------------------------------------------------------------------------
# Sezione nella pagina materia
# ---------------------------------------------------------------------------
class ExamSection(_SectionBase):
    """Domande d'esame della materia divise per appello."""

    def __init__(self, master, app, subject: str, sessions: list[dict[str, Any]],
                 questions: list[dict[str, Any]], on_change=None):
        super().__init__(master, app, subject, "ESAME · DOMANDE D'ESAME")
        self.on_change = on_change
        self.sessions = sessions
        self.questions = questions
        self.topics = [(int(row["id"]), str(row.get("title") or "")) for row in
                       slide_materials(self.db.list_materials_for_subject(subject))]
        self.button(self.header_actions, "Cartella", 80,
                    lambda: self.app.open_subject_folder(self.subject, KIND_EXAMS)).pack(side="left", padx=(0, 6))
        self.button(self.header_actions, "+  Appello", 92, self.add_session).pack(side="left", padx=(0, 6))
        self.button(self.header_actions, "+  Domanda", 96, lambda: self.add_question(None)).pack(
            side="left", padx=(0, 6))
        self.button(self.header_actions, "+  Piu' domande", 118, lambda: self.add_questions(None)).pack(
            side="left", padx=(0, 6))
        self.import_button = self.button(self.header_actions, "Importa tema PDF", 130, self.import_pdf, primary=True)
        self.import_button.pack(side="left")

        recurring = sum(1 for q in questions if int(q.get("occurrences") or 1) > 1)
        self.count_label.configure(
            text=(f"{_plural(len(questions), 'domanda', 'domande')} · {_plural(len(sessions), 'appello', 'appelli')}"
                  + (f" · {recurring} ricorrenti" if recurring else ""))
            if questions or sessions else "Nessun appello registrato"
        )
        if not questions and not sessions:
            self.empty("Registra gli appelli e le loro domande: «Importa tema PDF» trascrive il testo del "
                       "compito (anche piu' PDF insieme) e propone la divisione in domande; «+ Domanda» o "
                       "«+ Piu' domande» per quelle raccolte a voce. "
                       "Ogni domanda si collega a una slide (argomento) e a quelle uguali di altri appelli.")
            return

        filters = ctk.CTkFrame(self.body, fg_color="transparent")
        filters.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self._session_keys: dict[str, Any] = {ALL_SESSIONS: None}
        for session in sessions:
            name = session_label(session)
            if name in self._session_keys:
                name = f"{name} (#{session['id']})"
            self._session_keys[name] = int(session["id"])
        if any(q.get("session_id") is None for q in questions):
            self._session_keys[NO_SESSION] = NO_SESSION_KEY
        self.session_filter = _combo(filters, list(self._session_keys), 190, lambda _v: self._render())
        self.session_filter.set(ALL_SESSIONS)
        self.session_filter.pack(side="left", padx=(0, 6))
        topic_names = [ALL_TOPICS, *sorted({q.get("topic") or NO_TOPIC for q in questions})]
        self.topic_filter = _combo(filters, topic_names, 190, lambda _v: self._render())
        self.topic_filter.set(ALL_TOPICS)
        self.topic_filter.pack(side="left", padx=(0, 6))
        self.recurring_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(filters, text="Solo ricorrenti", variable=self.recurring_var, font=font(10),
                        text_color=COLORS.text_muted, command=self._render, checkbox_width=18,
                        checkbox_height=18).pack(side="left", padx=(4, 8))
        self.search_var = ctk.StringVar()
        search = _entry(filters, self.search_var, 180, "Cerca nel testo…")
        search.pack(side="left", fill="x", expand=True)
        self.search_var.trace_add("write", lambda *_a: self._schedule_render())
        self._render_job: str | None = None

        self.list_frame = ctk.CTkFrame(self.body, fg_color="transparent")
        self.list_frame.grid(row=1, column=0, sticky="ew")
        self.list_frame.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(self.body, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ctk.CTkLabel(footer, text="Tutte le domande in un file per ripassare o come contesto per un LLM",
                     font=font(10), text_color=COLORS.text_faint, anchor="w").pack(side="left")
        self.button(footer, "Esporta tutte (.md)", 140, self.export_markdown).pack(side="right")
        self._render()

    # -------------------------------------------------------------- elenco
    def _schedule_render(self) -> None:
        if self._render_job is not None:
            try:
                self.after_cancel(self._render_job)
            except tk.TclError:
                pass
        self._render_job = self.after(250, self._render)

    def _visible(self) -> list[dict[str, Any]]:
        session_key = self._session_keys.get(self.session_filter.get())
        topic = self.topic_filter.get()
        needle = self.search_var.get().strip().casefold()
        out = []
        for question in self.questions:
            if session_key == NO_SESSION_KEY and question.get("session_id") is not None:
                continue
            if isinstance(session_key, int) and question.get("session_id") != session_key:
                continue
            if topic != ALL_TOPICS and (question.get("topic") or NO_TOPIC) != topic:
                continue
            if self.recurring_var.get() and int(question.get("occurrences") or 1) < 2:
                continue
            if needle and needle not in " ".join(str(question.get(key) or "") for key in
                                                 ("text", "solution", "notes")).casefold():
                continue
            out.append(question)
        return out

    def _render(self) -> None:
        self._render_job = None
        for child in self.list_frame.winfo_children():
            child.destroy()
        visible = self._visible()
        filtered = len(visible) != len(self.questions)
        groups: dict[Any, list[dict[str, Any]]] = {}
        for question in visible:
            groups.setdefault(question.get("session_id"), []).append(question)
        order: list[tuple[Any, dict[str, Any] | None]] = [(int(s["id"]), s) for s in self.sessions]
        order.append((None, None))
        row = 0
        for session_id, session in order:
            items = groups.get(session_id, [])
            if session_id is None and not items:
                continue
            if filtered and not items:
                continue
            self._session_header(session, len(items), row)
            row += 1
            for question in items:
                self._question_row(question, row)
                row += 1
        if row == 0:
            ctk.CTkLabel(self.list_frame, text="Nessuna domanda con questi filtri.", font=font(11),
                         text_color=COLORS.text_faint, anchor="w").grid(row=0, column=0, sticky="w")

    def _session_header(self, session: dict[str, Any] | None, count: int, row: int) -> None:
        frame = ctk.CTkFrame(self.list_frame, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", pady=(10 if row else 2, 2))
        frame.grid_columnconfigure(0, weight=1)
        text = f"{session_label(session)}  ·  {_plural(count, 'domanda', 'domande')}"
        ctk.CTkLabel(frame, text=text, font=font(12, "bold"), text_color=COLORS.text, anchor="w").grid(
            row=0, column=0, sticky="w")
        if session is not None and str(session.get("notes") or "").strip():
            ctk.CTkLabel(frame, text=str(session["notes"]).strip()[:160], font=font(9),
                         text_color=COLORS.text_faint, anchor="w").grid(row=1, column=0, sticky="w")
        actions = ctk.CTkFrame(frame, fg_color="transparent")
        actions.grid(row=0, column=1, rowspan=2, sticky="e")
        session_id = int(session["id"]) if session is not None else None
        if session is not None and session.get("pdf_path"):
            self.button(actions, "Apri PDF", 74, lambda: _open_file(self, str(session["pdf_path"]))).pack(
                side="left", padx=(0, 4))
        self.button(actions, "+ Domanda", 84, lambda: self.add_question(session_id)).pack(side="left", padx=(0, 4))
        self.button(actions, "+ Piu'", 60, lambda: self.add_questions(session_id)).pack(side="left", padx=(0, 4))
        if session is not None:
            self.button(actions, "Modifica", 70, lambda: self.edit_session(session)).pack(side="left", padx=(0, 4))
            self.button(actions, "Elimina", 64, lambda: self.delete_session(session)).pack(side="left")

    def _question_row(self, question: dict[str, Any], row: int) -> None:
        card = ctk.CTkFrame(self.list_frame, fg_color=COLORS.surface_alt, corner_radius=10, border_width=1,
                            border_color=COLORS.border)
        card.grid(row=row, column=0, sticky="ew", pady=3)
        card.grid_columnconfigure(0, weight=1)
        text = " ".join(str(question.get("text") or "").split())
        ctk.CTkLabel(card, text=f"{int(question.get('number') or 0)}. {text[:300]}{'…' if len(text) > 300 else ''}",
                     font=font(12, "bold"), text_color=COLORS.text, anchor="w", wraplength=560,
                     justify="left").grid(row=0, column=0, sticky="w", padx=14, pady=(9, 1))
        meta = [f"Argomento: {question.get('topic') or NO_TOPIC}"]
        occurrences = int(question.get("occurrences") or 1)
        if occurrences > 1:
            meta.append(f"uscita {occurrences} volte")
        if str(question.get("solution") or "").strip():
            meta.append("con soluzione")
        if question.get("flashcard_id"):
            meta.append("flashcard creata")
        ctk.CTkLabel(card, text="  ·  ".join(meta), font=font(9),
                     text_color=COLORS.amber if occurrences > 1 else COLORS.text_faint, anchor="w").grid(
            row=1, column=0, sticky="w", padx=14)
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=14, pady=(6, 9))
        self.button(actions, "Modifica", 70, lambda: self.edit_question(question)).pack(side="left", padx=(0, 4))
        self.button(actions, "Uscita anche in…", 120, lambda: self.similar(question)).pack(side="left", padx=4)
        self.button(actions, "Crea flashcard", 110, lambda: self.make_flashcard(question)).pack(side="left", padx=4)
        self.button(actions, "Elimina", 64, lambda: self.delete_question(question)).pack(side="right")

    # -------------------------------------------------------------- azioni
    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def add_session(self) -> None:
        result = ExamSessionDialog(self, "Nuovo appello").show()
        if result is None:
            return
        self.db.add_exam_session(self.subject, **result)
        self.app.toast("Appello aggiunto.")
        self._changed()

    def edit_session(self, session: dict[str, Any]) -> None:
        result = ExamSessionDialog(self, "Modifica appello", session).show()
        if result is None:
            return
        self.db.update_exam_session(int(session["id"]), pdf_path=str(session.get("pdf_path") or ""), **result)
        self._changed()

    def delete_session(self, session: dict[str, Any]) -> None:
        count = int(session.get("questions") or 0)
        if not messagebox.askyesno(
            "Elimina appello",
            f"Eliminare «{session_label(session)}» e le sue {count} domande?\n\n"
            "Le flashcard gia' create restano. Il PDF del tema nella cartella Esami non viene toccato.",
            icon="warning", parent=self.winfo_toplevel(),
        ):
            return
        self.db.delete_exam_session(int(session["id"]))
        self._changed()

    def add_question(self, session_id: int | None) -> None:
        result = ExamQuestionDialog(self, self.sessions, self.topics, session_id=session_id,
                                    next_number=lambda sid: self.db.next_exam_question_number(sid, self.subject)).show()
        if result is None:
            return
        self.db.add_exam_question(self.subject, **result)
        self._changed()

    def add_questions(self, session_id: int | None) -> None:
        """Piu' domande in un colpo solo: testo incollato, diviso con le righe separatrici."""
        result = ExamBulkQuestionsDialog(self, self.sessions, self.topics, session_id=session_id).show()
        if result is None:
            return
        start = self.db.next_exam_question_number(result["session_id"], self.subject)
        for offset, question in enumerate(result["questions"]):
            self.db.add_exam_question(self.subject, question["text"], result["session_id"], start + offset,
                                      material_id=result["material_id"])
        self.app.toast(f"Aggiunte {_plural(len(result['questions']), 'domanda', 'domande')}.")
        self._changed()

    def edit_question(self, question: dict[str, Any]) -> None:
        result = ExamQuestionDialog(self, self.sessions, self.topics, question=question).show()
        if result is None:
            return
        self.db.update_exam_question(int(question["id"]), **result)
        self._changed()

    def delete_question(self, question: dict[str, Any]) -> None:
        if not messagebox.askyesno("Elimina domanda", "Eliminare questa domanda d'esame?",
                                   parent=self.winfo_toplevel()):
            return
        self.db.delete_exam_question(int(question["id"]))
        self._changed()

    def similar(self, question: dict[str, Any]) -> None:
        if SimilarQuestionsDialog(self, self.db, question, self.questions).show():
            self._changed()

    def make_flashcard(self, question: dict[str, Any]) -> None:
        back = str(question.get("solution") or "").strip()
        if not back:
            back = simpledialog.askstring(
                "Crea flashcard", "La domanda non ha una soluzione. Scrivi la risposta per il retro della scheda:",
                parent=self.winfo_toplevel(),
            ) or ""
            if not back.strip():
                return
        session = next((s for s in self.sessions if s["id"] == question.get("session_id")), None)
        notes = f"Domanda d'esame · {session_label(session)}"
        if question.get("topic"):
            notes += f" · {question['topic']}"
        try:
            card_id = self.db.add_flashcard(self.subject, str(question["text"]), back, notes)
        except ValueError as exc:
            messagebox.showerror("Crea flashcard", str(exc), parent=self.winfo_toplevel())
            return
        updates: dict[str, Any] = {"flashcard_id": card_id}
        if not str(question.get("solution") or "").strip():
            updates["solution"] = back
        self.db.update_exam_question(int(question["id"]), **updates)
        try:
            self.app.sync_flashcard_export(self.subject)
        except Exception:
            LOGGER.debug("Export flashcard non aggiornato.", exc_info=True)
        self.app.toast("Flashcard creata dalla domanda d'esame.")
        self._changed()

    def export_markdown(self) -> None:
        folder = self.app.subject_folder(self.subject, KIND_EXAMS)
        if folder is None:
            return
        target = folder / EXAM_EXPORT_NAME
        try:
            atomic_write_text(target, exam_markdown(self.subject, self.sessions, self.questions))
        except OSError as exc:
            messagebox.showerror("Esporta domande", str(exc), parent=self.winfo_toplevel())
            return
        self.app.toast(f"Esportate {len(self.questions)} domande in {target.name}")
        _open_file(self, str(target))

    # -------------------------------------------------------------- import PDF
    def import_pdf(self) -> None:
        """Importa uno o piu' temi d'esame (PDF): uno alla volta, ognuno con la sua revisione."""
        paths = list(filedialog.askopenfilenames(parent=self.winfo_toplevel(),
                                                 title="Temi d'esame (PDF, anche piu' di uno)",
                                                 filetypes=[("PDF", "*.pdf")]) or [])
        if not paths:
            return
        folder = self.app.subject_folder(self.subject, KIND_EXAMS)
        if folder is None:
            return
        self.import_button.configure(state="disabled")
        self._import_queue(paths, 0, {"sessions": 0, "questions": 0, "errors": []})

    def _import_queue(self, paths: list[str], index: int, totals: dict[str, Any]) -> None:
        """Trascrive ``paths[index]``, mostra la revisione e passa al successivo."""
        total = len(paths)
        if index >= total:
            self._finish_import(totals, total)
            return
        position = f" ({index + 1} di {total})" if total > 1 else ""
        path = paths[index]
        try:
            copy = self.app.subject_folders.copy_material(path, self.subject, KIND_EXAMS)
        except OSError as exc:
            totals["errors"].append(f"{Path(path).name}: copia non riuscita ({exc})")
            self._import_queue(paths, index + 1, totals)
            return
        pdf_view = getattr(self.app, "views", {}).get("pdf")
        options = pdf_view.extract_options() if pdf_view is not None and hasattr(pdf_view, "extract_options") else None
        cancel = threading.Event()
        progress = ProgressDialog(self.app, f"Importa tema d'esame{position}", f"Trascrivo {copy.name}…",
                                  on_cancel=cancel.set)

        def report(fraction: float, message: str) -> None:
            self.app.post(lambda: progress.update_progress(fraction, message))

        def done(source_text: str) -> None:
            progress.close()
            questions = split_exam_text(source_text)
            result = ExamReviewDialog(self.app, copy.name, guess_exam_date(copy.stem, source_text[:2000]),
                                      questions, position=position).show()
            if result is not None:
                session_id = self.db.add_exam_session(self.subject, result["exam_date"], result["title"], str(copy))
                for question in result["questions"]:
                    self.db.add_exam_question(self.subject, question["text"], session_id, question["number"])
                totals["sessions"] += 1
                totals["questions"] += len(result["questions"])
            self._import_queue(paths, index + 1, totals)

        def failed(exc: BaseException) -> None:
            progress.close()
            if isinstance(exc, InterruptedError):
                # «Annulla» durante la trascrizione ferma anche i file successivi.
                self._finish_import(totals, total, cancelled=True)
                return
            totals["errors"].append(f"{copy.name}: trascrizione non riuscita ({exc})")
            self._import_queue(paths, index + 1, totals)

        run_in_background(self.app, lambda: convert_pdf_source(copy, report, cancel, options), done, failed)

    def _finish_import(self, totals: dict[str, Any], total: int, cancelled: bool = False) -> None:
        self._enable_import()
        if totals["errors"]:
            messagebox.showwarning("Importa temi PDF", "\n".join(totals["errors"][:10]), parent=self.app)
        if totals["sessions"]:
            text = (f"Appello importato con {_plural(totals['questions'], 'domanda', 'domande')}."
                    if totals["sessions"] == 1 else
                    f"Importati {totals['sessions']} appelli con {_plural(totals['questions'], 'domanda', 'domande')}.")
            if cancelled:
                text += " Importazione interrotta."
            self.app.toast(text)
            self._changed()
        elif cancelled:
            self.app.toast("Importazione annullata.")

    def _enable_import(self) -> None:
        try:
            self.import_button.configure(state="normal")
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# Dialoghi
# ---------------------------------------------------------------------------
class ExamSessionDialog(_Modal):
    """``show()`` -> ``{"exam_date", "title", "notes"}`` o None."""

    def __init__(self, parent, title: str, session: dict[str, Any] | None = None):
        super().__init__(parent, title)
        self.heading(title, "La data e' facoltativa: un appello senza data compare in fondo all'elenco.")
        form = ctk.CTkFrame(self.body, fg_color="transparent")
        form.pack(fill="x", padx=16, pady=(0, 8))
        form.grid_columnconfigure(1, weight=1)
        self.label(form, "Data (GG/MM/AAAA)").grid(row=0, column=0, sticky="w")
        self.label(form, "Titolo (es. Primo appello estivo)").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.date_var = ctk.StringVar(value=format_exam_date(session.get("exam_date")) if session else "")
        _entry(form, self.date_var, 150).grid(row=1, column=0, sticky="w", pady=(4, 8))
        self.title_var = ctk.StringVar(value=str(session.get("title") or "") if session else "")
        _entry(form, self.title_var).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 8))
        self.label(form, "Note").grid(row=2, column=0, columnspan=2, sticky="w")
        self.notes_var = ctk.StringVar(value=str(session.get("notes") or "") if session else "")
        _entry(form, self.notes_var).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(8, 14))
        buttons.grid_columnconfigure(0, weight=1)
        self.action(buttons, "Annulla", self.cancel).grid(row=0, column=1, padx=(0, 6))
        self.action(buttons, "Salva", self.confirm, primary=True).grid(row=0, column=2)
        self.bind("<Return>", lambda _e: self.confirm())
        self.place()

    def confirm(self) -> None:
        try:
            parse_exam_date(self.date_var.get())
        except ValueError as exc:
            messagebox.showwarning("Appello", str(exc), parent=self)
            return
        self.result = {"exam_date": self.date_var.get().strip(), "title": self.title_var.get().strip(),
                       "notes": self.notes_var.get().strip()}
        self.close()


class ExamQuestionDialog(_Modal):
    """``show()`` -> campi della domanda (session_id, number, text, material_id, solution, notes) o None."""

    def __init__(self, parent, sessions: list[dict[str, Any]], topics: list[tuple[int, str]],
                 question: dict[str, Any] | None = None, session_id: int | None = None,
                 next_number: Callable[[int | None], int] | None = None):
        super().__init__(parent, "Modifica domanda" if question else "Nuova domanda d'esame")
        self.heading("Modifica domanda" if question else "Nuova domanda d'esame",
                     "L'argomento e' una delle slide della materia: dalla domanda si risale alla lezione.")
        self._next_number = next_number
        self._session_ids: dict[str, int | None] = {NO_SESSION: None}
        for session in sessions:
            name = session_label(session)
            if name in self._session_ids:
                name = f"{name} (#{session['id']})"
            self._session_ids[name] = int(session["id"])
        self._topic_ids: dict[str, int | None] = {NO_TOPIC: None}
        for material_id, title in topics:
            name = title if title not in self._topic_ids else f"{title} (#{material_id})"
            self._topic_ids[name] = material_id
        current_session = question.get("session_id") if question else session_id

        form = ctk.CTkFrame(self.body, fg_color="transparent")
        form.pack(fill="x", padx=16, pady=(0, 6))
        form.grid_columnconfigure(0, weight=1)
        self.label(form, "Appello").grid(row=0, column=0, sticky="w")
        self.label(form, "N.").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.session_combo = _combo(form, list(self._session_ids), command=lambda _v: self._suggest_number())
        self.session_combo.set(next((k for k, v in self._session_ids.items() if v == current_session), NO_SESSION))
        self.session_combo.grid(row=1, column=0, sticky="ew", pady=(4, 8))
        number = question.get("number") if question else (next_number(current_session) if next_number else 1)
        self.number_var = ctk.StringVar(value=str(number))
        _entry(form, self.number_var, 64).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(4, 8))

        self.label(self.body, "Testo della domanda").pack(fill="x", padx=16)
        self.text_box = _text_box(self.body, 120)
        self.text_box.pack(fill="x", padx=16, pady=(4, 8))
        self.label(self.body, "Argomento (slide)").pack(fill="x", padx=16)
        self.topic_combo = _combo(self.body, list(self._topic_ids))
        self.topic_combo.set(next((k for k, v in self._topic_ids.items()
                                   if question and v == question.get("material_id")), NO_TOPIC))
        self.topic_combo.pack(fill="x", padx=16, pady=(4, 8))
        self.label(self.body, "Soluzione (facoltativa)").pack(fill="x", padx=16)
        self.solution_box = _text_box(self.body, 80)
        self.solution_box.pack(fill="x", padx=16, pady=(4, 8))
        self.label(self.body, "Note").pack(fill="x", padx=16)
        self.notes_var = ctk.StringVar(value=str(question.get("notes") or "") if question else "")
        _entry(self.body, self.notes_var).pack(fill="x", padx=16, pady=(4, 8))
        if question:
            self.text_box.insert("1.0", str(question.get("text") or ""))
            self.solution_box.insert("1.0", str(question.get("solution") or ""))
        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(6, 14))
        buttons.grid_columnconfigure(0, weight=1)
        self.action(buttons, "Annulla", self.cancel).grid(row=0, column=1, padx=(0, 6))
        self.action(buttons, "Salva", self.confirm, primary=True).grid(row=0, column=2)
        buttons.pack_configure(side="bottom")  # restano visibili se la finestra e' piu' bassa del contenuto
        self.place(600)
        self.after(60, lambda: self.text_box.focus_set() if self.winfo_exists() else None)

    def _suggest_number(self) -> None:
        if self._next_number is not None:
            self.number_var.set(str(self._next_number(self._session_ids.get(self.session_combo.get()))))

    def confirm(self) -> None:
        text = self.text_box.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("Domanda", "Scrivi il testo della domanda.", parent=self)
            return
        try:
            number = int(self.number_var.get().strip())
            if number < 1:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Domanda", "Il numero della domanda deve essere un intero ≥ 1.", parent=self)
            return
        self.result = {
            "session_id": self._session_ids.get(self.session_combo.get()),
            "number": number,
            "text": text,
            "material_id": self._topic_ids.get(self.topic_combo.get()),
            "solution": self.solution_box.get("1.0", "end").strip(),
            "notes": self.notes_var.get().strip(),
        }
        self.close()


class ExamBulkQuestionsDialog(_Modal):
    """Piu' domande insieme. ``show()`` -> ``{"session_id", "material_id", "questions"}`` o None."""

    def __init__(self, parent, sessions: list[dict[str, Any]], topics: list[tuple[int, str]],
                 session_id: int | None = None):
        super().__init__(parent, "Aggiungi piu' domande")
        self.heading(
            "Aggiungi piu' domande",
            "Incolla o scrivi le domande lasciando una riga vuota fra una e l'altra. Se una domanda contiene "
            f"righe vuote, separa le domande con una riga «{QUESTION_SEPARATOR}». La numerazione continua "
            "dopo le domande gia' presenti nell'appello.",
            wrap=600,
        )
        self._session_ids: dict[str, int | None] = {NO_SESSION: None}
        for session in sessions:
            name = session_label(session)
            if name in self._session_ids:
                name = f"{name} (#{session['id']})"
            self._session_ids[name] = int(session["id"])
        self._topic_ids: dict[str, int | None] = {NO_TOPIC: None}
        for material_id, title in topics:
            name = title if title not in self._topic_ids else f"{title} (#{material_id})"
            self._topic_ids[name] = material_id

        form = ctk.CTkFrame(self.body, fg_color="transparent")
        form.pack(fill="x", padx=16, pady=(0, 6))
        form.grid_columnconfigure((0, 1), weight=1)
        self.label(form, "Appello").grid(row=0, column=0, sticky="w")
        self.label(form, "Argomento (slide) per tutte").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.session_combo = _combo(form, list(self._session_ids))
        self.session_combo.set(next((k for k, v in self._session_ids.items() if v == session_id), NO_SESSION))
        self.session_combo.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.topic_combo = _combo(form, list(self._topic_ids))
        self.topic_combo.set(NO_TOPIC)
        self.topic_combo.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))
        tools = ctk.CTkFrame(self.body, fg_color="transparent")
        tools.pack(fill="x", padx=16, pady=(8, 4))
        self.count_label = ctk.CTkLabel(tools, text="", font=font(10, "bold"), text_color=COLORS.accent, anchor="w")
        self.count_label.pack(side="left")
        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.pack(side="bottom", fill="x", padx=16, pady=(4, 14))
        buttons.grid_columnconfigure(0, weight=1)
        self.action(buttons, "Annulla", self.cancel).grid(row=0, column=1, padx=(0, 6))
        self.action(buttons, "Aggiungi domande", self.confirm, primary=True, width=140).grid(row=0, column=2)
        self.editor = ctk.CTkTextbox(self.body, height=300, width=620, font=font(12), fg_color=COLORS.surface_alt,
                                     border_color=COLORS.border, border_width=1, text_color=COLORS.text, wrap="word")
        self.editor.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self.editor.bind("<KeyRelease>", lambda _e: self._update_count())
        self.editor.bind("<<Paste>>", lambda _e: self.after(10, self._update_count))
        self._update_count()
        self.place(660)
        self.after(60, lambda: self.editor.focus_set() if self.winfo_exists() else None)

    def _questions(self) -> list[dict[str, Any]]:
        return split_pasted_questions(self.editor.get("1.0", "end"))

    def _update_count(self) -> None:
        count = len(self._questions())
        self.count_label.configure(text=f"{_plural(count, 'domanda', 'domande')} trovate")

    def confirm(self) -> None:
        questions = self._questions()
        if not questions:
            messagebox.showwarning("Domande", "Scrivi o incolla almeno una domanda.", parent=self)
            return
        self.result = {
            "session_id": self._session_ids.get(self.session_combo.get()),
            "material_id": self._topic_ids.get(self.topic_combo.get()),
            "questions": questions,
        }
        self.close()


class ExamReviewDialog(_Modal):
    """Revisione della divisione in domande di un tema PDF.

    ``show()`` -> ``{"exam_date", "title", "questions": [{"number", "text"}]}`` o None.
    """

    def __init__(self, parent, file_name: str, exam_date: str, questions: list[dict[str, Any]],
                 position: str = ""):
        super().__init__(parent, "Revisione del tema d'esame")
        self.heading(
            f"Revisione · {file_name}{position}",
            f"Ogni domanda inizia con una riga «{QUESTION_SEPARATOR}». Per dividere una domanda aggiungi la "
            "riga dove serve, per unirne due cancellala. Il testo prima della prima domanda (intestazione "
            "del compito) e' gia' stato tolto.",
            wrap=640,
        )
        form = ctk.CTkFrame(self.body, fg_color="transparent")
        form.pack(fill="x", padx=16, pady=(0, 6))
        form.grid_columnconfigure(1, weight=1)
        self.label(form, "Data dell'appello").grid(row=0, column=0, sticky="w")
        self.label(form, "Titolo").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.date_var = ctk.StringVar(value=exam_date)
        _entry(form, self.date_var, 150, "GG/MM/AAAA").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.title_var = ctk.StringVar(value=Path(file_name).stem)
        _entry(form, self.title_var).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))
        tools = ctk.CTkFrame(self.body, fg_color="transparent")
        tools.pack(fill="x", padx=16, pady=(8, 4))
        self.count_label = ctk.CTkLabel(tools, text="", font=font(10, "bold"), text_color=COLORS.accent, anchor="w")
        self.count_label.pack(side="left")
        self.action(tools, "Dividi qui", self._split_here, width=96).pack(side="right")
        # Pulsanti ancorati in basso prima dell'editor: se la finestra e' bassa si restringe l'editor.
        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.pack(side="bottom", fill="x", padx=16, pady=(4, 14))
        buttons.grid_columnconfigure(0, weight=1)
        self.action(buttons, "Salta questo tema" if position else "Annulla", self.cancel,
                    width=140 if position else 110).grid(row=0, column=1, padx=(0, 6))
        self.action(buttons, "Salva appello", self.confirm, primary=True, width=130).grid(row=0, column=2)
        self.editor = ctk.CTkTextbox(self.body, height=320, width=640, font=font(12), fg_color=COLORS.surface_alt,
                                     border_color=COLORS.border, border_width=1, text_color=COLORS.text, wrap="word")
        self.editor.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self.editor.insert("1.0", questions_to_review_text(questions) if questions else f"{QUESTION_SEPARATOR}\n")
        self.editor.bind("<KeyRelease>", lambda _e: self._update_count())
        self._update_count()
        self.place(700)

    def _split_here(self) -> None:
        self.editor.insert("insert linestart", f"{QUESTION_SEPARATOR}\n")
        self._update_count()

    def _update_count(self) -> None:
        count = len(review_text_to_questions(self.editor.get("1.0", "end")))
        self.count_label.configure(text=f"{_plural(count, 'domanda', 'domande')} trovate")

    def confirm(self) -> None:
        try:
            parse_exam_date(self.date_var.get())
        except ValueError as exc:
            messagebox.showwarning("Revisione", str(exc), parent=self)
            return
        questions = review_text_to_questions(self.editor.get("1.0", "end"))
        if not questions:
            messagebox.showwarning("Revisione", "Nessuna domanda: il testo e' vuoto.", parent=self)
            return
        self.result = {"exam_date": self.date_var.get().strip(), "title": self.title_var.get().strip(),
                       "questions": questions}
        self.close()


class SimilarQuestionsDialog(_Modal):
    """Collega la domanda alle stesse domande uscite in altri appelli. ``show()`` -> True se cambiato."""

    def __init__(self, parent, db, question: dict[str, Any], questions: list[dict[str, Any]]):
        super().__init__(parent, "Uscita anche in…")
        self.db = db
        self.question = question
        self.result = False
        text = " ".join(str(question.get("text") or "").split())
        self.heading("Stessa domanda in altri appelli",
                     f"«{text[:160]}{'…' if len(text) > 160 else ''}»\nCollega le domande uguali: la domanda "
                     "risulta «uscita N volte» e si trova con il filtro «Solo ricorrenti».")
        listing = ctk.CTkScrollableFrame(self.body, fg_color="transparent", height=320, width=600)
        listing.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        listing.grid_columnconfigure(0, weight=1)
        group = question.get("group_id")
        linked = [q for q in questions if group is not None and q.get("group_id") == group and q["id"] != question["id"]]
        row = 0
        if linked:
            row = self._title(listing, "Gia' collegate", row)
            for other in linked:
                row = self._row(listing, other, row, None, linked=True)
        suggestions = [(score, q) for score, q in similar_questions(question, questions, threshold=0.0, limit=40)
                       if q not in linked]
        row = self._title(listing, "Altre domande, dalla piu' simile", row)
        if not suggestions:
            ctk.CTkLabel(listing, text="Nessuna domanda in altri appelli.", font=font(10),
                         text_color=COLORS.text_faint, anchor="w").grid(row=row, column=0, sticky="w")
        for score, other in suggestions:
            row = self._row(listing, other, row, score)
        buttons = ctk.CTkFrame(self.body, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(4, 14))
        buttons.grid_columnconfigure(0, weight=1)
        if group is not None:
            self.action(buttons, "Scollega questa", self._unlink, width=130).grid(row=0, column=1, padx=(0, 6))
        self.action(buttons, "Chiudi", self.close).grid(row=0, column=2)
        self.place(680)

    def cancel(self) -> None:
        self.close()

    @staticmethod
    def _title(parent, text: str, row: int) -> int:
        ctk.CTkLabel(parent, text=text.upper(), font=font(9, "bold"), text_color=COLORS.text_faint, anchor="w").grid(
            row=row, column=0, sticky="w", pady=(6, 2))
        return row + 1

    def _row(self, parent, other: dict[str, Any], row: int, score: float | None, linked: bool = False) -> int:
        card = ctk.CTkFrame(parent, fg_color=COLORS.surface_alt, corner_radius=10, border_width=1,
                            border_color=COLORS.border)
        card.grid(row=row, column=0, sticky="ew", pady=3)
        card.grid_columnconfigure(0, weight=1)
        text = " ".join(str(other.get("text") or "").split())
        head = session_label({"exam_date": other.get("exam_date"), "title": other.get("session_title")}) \
            if other.get("session_id") is not None else NO_SESSION
        if score is not None:
            head += f"  ·  somiglianza {round(score * 100)}%"
        ctk.CTkLabel(card, text=head, font=font(9, "bold"), text_color=COLORS.text_faint, anchor="w").grid(
            row=0, column=0, sticky="w", padx=12, pady=(7, 0))
        ctk.CTkLabel(card, text=f"{int(other.get('number') or 0)}. {text[:220]}", font=font(11),
                     text_color=COLORS.text, anchor="w", wraplength=470, justify="left").grid(
            row=1, column=0, sticky="w", padx=12, pady=(0, 7))
        if not linked:
            self.action(card, "Collega", lambda value=int(other["id"]): self._link(value), width=84).grid(
                row=0, column=1, rowspan=2, padx=10)
        return row + 1

    def _link(self, other_id: int) -> None:
        self.db.link_exam_questions(int(self.question["id"]), other_id)
        self.result = True
        self.close()

    def _unlink(self) -> None:
        self.db.unlink_exam_question(int(self.question["id"]))
        self.result = True
        self.close()
