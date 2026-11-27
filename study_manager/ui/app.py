"""Finestra principale e composizione dei moduli."""

from __future__ import annotations

import dataclasses
import logging
import os
import queue
import signal
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox
from typing import Any, Callable

import customtkinter as ctk

from ..config import APP_TITLE, APP_VERSION, COLORS, PHASE_LABELS
from ..database import Database
from ..logging_setup import setup_logging
from ..paths import ensure_data_layout
from ..services.audio_service import AudioController, audio_backend_info
from ..services.blocker import BlockerController
from ..services.calendar_sync import CalendarSyncService
from ..services.pomodoro_service import PomodoroService
from ..services.subject_folders import SubjectFolders
from ..services import sync_folder as sync
from ..services import updater
from ..services.transcription import TranscriptionController, transcription_backend_info
from ..services.youtube_audio import youtube_backend_info
from ..utils import safe_float
from .analytics_view import AnalyticsView
from .audio_view import AudioView
from .blocker_view import BlockerView
from .calendar_view import CalendarView
from .flashcard_view import FlashcardView
from .pdf_view import PdfView
from .text_shortcuts import install_text_shortcuts
from .pomodoro_view import PomodoroView
from .settings_view import SettingsView
from .subjects_view import SubjectsView
from .theme import (
    THEME_DARK,
    THEME_LIGHT,
    apply_palette,
    color_map,
    configure_theme,
    current_theme,
    font,
    recolor_widget_tree,
    sync_appearance,
)
from .theme_reveal import ThemeReveal
from .update_dialog import UpdateDialog
from .video_view import VideoView
from .widgets import glyph_icon, render_state, set_window_icon


LOGGER = logging.getLogger(__name__)


