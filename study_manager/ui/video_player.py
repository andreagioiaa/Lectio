"""Lettore video integrato (libVLC) con annotazioni e dubbi della videolezione.

Richiede VLC installato sul PC: ``python-vlc`` e' solo il binding verso ``libvlc.dll``.
Annotazioni (testo libero con tempi ``[mm:ss]``) e dubbi (stesso formato della
vista PDF e Slide) sono salvati nella riga ``video_lessons``.
"""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from types import SimpleNamespace
from typing import Any

import customtkinter as ctk

from ..config import COLORS
from ..services.pdf_tools.draft import clean_doubts
from ..services.transcription import format_timestamp
from ..utils import format_clock
from .theme import font, mono
from .widgets import center_window, set_window_icon

SPEEDS = ("0.75x", "1x", "1.25x", "1.5x", "1.75x", "2x")
VLC_MISSING = (
    "Il lettore integrato usa VLC, che non e' stato trovato.\n\n"
    "Installalo con 'winget install VideoLAN.VLC' (versione 64 bit) e riapri il video."
)


def load_vlc():
    """Modulo ``vlc`` pronto all'uso, oppure ``None`` se VLC non e' installato."""
    try:
        import vlc

        return vlc if vlc.Instance() is not None else None
    except Exception:  # OSError/NameError se manca libvlc.dll
        return None