class LectioApp(ctk.CTk):
    def __init__(self):
        configure_theme()
        super().__init__()
        install_text_shortcuts(self)
        self.paths = ensure_data_layout()
        self.logger = logging.getLogger("lectio.app")
        self._heartbeat_job: str | None = None
        self._check_sync_folder()
        self.db = Database(self.paths["db"], journal_mode="DELETE" if self.paths["synced"] else "WAL")
        interrupted = self.db.recover_interrupted_video_jobs()
        if interrupted:
            self.logger.info(
                "Ripristinate %s trascrizioni interrotte alla chiusura precedente.",
                interrupted,
            )
        self.blocker = BlockerController(self.db, self.paths)
        self.audio = AudioController(self.paths["sounds"])
        self.transcriptions = TranscriptionController(self.paths)
        self.subject_folders = SubjectFolders(self.paths["subjects"])
        # I file dentro ``Materie`` e nella cartella dati si salvano con percorso relativo.
        self.db.set_path_roots(subjects=self.paths["subjects"], data=self.paths["shared"])
        try:
            converted = self.db.relativize_stored_paths()
            if converted:
                self.logger.info("Convertiti %s percorsi in forma relativa.", converted)
        except Exception:
            self.logger.exception("Conversione dei percorsi in forma relativa non riuscita.")
        try:
            self.subject_folders.ensure_all(self.db.list_subject_tags())
        except Exception:
            self.logger.exception("Creazione delle cartelle materia non riuscita.")
        self.pomodoro = PomodoroService(self.db, self.blocker)
        # Google Calendar: tutte le chiamate HTTP avvengono in thread daemon; gli
        # aggiornamenti tornano al main thread tramite la coda ``post``.
        self.google_sync = CalendarSyncService(self.db)
        self.google_sync.add_listener(
            lambda kind, data: self.post(lambda: self._on_google_sync_event(kind, data))
        )
        self._audio_autoplay = False
        self._audio_track = ""
        self._audio_volume = 0.55
        self._audio_loop = True
        self._audio_auto_started: set[int] = set()
        self._audio_auto_paused: int | None = None
        self._audio_last_error = ""
        self.reload_audio_settings()
        self._closing = False
        self._shutdown_done = False
        self._poll_job: str | None = None
        self._toast_job: str | None = None
        self._current_view: str | None = None
        self._title_signature = ""
        self._last_result_id: int | None = None
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._ui_queue: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        self.views: dict[str, Any] = {}
        self._view_theme: dict[str, str] = {}  # tema con cui ogni vista e' dipinta
        self._hosts_warned = False
        self._update_info: updater.UpdateInfo | None = None
        self._update_dialog: UpdateDialog | None = None
        self._update_checking = False
        # Tema salvato: la palette va scelta prima di creare i widget.
        try:
            saved_theme = self.db.get_setting("ui_theme", THEME_DARK)
        except Exception:
            saved_theme = THEME_DARK
        apply_palette(THEME_LIGHT if saved_theme == THEME_LIGHT else THEME_DARK)

        self.title(APP_TITLE)
        self.configure(fg_color=COLORS.bg)
        self.minsize(1060, 680)
        self._set_initial_geometry()
        set_window_icon(self, self.paths["icon"])
        self._set_app_user_model_id()
        self.report_callback_exception = self._report_exception
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._nav_mode = ""
        self._nav_layout_job: str | None = None
        self._build_sidebar()
        self._build_content()
        self._build_toast()
        self._setup_signals()
        if self.paths["synced"]:
            self._sync_heartbeat()
            self.after(1500, self._warn_conflict_copies)
        if current_theme() == THEME_LIGHT:
            # i widget sono nati con la palette chiara: restano da convertire solo
            # i pochi colori scritti direttamente nelle viste
            recolor_widget_tree(self, color_map(THEME_DARK, THEME_LIGHT, literals_only=True))

        self._prefill_views(exclude="pomodoro")
        self.show_view("pomodoro")
        # I grafici della dashboard (Matplotlib) si preparano in sottofondo poco
        # dopo l'avvio, cosi' anche "Analisi" e' pronta al primo clic.
        self.after(1500, self._warm_up_views)
        self.after(1000, self._global_poll)
        # Sync automatica non bloccante all'avvio (se l'account e' collegato).
        self.after(2500, self._startup_google_sync)
        # Aggiornamenti: un controllo a ogni avvio (vedi services/updater.py).
        self.after(800, self._startup_update_check)

    # ------------------------------------------------------------------ chrome
    def _set_initial_geometry(self) -> None:
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(1320, max(1060, screen_w - 80))
        height = min(860, max(680, screen_h - 90))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    @staticmethod
    def _set_app_user_model_id() -> None:
        if os.name != "nt":
            return
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Lectio.Desktop.1"
            )
        except (AttributeError, OSError):
            pass

    # Voci di navigazione: (chiave, glifo Segoe MDL2, etichetta, etichetta breve)
    NAV_ITEMS = (
        ("pomodoro", "", "Pomodoro", "Pomodoro"),
        ("audio", "", "Audio", "Audio"),
        ("videos", "", "Video e Trascrizione", "Video"),
        ("pdf", "", "PDF e Slide", "PDF"),
        ("blocker", "", "Blocco distrazioni", "Blocco"),
        ("calendar", "", "Calendario", "Calendario"),
        ("subjects", "", "Materie", "Materie"),
        ("flashcards", "", "Flashcard", "Flashcard"),
        ("analytics", "", "Analisi", "Analisi"),
        ("settings", "", "Impostazioni", "Impostazioni"),
    )

    def _build_sidebar(self) -> None:
        """Barra di navigazione in alto: marchio, sezioni affiancate e cambio tema.

        Se la finestra e' stretta le etichette si accorciano e, al limite,
        restano solo le icone (vedi ``_layout_nav``).
        """
        bar = ctk.CTkFrame(self, height=58, corner_radius=0, fg_color=COLORS.sidebar, border_width=0)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_rowconfigure(0, weight=1)
        bar.grid_columnconfigure(1, weight=1)
        self.topbar = bar

        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="w", padx=(18, 14))
        logo = self._load_logo(40)
        if logo is not None:
            ctk.CTkLabel(brand, text="", image=logo).pack(side="left", padx=(0, 10))
        brand_text = ctk.CTkFrame(brand, fg_color="transparent")
        brand_text.pack(side="left")
        ctk.CTkLabel(brand_text, text="Lectio", font=font(17, "bold"), text_color=COLORS.text, anchor="w").pack(
            anchor="w"
        )
        ctk.CTkLabel(
            brand_text, text=f"v{APP_VERSION} · locale e offline", font=font(9), text_color=COLORS.accent, anchor="w",
        ).pack(anchor="w")

        nav = ctk.CTkFrame(bar, fg_color="transparent")
        nav.grid(row=0, column=1, sticky="w")
        self.nav_frame = nav
        for column, (key, _glyph, label, _short) in enumerate(self.NAV_ITEMS):
            button = ctk.CTkButton(
                nav,
                text=label,
                width=20,
                height=40,
                font=font(12, "bold"),
                fg_color="transparent",
                hover_color=COLORS.surface_alt,
                text_color=COLORS.text_muted,
                corner_radius=20,
                compound="left",
                command=lambda view_key=key: self.show_view(view_key),
            )
            button.grid(row=0, column=column, padx=2)
            self._nav_buttons[key] = button

        # Cambio tema: il simbolo mostra il tema in cui si passera' (sole = chiaro).
        self.theme_button = ctk.CTkButton(
            bar,
            text="",
            width=40,
            height=40,
            font=font(18),
            fg_color="transparent",
            hover_color=COLORS.surface_alt,
            text_color=COLORS.amber,
            corner_radius=20,
            command=self.toggle_theme,
        )
        self.theme_button.grid(row=0, column=3, sticky="e", padx=(4, 14), pady=8)
        # Compare solo quando c'e' una versione nuova (``_show_update_button``).
        self.update_button = ctk.CTkButton(
            bar,
            text="",
            width=20,
            height=32,
            font=font(11, "bold"),
            fg_color=COLORS.accent,
            hover_color=COLORS.accent_hover,
            text_color="#FFFFFF",
            corner_radius=16,
            command=self.open_update_dialog,
        )
        self._update_theme_button()
        # linea sottile sotto la barra, come i separatori di X
        tk.Frame(bar, height=1, bg=COLORS.border, bd=0, highlightthickness=0).place(
            relx=0, rely=1.0, relwidth=1.0, anchor="sw"
        )

        bar.bind("<Configure>", lambda _e: self._schedule_nav_layout())
        self.after(50, self._layout_nav)

    def _schedule_nav_layout(self) -> None:
        if self._nav_layout_job is not None:
            try:
                self.after_cancel(self._nav_layout_job)
            except (tk.TclError, ValueError):
                pass
        self._nav_layout_job = self.after(40, self._layout_nav)

    def _nav_texts(self, mode: str) -> dict[str, str]:
        texts = {}
        for key, _glyph, label, short in self.NAV_ITEMS:
            if mode == "full":
                texts[key] = label
            elif mode == "short":
                texts[key] = short
            else:
                texts[key] = ""
        return texts

    def _layout_nav(self) -> None:
        """Sceglie etichette complete, brevi o solo icone in base alla larghezza."""
        self._nav_layout_job = None
        try:
            available = self.topbar.winfo_width()
            if available <= 1:
                return
            fixed = self.topbar.grid_slaves(row=0, column=0)[0].winfo_reqwidth() + self.theme_button.winfo_reqwidth() + 58
            if self.update_button.winfo_ismapped():
                fixed += self.update_button.winfo_reqwidth() + 10
            for mode in ("full", "short", "icons"):
                texts = self._nav_texts(mode)
                for key, button in self._nav_buttons.items():
                    if button.cget("text") != texts[key]:
                        button.configure(text=texts[key], width=40 if mode == "icons" else 20)
                self.nav_frame.update_idletasks()
                if self.nav_frame.winfo_reqwidth() + fixed <= available or mode == "icons":
                    self._nav_mode = mode
                    break
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ tema
    def _update_theme_button(self) -> None:
        light = current_theme() == THEME_LIGHT
        # in tema chiaro si mostra la luna (si torna allo scuro), in tema scuro il sole
        color = COLORS.purple if light else COLORS.amber
        icon = glyph_icon("" if light else "", color, 20)
        self.theme_button.configure(
            image=icon,
            text="" if icon else ("☾" if light else "☀"),
            text_color=color,
        )

    def toggle_theme(self) -> None:
        """Passa dal tema scuro al chiaro (e viceversa) senza riavviare l'app."""
        target = THEME_DARK if current_theme() == THEME_LIGHT else THEME_LIGHT
        started = time.perf_counter()
        reveal = ThemeReveal.capture(self)
        try:
            self.configure(cursor="watch")
            self.update_idletasks()
        except tk.TclError:
            pass
        # Solo la sezione visibile si ricolora subito; le altre alla prossima
        # apertura (``_sync_view_theme``): ricolorarle tutte richiede secondi.
        hidden = [view for key, view in self.views.items() if key != self._current_view]
        mapping = apply_palette(target, defer=hidden)
        recolor_widget_tree(self, mapping, skip=hidden)
        if self._current_view:
            self._sync_view_theme(self._current_view)
        self.configure(fg_color=COLORS.bg)
        self._update_theme_button()
        for button in self._nav_buttons.values():
            button._lectio_colors = None  # i colori della voce attiva vanno ricalcolati
        self._paint_nav()
        try:
            self.db.set_setting("ui_theme", target)
        except Exception:
            self.logger.exception("Salvataggio del tema non riuscito.")
        try:
            self.configure(cursor="")
        except tk.TclError:
            pass
        self.logger.info(
            "Tema %s applicato in %.0f ms.", target, (time.perf_counter() - started) * 1000
        )
        if reveal is not None:
            reveal.play(self)

    def _sync_view_theme(self, key: str) -> None:
        """Porta la vista al tema attivo se era stata dipinta con l'altro."""
        view = self.views.get(key)
        painted = self._view_theme.get(key, current_theme())
        if view is None or painted == current_theme():
            return
        recolor_widget_tree(view, color_map(painted, current_theme()))
        sync_appearance(view)
        self._view_theme[key] = current_theme()
        hook = getattr(view, "on_theme_changed", None)
        if hook is not None:
            try:
                hook()
            except Exception:
                self.logger.exception("Aggiornamento del tema non riuscito per la vista '%s'.", key)

    def _build_content(self) -> None:
        self.content = ctk.CTkFrame(self, fg_color=COLORS.bg, corner_radius=0)
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        # Tutte le viste vengono create all'avvio e restano in vita fino alla
        # chiusura: sono impilate nella stessa cella della griglia e il cambio
        # di sezione porta in primo piano quella scelta (tkraise). Nessuna vista
        # viene nascosta con grid_forget o distrutta, quindi i widget non vanno
        # ridisegnati a ogni cambio e non c'e' sfarfallio.
        self._view_factories = {
            "pomodoro": PomodoroView,
            "audio": AudioView,
            "videos": VideoView,
            "pdf": PdfView,
            "blocker": BlockerView,
            "calendar": CalendarView,
            "subjects": SubjectsView,
            "flashcards": FlashcardView,
            "analytics": AnalyticsView,
            "settings": SettingsView,
        }
        self.views = {}
        for key in self._view_factories:
            self._ensure_view(key)

    def _ensure_view(self, key: str):
        view = self.views.get(key)
        if view is None:
            factory = self._view_factories[key]
            started = time.perf_counter()
            try:
                view = factory(self.content, self)
            except Exception as exc:
                # Una vista difettosa non deve impedire l'avvio delle altre.
                self.logger.exception("Creazione della vista '%s' non riuscita.", key)
                view = self._error_view(key, exc)
            view.grid(row=0, column=0, sticky="nsew", padx=24, pady=(18, 12))
            view.lower()
            self.views[key] = view
            self._view_theme[key] = current_theme()
            self.logger.debug(
                "Vista '%s' creata in %.0f ms.", key, (time.perf_counter() - started) * 1000
            )
        return view

    def _error_view(self, key: str, error: Exception) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.content, fg_color="transparent")
        ctk.CTkLabel(
            frame,
            text=f"La sezione '{key}' non e' disponibile.",
            font=font(18, "bold"),
            text_color=COLORS.text,
            anchor="w",
        ).pack(anchor="w", pady=(10, 6))
        ctk.CTkLabel(
            frame,
            text=f"{error}\n\nDettagli in lectio.log.",
            font=font(12),
            text_color=COLORS.text_muted,
            anchor="w",
            justify="left",
        ).pack(anchor="w")
        return frame

    def is_view_visible(self, view: Any) -> bool:
        """True se ``view`` e' la sezione in primo piano.

        Con le viste sempre presenti ``winfo_ismapped`` e' vero per tutte,
        quindi le viste chiedono all'app quale sia quella visibile.
        """
        if self._closing or self._current_view is None:
            return False
        return self.views.get(self._current_view) is view

    def _prefill_views(self, exclude: str = "") -> None:
        """Popola subito tutte le sezioni con i dati, ancora coperte.

        ``on_show`` carica i dati e ``on_hide`` ferma gli eventuali timer di
        aggiornamento, cosi' le sezioni nascoste non consumano CPU ma alla
        prima apertura sono gia' complete.
        """
        for key, view in list(self.views.items()):
            if key == exclude:
                continue
            try:
                if hasattr(view, "on_show"):
                    view.on_show()
                if hasattr(view, "on_hide"):
                    view.on_hide()
            except Exception:
                self.logger.exception("Precaricamento della vista '%s' non riuscito.", key)

    def _warm_up_views(self) -> None:
        if self._closing:
            return
        for key, view in list(self.views.items()):
            prepare = getattr(view, "prepare_in_background", None)
            if prepare is None:
                continue
            try:
                prepare()
            except Exception:
                self.logger.exception("Preparazione della vista '%s' non riuscita.", key)

    def _load_logo(self, height: int) -> ctk.CTkImage | None:
        """Logo dell'app (sfondo trasparente) per la barra in alto; ``None`` se il file manca."""
        try:
            from PIL import Image

            image = Image.open(self.paths["logo"])
            width = round(height * image.width / image.height)
            return ctk.CTkImage(light_image=image, dark_image=image, size=(width, height))
        except Exception:  # noqa: BLE001 - il logo e' solo decorativo
            LOGGER.debug("Logo non caricato", exc_info=True)
            return None

    def _build_toast(self) -> None:
        self._toast = ctk.CTkLabel(
            self,
            text="",
            font=font(11, "bold"),
            text_color=COLORS.text,
            fg_color=COLORS.surface_alt,
            corner_radius=10,
            padx=16,
            height=38,
        )
        self._toast.place_forget()

    # ------------------------------------------------------------------ nav
    def show_view(self, key: str) -> None:
        if key not in self._view_factories:
            return
        if self._current_view == key:
            view = self._ensure_view(key)
            if hasattr(view, "on_show"):
                view.on_show()
            return
        old = self.views.get(self._current_view) if self._current_view else None
        if old is not None and hasattr(old, "on_hide"):
            old.on_hide()
        view = self._ensure_view(key)
        self._sync_view_theme(key)
        self._current_view = key
        # La vista si aggiorna (solo se i dati sono cambiati) mentre e' ancora
        # coperta, poi viene portata in primo piano: nessuna rimozione dalla
        # griglia, nessun ridisegno completo.
        if hasattr(view, "on_show"):
            view.on_show()
        view.tkraise()
        self._paint_nav()

    def _paint_nav(self) -> None:
        """Voce attiva: pillola con testo chiaro e icona blu; le altre in grigio."""
        glyphs = {key: glyph for key, glyph, _label, _short in self.NAV_ITEMS}
        for nav_key, button in self._nav_buttons.items():
            active = nav_key == self._current_view
            colors = (
                COLORS.surface_alt if active else "transparent",
                COLORS.text if active else COLORS.text_muted,
                COLORS.accent if active else COLORS.text_muted,
            )
            if getattr(button, "_lectio_colors", None) != colors:
                button._lectio_colors = colors
                button.configure(
                    fg_color=colors[0],
                    text_color=colors[1],
                    image=glyph_icon(glyphs[nav_key], colors[2], 20),
                )

    # ------------------------------------------------------------------ events
    def start_from_event(self, event: dict[str, Any]) -> None:
        if not self._confirm_interrupt_if_needed(
            f"Avviare lo studio per '{event.get('title', 'evento')}'?"
        ):
            return
        ok, error = self.pomodoro.start_from_event(event)
        if not ok:
            messagebox.showwarning("Lectio", error, parent=self)
            return
        self.show_view("pomodoro")
        self.toast(f"Sessione avviata: {event.get('title', 'evento')}")

    def start_from_material(self, material: dict[str, Any]) -> None:
        if not self._confirm_interrupt_if_needed(
            f"Avviare lo studio per '{material.get('title', 'materiale')}'?"
        ):
            return
        ok, error = self.pomodoro.start_from_material(material)
        if not ok:
            messagebox.showwarning("Lectio", error, parent=self)
            return
        self.show_view("pomodoro")
        self.toast(f"Studio avviato: {material.get('title', 'materiale')}")

    def _confirm_interrupt_if_needed(self, message: str) -> bool:
        snapshot = self.pomodoro.snapshot()
        if snapshot["status"] in {"running", "paused"}:
            return messagebox.askyesno(
                "Sessione in corso",
                f"{message}\n\nLa sessione corrente verra' salvata come incompleta. Continuare?",
                parent=self,
            ) and self._reset_for_new_session()
        return True

    def _reset_for_new_session(self) -> bool:
        self.pomodoro.reset()
        return True

    def on_session_finished(self, result: dict[str, Any]) -> None:
        result_id = int(result.get("session_id", 0) or 0)
        if result_id and result_id == self._last_result_id:
            return
        self._last_result_id = result_id or self._last_result_id
        subjects = self.views.get("subjects")
        if subjects is not None:
            subjects.refresh_if_visible()
        analytics = self.views.get("analytics")
        if analytics is not None:
            analytics.refresh_if_visible()
        minutes = result.get("minutes", 0)
        if result.get("completed"):
            self.toast(f"Pomodoro completato · {minutes} minuti salvati.")
        else:
            self.toast(f"Sessione interrotta · {minutes} minuti salvati.")

    # ------------------------------------------------------------ google calendar
    def _startup_google_sync(self) -> None:
        if self._closing:
            return
        try:
            if self.google_sync.has_sources():
                self.google_sync.sync_async("startup")
        except Exception:
            self.logger.exception("Avvio della sincronizzazione Google non riuscito.")

    def _on_google_sync_event(self, kind: str, data: dict[str, Any]) -> None:
        for key in ("calendar", "settings"):
            view = self.views.get(key)
            if view is not None and hasattr(view, "on_sync_event"):
                try:
                    view.on_sync_event(kind, data)
                except Exception:
                    self.logger.exception("Aggiornamento vista '%s' dopo la sync non riuscito.", key)
        if kind == "sync_finished":
            subjects = self.views.get("subjects")
            if subjects is not None:
                render_state(subjects).forget()
                subjects.refresh_if_visible()
            error = str(data.get("error") or "")
            if error and data.get("reason") != "startup" and self._current_view != "calendar":
                self.toast(f"Google Calendar: {error}")

    def ensure_google_calendar_for_subject(self, subject: str) -> None:
        """Crea in background il calendario Google di una nuova materia (se attivo)."""
        name = str(subject or "").strip()
        if not name or self.db.get_setting("google_auto_create_calendar") not in {"1", "true"}:
            return
        if not self.google_sync.is_connected():
            return

        def worker() -> None:
            try:
                created = self.google_sync.ensure_calendar_for_subject(name)
            except Exception as exc:
                self.logger.warning("Creazione calendario Google per '%s' non riuscita: %s", name, exc)
                self.post(lambda: self.toast("Calendario Google della materia non creato."))
            else:
                if created:
                    self.post(lambda: self._on_google_sync_event("sync_finished", {"reason": "subject"}))
            finally:
                self.db.close_current_thread()

        threading.Thread(target=worker, name="Lectio-GoogleCalendarCreate", daemon=True).start()

    # ------------------------------------------------------------ cartelle materia
    def subject_folder(self, subject: str, kind: str | None = None) -> Path | None:
        """Cartella (o sottocartella ``kind``) della materia, creata se manca."""
        if not str(subject or "").strip():
            return None
        if kind is None:
            return self.subject_folders.ensure(subject)
        return self.subject_folders.subfolder(subject, kind)

    def on_subject_renamed(self, old_name: str, new_name: str) -> None:
        """Rinomina la cartella su disco e aggiorna i percorsi salvati nel database."""
        try:
            old_dir, new_dir = self.subject_folders.rename(old_name, new_name)
            if old_dir is not None and new_dir is not None and old_dir != new_dir:
                self.db.relocate_paths(old_dir, new_dir)
        except Exception:
            self.logger.exception("Rinomina della cartella materia non riuscita.")
            self.toast("Materia rinominata, ma la cartella su disco non e' stata spostata.")
        self.sync_flashcard_export(new_name)

    def sync_flashcard_export(self, *subjects: str) -> None:
        """Aggiorna ``Flashcard/Flashcard.csv`` nelle cartelle delle materie indicate."""
        seen: set[str] = set()
        for subject in subjects:
            name = str(subject or "").strip()
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            try:
                cards = self.db.list_flashcards(subject=name, limit=100000)
                self.subject_folders.export_flashcards(name, cards)
            except Exception:
                self.logger.exception("Export flashcard per '%s' non riuscito.", name)

    def register_generated_material(self, file_path: str | Path, subject: str, title: str = "") -> None:
        """Collega a una materia un file prodotto dall'app (es. export PDF)."""
        name = str(subject or "").strip()
        if not name:
            return
        path = Path(file_path)
        existing = self.db.find_material_by_path(str(path))
        if existing is not None:
            self.db.update_material(
                int(existing["id"]),
                str(existing.get("title") or title or path.stem),
                str(path),
                name,
                safe_float(existing.get("target_hours", 0)),
                existing.get("calendar_event_id"),
            )
        else:
            self.db.add_material(title or path.stem, str(path), name, 0, None)
        subjects = self.views.get("subjects")
        if subjects is not None:
            subjects.refresh_if_visible()

    def open_subject_folder(self, subject: str, kind: str | None = None) -> None:
        folder = self.subject_folder(subject, kind)
        if folder is None:
            messagebox.showerror("Cartella materia", "Cartella non disponibile.", parent=self)
            return
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            messagebox.showinfo("Cartella materia", str(folder), parent=self)

    def open_in_view(self, key: str, **options: Any) -> None:
        """Apre una sezione e, se la vista lo supporta, seleziona un elemento."""
        self.show_view(key)
        view = self.views.get(key)
        if view is not None and hasattr(view, "focus_item"):
            try:
                view.focus_item(**options)
            except Exception:
                self.logger.exception("Apertura elemento in '%s' non riuscita.", key)

    def toast(self, message: str) -> None:
        if self._closing:
            return
        if self._toast_job is not None:
            try:
                self.after_cancel(self._toast_job)
            except (tk.TclError, ValueError):
                pass
        self._toast.configure(text=message)
        self._toast.place(relx=1.0, rely=1.0, x=-26, y=-22, anchor="se")
        self._toast.lift()
        self._toast_job = self.after(3600, self._hide_toast)

    def _hide_toast(self) -> None:
        self._toast.place_forget()
        self._toast_job = None

    def update_window_title(self, snapshot: dict[str, Any]) -> None:
        if snapshot["status"] in {"running", "paused"}:
            phase = PHASE_LABELS.get(snapshot["phase"], snapshot["phase"])
            title = f"{snapshot['remaining_label']} · {phase} · {snapshot['subject']} | Lectio"
        else:
            title = APP_TITLE
        if title != self._title_signature:
            self._title_signature = title
            self.title(title)

    # ------------------------------------------------------------------ polling
    def post(self, callback: Callable[[], None]) -> None:
        """Accoda un aggiornamento UI da un thread di background.

        Tkinter non e' thread-safe: nessun widget viene toccato fuori dal thread
        principale. La coda viene svuotata dal polling globale.
        """
        if self._closing:
            return
        self._ui_queue.put(callback)

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            if self._closing:
                return
            try:
                callback()
            except Exception:
                self.logger.exception("Errore eseguendo un aggiornamento UI accodato.")

    @staticmethod
    def _setting_bool(value: str) -> bool:
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def reload_audio_settings(self) -> None:
        settings = self.db.get_settings()
        self._audio_autoplay = self._setting_bool(settings.get("audio_autoplay", "0"))
        self._audio_track = str(settings.get("audio_track", "") or "")
        if self._audio_track and not self.audio.has_track(self._audio_track):
            self._audio_track = ""
        self._audio_volume = max(
            0.0, min(1.0, safe_float(settings.get("audio_volume", "0.55"), 0.55))
        )
        self._audio_loop = self._setting_bool(settings.get("audio_loop", "1"))
        self.audio.set_volume(self._audio_volume)
        self.audio.set_loop(self._audio_loop)
        if not self._audio_autoplay and self.audio.status()["source"] == "auto":
            self.audio.stop()
            self._audio_auto_paused = None
            self._audio_auto_started.clear()

    def refresh_after_import(self) -> None:
        """Ricarica configurazione e viste dopo un ripristino dei dati."""
        self.reload_audio_settings()
        try:
            self.blocker.reload_config()
        except Exception:
            self.logger.exception("Ricarica blocker dopo importazione non riuscita.")
        try:
            self.pomodoro.reload_config()
        except Exception:
            self.logger.exception("Ricarica Pomodoro dopo importazione non riuscita.")
        # Solo la sezione visibile si aggiorna subito; le altre ricostruiscono
        # alla prossima apertura (i dati importati cambiano la loro firma).
        current = self.views.get(self._current_view) if self._current_view else None
        for key, view in list(self.views.items()):
            render_state(view).forget()
            if view is not current:
                continue
            try:
                if hasattr(view, "on_show"):
                    view.on_show()
                elif hasattr(view, "refresh"):
                    view.refresh()
            except Exception:
                self.logger.exception("Aggiornamento vista '%s' non riuscito.", key)

    def _sync_audio(self, snapshot: dict[str, Any]) -> None:
        """Allinea la musica automatica allo stato del Pomodoro."""
        audio_status = self.audio.status()
        if not self._audio_autoplay or not self._audio_track:
            if audio_status["source"] == "auto":
                self.audio.stop()
                self._audio_auto_paused = None
                self._audio_auto_started.clear()
            return

        phase = str(snapshot.get("phase", ""))
        status = str(snapshot.get("status", ""))
        session_id = snapshot.get("session_id")
        if not isinstance(session_id, int):
            session_id = None

        if phase == "work" and status == "running" and session_id is not None:
            if (
                self._audio_auto_paused == session_id
                and audio_status["source"] == "auto"
                and audio_status["paused"]
            ):
                if self.audio.resume():
                    self._audio_auto_paused = None
                return
            if session_id in self._audio_auto_started:
                return
            if audio_status["source"] == "manual":
                # Una scelta manuale dell'utente ha la precedenza.
                self._audio_auto_started.add(session_id)
                return
            if audio_status["source"] == "auto":
                self.audio.stop()
            ok, error = self.audio.play(
                self._audio_track,
                loop=self._audio_loop,
                source="auto",
            )
            self._audio_auto_started.add(session_id)
            if ok:
                self._audio_last_error = ""
            elif error and error != self._audio_last_error:
                self._audio_last_error = error
                self.toast(f"Audio non avviato: {error}")
            return

        if phase == "work" and status == "paused" and session_id is not None:
            if audio_status["source"] == "auto" and audio_status["playing"]:
                if self.audio.pause():
                    self._audio_auto_paused = session_id
            return

        # Pausa, fine fase o timer fermo: interrompe solo l'audio automatico.
        if audio_status["source"] == "auto":
            self.audio.stop()
        self._audio_auto_paused = None

    def _global_poll(self) -> None:
        if self._closing:
            return
        self._drain_ui_queue()
        try:
            status = self.blocker.status()
            snapshot = self.pomodoro.snapshot()
            self.update_window_title(snapshot)
            self._sync_audio(snapshot)
            result = snapshot.get("last_result")
            if isinstance(result, dict):
                self.on_session_finished(result)
            # Lo stato del blocco non e' piu' nella barra in alto: resta solo
            # l'avviso, una volta, se il file hosts va ripristinato.
            if status["hosts_residual"] and not self._hosts_warned:
                self._hosts_warned = True
                self.toast("File hosts da ripristinare: Blocco distrazioni > Ripristina file hosts.")
            elif not status["hosts_residual"]:
                self._hosts_warned = False
        except Exception:
            self.logger.exception("Errore nel polling globale.")
        self._poll_job = self.after(1000, self._global_poll)

    # ------------------------------------------------------------------ diagnostics
    def run_smoke_test(self) -> int:
        """Apre tutte le viste e prova timer/chiusura senza interazione manuale."""
        try:
            self.withdraw()
            self.update()
            self.db.set_settings(
                {
                    "block_sites_enabled": "0",
                    "block_apps_enabled": "0",
                    "auto_start_next": "0",
                    "web_trap_enabled": "0",
                    "audio_autoplay": "0",
                }
            )
            self.blocker.reload_config()
            self.reload_audio_settings()
            try:
                pygame_version = audio_backend_info()
                yt_version = youtube_backend_info()
                transcription_version = transcription_backend_info()
            except Exception as exc:
                raise RuntimeError(f"Backend audio/trascrizione non disponibile: {exc}") from exc
            self.logger.info(
                "Backend pronti: pygame %s, yt-dlp %s, %s.",
                pygame_version,
                yt_version,
                transcription_version,
            )
            smoke_video = os.environ.get("LECTIO_SMOKE_TRANSCRIBE_VIDEO", "").strip()
            if smoke_video:
                smoke_target = Path(self.paths["transcripts"]) / "smoke-trascrizione.txt"
                outcome: dict[str, Any] = {}

                def on_smoke_done(event: dict[str, Any]) -> None:
                    outcome.update(event)

                started, start_error = self.transcriptions.start(
                    lesson_id=0,
                    video_path=smoke_video,
                    transcript_path=str(smoke_target),
                    model="base-q5_1",
                    language="it",
                    title="Smoke trascrizione",
                    subject="Smoke Test",
                    on_progress=lambda event: None,
                    on_done=on_smoke_done,
                )
                if not started:
                    raise RuntimeError(
                        f"Trascrizione di prova non avviata: {start_error}"
                    )
                deadline = time.monotonic() + 180.0
                while not outcome and time.monotonic() < deadline:
                    self.update()
                    self.update_idletasks()
                    time.sleep(0.05)
                if not outcome:
                    self.transcriptions.cancel()
                    self.transcriptions.shutdown()
                    raise RuntimeError("La trascrizione di prova non e' terminata in tempo.")
                self.transcriptions.shutdown()
                if not outcome.get("ok"):
                    raise RuntimeError(
                        f"Trascrizione di prova fallita: {outcome.get('error')}"
                    )
                result = dict(outcome.get("result") or {})
                output = Path(str(result.get("transcript_path") or ""))
                if not output.is_file() or output.stat().st_size <= 0:
                    raise RuntimeError("La trascrizione di prova non ha prodotto un file .txt.")
                self.logger.info(
                    "Smoke trascrizione completato: %s (%s blocchi).",
                    output,
                    result.get("blocks"),
                )
            smoke_url = os.environ.get("LECTIO_SMOKE_YOUTUBE_URL", "").strip()
            if smoke_url:
                from ..services.youtube_audio import download_audio, probe_video

                info = probe_video(smoke_url)
                track = download_audio(
                    smoke_url,
                    self.paths["sounds"],
                    str(info["title"]),
                )
                ok, error = self.audio.play(track.name, loop=False, source="manual")
                if not ok:
                    raise RuntimeError(f"Riproduzione audio di prova fallita: {error}")
                self.audio.stop()
                self.logger.info("Smoke audio YouTube completato: %s.", track.name)
            for key in self._view_factories:
                self.show_view(key)
                for _ in range(8):
                    self.update()
                    self.update_idletasks()
                    time.sleep(0.03)
            analytics = self.views.get("analytics")
            deadline = time.monotonic() + 20.0
            while (
                analytics is not None
                and not analytics._charts_ready
                and time.monotonic() < deadline
            ):
                self.update()
                self.update_idletasks()
                time.sleep(0.05)
            if analytics is not None and not analytics._charts_ready:
                raise RuntimeError("I grafici non sono stati inizializzati.")
            self.show_view("pomodoro")
            started, error = self.pomodoro.start_current(subject="Smoke Test")
            if not started:
                raise RuntimeError(error or "Timer non avviato.")
            for _ in range(6):
                self.update()
                time.sleep(0.03)
            self.pomodoro.reset()
            self.update()
            self._closing = True
            self._shutdown_services()
            try:
                self.destroy()
            except tk.TclError:
                pass
            self.logger.info("Smoke test completato correttamente.")
            return 0
        except Exception:
            self.logger.exception("Smoke test fallito.")
            self.emergency_shutdown()
            try:
                self.destroy()
            except tk.TclError:
                pass
            return 1

    # ------------------------------------------------------------------ lifecycle
    def _report_exception(self, *args) -> None:
        if len(args) == 3:
            exc_type, exc_value, exc_tb = args
        else:
            exc_type, exc_value, exc_tb = sys.exc_info()
        self.logger.error(
            "Eccezione non gestita nella UI.",
            exc_info=(exc_type, exc_value, exc_tb),
        )
        try:
            messagebox.showerror(
                "Errore imprevisto",
                f"Si e' verificato un errore:\n{exc_value}\n\nDettagli in lectio.log.",
                parent=self,
            )
        except tk.TclError:
            pass

    def _setup_signals(self) -> None:
        for name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, self._handle_signal)
            except (ValueError, OSError):
                pass

    def _handle_signal(self, signum: int, frame: Any) -> None:
        del frame
        self.logger.warning("Segnale %s ricevuto: chiusura d'emergenza.", signum)
        try:
            self.after(0, self._emergency_ui_close)
        except (tk.TclError, RuntimeError):
            self.emergency_shutdown()

    def _emergency_ui_close(self) -> None:
        self.emergency_shutdown()
        try:
            self.destroy()
        except tk.TclError:
            pass

    def on_close(self) -> None:
        if self._closing:
            return
        warnings = self._exit_warnings()
        if warnings and not messagebox.askyesno(
            "Uscire da Lectio?",
            "\n\n".join(warnings) + "\n\nUscire comunque?",
            parent=self,
        ):
            return
        self._close_now()

    def _exit_warnings(self) -> list[str]:
        """Lavori in corso che la chiusura interromperebbe."""
        warnings: list[str] = []
        snapshot = self.pomodoro.snapshot()
        if snapshot["status"] in {"running", "paused"} and snapshot["phase"] == "work":
            warnings.append(
                "La sessione di studio corrente verra' salvata come incompleta."
            )
        transcription_job = self.transcriptions.current_job()
        if transcription_job is not None:
            warnings.append(
                "La trascrizione in corso verra' annullata. Il file .txt precedente, "
                "se presente, non verra' modificato."
            )
        pdf_view = self.views.get("pdf")
        if pdf_view is not None and pdf_view.is_busy():
            warnings.append("L'elaborazione PDF in corso verra' interrotta.")
        return warnings

    def _close_now(self) -> None:
        """Chiude Lectio senza altre domande: ferma i servizi e ripristina hosts."""
        if self._closing:
            return
        self._closing = True
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except (tk.TclError, ValueError):
                pass
            self._poll_job = None
        try:
            self.title("Lectio - chiusura e ripristino hosts...")
            self.update_idletasks()
        except tk.TclError:
            pass
        self._shutdown_services()
        try:
            self.destroy()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ aggiornamenti
    def _startup_update_check(self) -> None:
        """All'avvio: pulizia dell'aggiornamento precedente e controllo su GitHub."""
        updater.cleanup_previous()
        try:
            previous = self.db.get_setting(updater.SETTING_LAST_RUN_VERSION, "")
            if previous != APP_VERSION:
                self.db.set_setting(updater.SETTING_LAST_RUN_VERSION, APP_VERSION)
                old, new = updater.parse_version(previous), updater.parse_version(APP_VERSION)
                if old is not None and new is not None and old < new:
                    self.toast(f"Lectio aggiornato alla v{APP_VERSION}.")
        except Exception:
            self.logger.exception("Lettura della versione precedente non riuscita.")
        if os.environ.get("LECTIO_NO_UPDATE_CHECK", "").strip():
            return
        # Un obbligo gia' noto vale anche senza connessione: si blocca subito.
        try:
            cached = updater.cached_update(self.db)
        except Exception:
            self.logger.exception("Lettura dell'ultimo controllo aggiornamenti non riuscita.")
            cached = None
        if cached is not None and cached.required:
            self._apply_update_info(cached, manual=False)
        self.check_for_updates(manual=False)

    def check_for_updates(self, manual: bool = True) -> None:
        """Scarica ``update.json`` in un thread; il risultato torna dalla coda ``post``."""
        if self._update_checking:
            if manual:
                self.toast("Controllo degli aggiornamenti gia' in corso.")
            return
        self._update_checking = True

        def worker() -> None:
            manifest: dict[str, Any] | None = None
            error = ""
            try:
                manifest = updater.fetch_manifest()
            except updater.UpdateError as exc:
                error = str(exc)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Controllo aggiornamenti non riuscito.")
                error = str(exc)
            self.post(lambda: self._on_update_checked(manifest, error, manual))

        threading.Thread(target=worker, name="Lectio-UpdateCheck", daemon=True).start()

    def _on_update_checked(self, manifest: dict[str, Any] | None, error: str, manual: bool) -> None:
        self._update_checking = False
        if manifest is None:
            self.logger.info("Controllo aggiornamenti non riuscito: %s", error)
            if manual:
                messagebox.showwarning("Aggiornamenti", f"Controllo non riuscito:\n{error}", parent=self)
            return
        try:
            updater.remember_manifest(self.db, manifest)
            known_min = self.db.get_setting(updater.SETTING_KNOWN_MIN, "")
        except Exception:
            self.logger.exception("Salvataggio del controllo aggiornamenti non riuscito.")
            known_min = ""
        info = updater.evaluate(manifest, APP_VERSION, known_min)
        if info is None:
            self._update_info = None
            self.update_button.grid_remove()
            self._schedule_nav_layout()
            if manual:
                messagebox.showinfo("Aggiornamenti", f"Lectio v{APP_VERSION} e' aggiornato.", parent=self)
            return
        self._apply_update_info(info, manual)

    def _apply_update_info(self, info: updater.UpdateInfo, manual: bool) -> None:
        if info.required and not updater.is_frozen():
            # Avviato dai sorgenti (sviluppo): niente blocco, solo l'avviso.
            info = dataclasses.replace(info, required=False)
        already_shown = self._update_info is not None and self._update_info.latest == info.latest
        self._update_info = info
        self.update_button.configure(
            text=f"Aggiorna a v{info.latest}",
            fg_color=COLORS.red if info.required else COLORS.accent,
        )
        self.update_button.grid(row=0, column=2, sticky="e", padx=(10, 0), pady=8)
        self._schedule_nav_layout()
        skipped = self.db.get_setting(updater.SETTING_SKIPPED, "")
        if info.required or manual or (not already_shown and skipped != info.latest):
            self.open_update_dialog()

    def open_update_dialog(self) -> None:
        info = self._update_info
        if info is None or self._closing:
            return
        existing = self._update_dialog
        if existing is not None and existing.winfo_exists():
            if existing.busy or existing.info == info:
                existing.lift()
                existing.focus_force()
                return
            existing.close()
        self._update_dialog = UpdateDialog(
            self,
            info,
            can_install=updater.can_self_update(),
            on_update=self._start_update,
            on_skip=self._skip_update,
            on_quit=self.on_close,
        )

    def _skip_update(self, info: updater.UpdateInfo) -> None:
        self.db.set_setting(updater.SETTING_SKIPPED, info.latest)
        self.toast(f"La v{info.latest} non verra' piu' proposta all'avvio.")

    def _start_update(self, dialog: UpdateDialog) -> None:
        info = dialog.info
        if not updater.can_self_update():
            webbrowser.open(info.page)
            return
        warnings = self._exit_warnings()
        if warnings and not messagebox.askyesno(
            "Aggiornare ora?",
            "Per installare l'aggiornamento Lectio si riavvia.\n\n" + "\n\n".join(warnings) + "\n\nContinuare?",
            parent=dialog,
        ):
            return
        dialog.set_busy(True, "Download in corso...")
        exe = updater.current_exe()
        last_report = [0.0]

        def progress(received: int, total: int) -> None:
            now = time.monotonic()
            if now - last_report[0] < 0.25 and received < total:
                return
            last_report[0] = now
            self.post(lambda: dialog.set_progress(received, total))

        def worker() -> None:
            new_exe: Path | None = None
            error = ""
            try:
                new_exe = updater.download(info, exe, progress)
            except updater.UpdateError as exc:
                error = str(exc)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Download dell'aggiornamento non riuscito.")
                error = str(exc) or exc.__class__.__name__
            self.post(lambda: self._install_update(dialog, info, new_exe, error))

        threading.Thread(target=worker, name="Lectio-UpdateDownload", daemon=True).start()

    def _install_update(
        self, dialog: UpdateDialog, info: updater.UpdateInfo, new_exe: Path | None, error: str
    ) -> None:
        if new_exe is None:
            dialog.set_busy(False)
            dialog.set_status(f"Aggiornamento non riuscito: {error}", error=True)
            return
        dialog.set_status("Backup dei dati prima dell'aggiornamento...")
        try:
            dialog.update_idletasks()
            target = self._backup_before_update(info)
            self.logger.info("Backup prima dell'aggiornamento: %s", target)
        except Exception as exc:
            self.logger.exception("Backup prima dell'aggiornamento non riuscito.")
            if not messagebox.askyesno(
                "Backup non riuscito",
                f"Il backup automatico dei dati non e' riuscito:\n{exc}\n\nAggiornare comunque?",
                icon="warning",
                parent=dialog,
            ):
                new_exe.unlink(missing_ok=True)
                dialog.set_busy(False)
                dialog.set_status("Aggiornamento annullato.")
                return
        try:
            updater.launch_finisher(new_exe, updater.current_exe(), updater.processes_to_wait())
        except OSError as exc:
            self.logger.exception("Avvio del nuovo exe non riuscito.")
            dialog.set_busy(False)
            dialog.set_status(f"Avvio della nuova versione non riuscito: {exc}", error=True)
            return
        dialog.set_status("Riavvio di Lectio...")
        dialog.busy = False
        self._close_now()

    def _backup_before_update(self, info: updater.UpdateInfo) -> Path:
        from ..services.backup import export_backup

        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = Path(self.paths["data"]) / "backups" / f"lectio-v{APP_VERSION}-prima-di-v{info.latest}-{stamp}.zip"
        export_backup(self.db, self.paths, target)
        return target

    # ------------------------------------------------------------ sincronizzazione
    def _check_sync_folder(self) -> None:
        """All'avvio: cartella sincronizzata raggiungibile e non aperta su un altro PC."""
        folder = self.paths.get("sync_folder")
        if folder is None:
            return
        self.withdraw()
        while not self.paths["synced"]:
            if messagebox.askretrycancel(
                "Cartella sincronizzata non raggiungibile",
                f"La cartella dei dati sincronizzati non e' raggiungibile:\n{folder}\n\n"
                "Controlla che OneDrive (o il programma di sincronizzazione) sia attivo e la cartella "
                "scaricata, poi premi Riprova.",
                icon="warning", parent=self,
            ):
                self.paths = ensure_data_layout()
                continue
            if messagebox.askyesno(
                "Usare i dati di questo PC?",
                "Aprire Lectio con i dati salvati solo su questo PC?\n\nLe modifiche fatte ora non "
                "finiranno nella cartella sincronizzata. Al prossimo avvio Lectio la cerchera' di nuovo.",
                parent=self,
            ):
                self.deiconify()
                return
            self.destroy()
            raise SystemExit(0)
        conflict = sync.lock_conflict(Path(folder))
        if conflict is not None and not messagebox.askyesno(
            "Lectio e' aperto su un altro PC",
            f"Lectio risulta aperto su «{conflict.get('pc')}» (ultimo segnale alle "
            f"{str(conflict.get('heartbeat'))[11:16]}).\n\nUsa Lectio su un PC alla volta: chiudilo li', "
            "aspetta che la sincronizzazione finisca e riapri qui.\n\nAprire comunque? Le modifiche "
            "fatte contemporaneamente sui due PC potrebbero andare perse.",
            icon="warning", parent=self,
        ):
            self.destroy()
            raise SystemExit(0)
        self.deiconify()

    def _sync_heartbeat(self) -> None:
        """Aggiorna ``lectio.lock`` ogni minuto, cosi' gli altri PC sanno che Lectio e' aperto qui."""
        try:
            sync.write_lock(Path(self.paths["shared"]))
        except OSError:
            self.logger.warning("Aggiornamento di lectio.lock non riuscito.", exc_info=True)
        if not self._closing:
            self._heartbeat_job = self.after(sync.HEARTBEAT_SECONDS * 1000, self._sync_heartbeat)

    def _warn_conflict_copies(self) -> None:
        copies = sync.conflict_copies(Path(self.paths["shared"]))
        if copies:
            messagebox.showwarning(
                "Copie in conflitto",
                "Nella cartella sincronizzata ci sono copie del database create dal programma di "
                "sincronizzazione (Lectio e' stato usato su due PC insieme):\n\n"
                + "\n".join(f"• {path.name}" for path in copies[:5])
                + "\n\nLectio usa study_manager.db. Le copie non vengono toccate: se ti servono dati "
                "che mancano, chiedi aiuto prima di cancellarle.",
                parent=self,
            )

    def emergency_shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._closing = True
        self._shutdown_services()

    def _shutdown_services(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        try:
            self.pomodoro.shutdown()
        except Exception:
            self.logger.exception("Chiusura Pomodoro non riuscita.")
        try:
            self.blocker.shutdown()
        except Exception:
            self.logger.exception("Chiusura blocker non riuscita.")
        try:
            self.audio.shutdown()
        except Exception:
            self.logger.exception("Chiusura audio non riuscita.")
        pdf_view = self.views.get("pdf")
        if pdf_view is not None:
            try:
                pdf_view.shutdown()
            except Exception:
                self.logger.exception("Chiusura modulo PDF non riuscita.")
        job = self.transcriptions.current_job()
        try:
            self.transcriptions.cancel()
        except Exception:
            self.logger.exception("Annullamento trascrizione non riuscito.")
        if job is not None:
            try:
                self.db.set_video_lesson_job(
                    int(job["lesson_id"]),
                    "cancelled",
                    0.0,
                    "Trascrizione interrotta alla chiusura dell'applicazione.",
                )
            except Exception:
                self.logger.exception("Salvataggio interruzione trascrizione non riuscito.")
        try:
            self.transcriptions.shutdown()
        except Exception:
            self.logger.exception("Chiusura trascrizione non riuscita.")
        if self.paths.get("synced"):
            if self._heartbeat_job is not None:
                try:
                    self.after_cancel(self._heartbeat_job)
                except (tk.TclError, ValueError):
                    pass
            try:
                sync.write_snapshot(self.db, self.paths)
            except Exception:
                self.logger.exception("Snapshot di chiusura non riuscito.")
        try:
            self.db.close_all()
        except Exception:
            self.logger.exception("Chiusura database non riuscita.")
        if self.paths.get("synced"):
            try:
                sync.release_lock(Path(self.paths["shared"]))
            except OSError:
                self.logger.warning("Rimozione di lectio.lock non riuscita.", exc_info=True)


def run_app(debug: bool = False, smoke_test: bool = False) -> int:
    setup_logging(debug=debug)
    LOGGER.info("Avvio Lectio %s", APP_VERSION)
    app = LectioApp()
    import atexit

    atexit.register(app.emergency_shutdown)
    if smoke_test:
        return app.run_smoke_test()
    app.mainloop()
    return 0