class VideoPlayerWindow(ctk.CTkToplevel):
    def __init__(self, view, lesson: dict[str, Any], vlc):
        super().__init__(view.winfo_toplevel())
        self.view = view
        self.app = view.app
        self.db = view.db
        self.lesson_id = int(lesson["id"])
        self.subject = str(lesson.get("subject") or "")
        self.video_title = str(lesson.get("title") or "Video")
        try:
            raw_doubts = json.loads(str(lesson.get("doubts") or "[]"))
        except ValueError:
            raw_doubts = []
        # Oggetto "documento" atteso da DoubtsDialog (doubts, title, path).
        self.doc = SimpleNamespace(
            doubts=clean_doubts(raw_doubts), title=self.video_title, path=Path(str(lesson.get("video_path") or ""))
        )
        self._save_job: str | None = None
        self._poll_job: str | None = None
        self._dragging = False
        self._doubts_dialog = None

        self.title(f"{self.video_title} | Lectio")
        self.configure(fg_color=COLORS.bg)
        self.minsize(900, 520)
        set_window_icon(self, self.app.paths["icon"])
        center_window(self, 1240, 720)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build(str(lesson.get("notes") or ""))
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.instance = vlc.Instance("--no-video-title-show", "--quiet")
        self.player = self.instance.media_player_new()
        self.player.set_media(self.instance.media_new(str(self.doc.path)))
        self.update_idletasks()
        self.player.set_hwnd(self.video_frame.winfo_id())
        self.player.play()
        self._poll()

    # ------------------------------------------------------------------ layout
    def _build(self, notes: str) -> None:
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)
        self.video_frame = tk.Frame(left, bg="black", highlightthickness=0)
        self.video_frame.grid(row=0, column=0, sticky="nsew")

        controls = ctk.CTkFrame(left, fg_color=COLORS.surface, corner_radius=10)
        controls.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        controls.grid_columnconfigure(3, weight=1)
        self.play_button = self._button(controls, "⏸", self.toggle_play, 44)
        self.play_button.grid(row=0, column=0, padx=(10, 4), pady=8)
        self._button(controls, "−10s", lambda: self.skip(-10), 52).grid(row=0, column=1, padx=4)
        self._button(controls, "+10s", lambda: self.skip(10), 52).grid(row=0, column=2, padx=4)
        self.slider = ctk.CTkSlider(
            controls, from_=0, to=1, command=self._on_slide, button_color=COLORS.accent,
            progress_color=COLORS.accent, fg_color=COLORS.border,
        )
        self.slider.set(0)
        self.slider.grid(row=0, column=3, sticky="ew", padx=10)
        self.slider.bind("<ButtonPress-1>", lambda _e: setattr(self, "_dragging", True))
        self.slider.bind("<ButtonRelease-1>", self._on_release)
        self.time_label = ctk.CTkLabel(controls, text="00:00 / 00:00", font=mono(11), text_color=COLORS.text_muted)
        self.time_label.grid(row=0, column=4, padx=6)
        self.speed = ctk.CTkSegmentedButton(controls, values=list(SPEEDS), font=font(10), command=self._on_speed)
        self.speed.set("1x")
        self.speed.grid(row=0, column=5, padx=(4, 10))

        side = ctk.CTkFrame(self, fg_color=COLORS.surface, corner_radius=14, width=330)
        side.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=12)
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(side, text="Annotazioni", font=font(14, "bold"), text_color=COLORS.text,
                     anchor="w").grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 2))
        actions = ctk.CTkFrame(side, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 6))
        self._button(actions, "⏱  Inserisci tempo", self.insert_time, 130).pack(side="left")
        self.doubts_button = self._button(actions, "", self.open_doubts, 120)
        self.doubts_button.pack(side="right")
        self.notes_box = ctk.CTkTextbox(
            side, wrap="word", fg_color=COLORS.surface_alt, border_width=0, corner_radius=10,
            text_color=COLORS.text, font=font(12),
        )
        self.notes_box.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))
        self.notes_box.insert("1.0", notes)
        self.notes_box.bind("<KeyRelease>", lambda _e: self._schedule_save())
        ctk.CTkLabel(
            side, text="Salvataggio automatico. «Inserisci tempo» mette il punto del video in cui sei.",
            font=font(9), text_color=COLORS.text_faint, wraplength=290, justify="left", anchor="w",
        ).grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 12))
        self._update_doubts_button()

    def _button(self, parent, text: str, command, width: int) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, width=width, height=30, font=font(10, "bold"), corner_radius=8,
            fg_color="transparent", hover_color=COLORS.surface_alt, border_width=1,
            border_color=COLORS.border, text_color=COLORS.text, command=command,
        )

    # ---------------------------------------------------------------- player
    def _now(self) -> str:
        return format_timestamp(max(0, self.player.get_time()) / 1000)

    def toggle_play(self) -> None:
        self.player.pause() if self.player.is_playing() else self.player.play()

    def skip(self, seconds: int) -> None:
        self.player.set_time(max(0, self.player.get_time() + seconds * 1000))

    def _on_slide(self, _value) -> None:
        self._dragging = True

    def _on_release(self, _event) -> None:
        self._dragging = False
        self.player.set_position(float(self.slider.get()))

    def _on_speed(self, value: str) -> None:
        self.player.set_rate(float(value.rstrip("x")))

    def _poll(self) -> None:
        length = self.player.get_length()
        current = max(0, self.player.get_time())
        if length > 0 and not self._dragging:
            self.slider.set(min(1.0, current / length))
        self.time_label.configure(text=f"{format_clock(current / 1000)} / {format_clock(max(0, length) / 1000)}")
        self.play_button.configure(text="⏸" if self.player.is_playing() else "▶")
        self._poll_job = self.after(300, self._poll)

    # ------------------------------------------------------- notes / doubts
    def insert_time(self) -> None:
        self.notes_box.insert("insert", f"[{self._now()}] ")
        self.notes_box.focus_set()
        self._schedule_save()

    def _schedule_save(self) -> None:
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(800, self._save)

    def _save(self) -> None:
        self._save_job = None
        try:
            self.db.update_video_lesson_study(self.lesson_id, self.doc.doubts, self.notes_box.get("1.0", "end-1c"))
        except Exception:
            self.app.logger.exception("Salvataggio annotazioni video non riuscito.")
            self.app.toast("Annotazioni del video non salvate.")

    def _update_doubts_button(self) -> None:
        pending = sum(1 for doubt in self.doc.doubts if not doubt.get("card"))
        self.doubts_button.configure(text=f"❓  Dubbi ({pending})" if pending else "❓  Dubbi")

    def open_doubts(self) -> None:
        from .doubts_dialog import DoubtsDialog

        if self._doubts_dialog is not None and self._doubts_dialog.winfo_exists():
            self._doubts_dialog.lift()
            self._doubts_dialog.entry.insert("end", f"[{self._now()}] ")
            return
        self._doubts_dialog = DoubtsDialog(self, self.doc, initial=f"[{self._now()}] ",
                                           heading="Dubbi su questa videolezione")

    # Interfaccia richiesta da DoubtsDialog.
    def doubts_changed(self, _doc) -> None:
        self._update_doubts_button()
        self._save()

    def create_chapter_flashcard(self, front: str, doc, on_created=None) -> None:
        from .flashcard_view import FlashcardDialog

        def saved(card_id: int) -> None:
            self.app.sync_flashcard_export(self.subject)
            if on_created is not None:
                on_created(card_id)

        FlashcardDialog(
            self, None,
            prefill={"subject": self.subject, "front": front, "notes": f"Videolezione: {self.video_title}"},
            on_saved=saved,
        )

    def close(self) -> None:
        for job in (self._poll_job, self._save_job):
            if job is not None:
                self.after_cancel(job)
        self._save()
        try:
            self.player.stop()
            self.player.release()
            self.instance.release()
        except Exception:
            pass
        self.view.player_closed(self.lesson_id)
        self.destroy()
