"""Costanti, default e palette dell'applicazione."""

from __future__ import annotations

from dataclasses import dataclass


APP_NAME = "Lectio"
APP_TITLE = "Lectio - Gestionale Studio & Analytics"
APP_VERSION = "1.5.0"

DEFAULT_SETTINGS: dict[str, str] = {
    "pomodoro_work": "25",
    "pomodoro_short": "5",
    "pomodoro_long": "15",
    "pomodoro_cycles": "4",
    "auto_start_next": "1",
    "notification_sound": "1",
    "block_sites_enabled": "1",
    "block_apps_enabled": "1",
    "terminate_blocked_apps": "1",
    "web_trap_enabled": "1",
    "app_scan_interval": "2.0",
    "audio_autoplay": "0",
    "audio_track": "",
    "audio_volume": "0.55",
    "audio_loop": "1",
    "transcription_model": "small-q5_1",
    "transcription_language": "it",
    # Google Calendar (sincronizzazione bidirezionale)
    "google_sync_past_days": "180",
    "google_sync_future_days": "365",
    "google_auto_create_calendar": "0",
    "google_enabled_calendars": "[]",
    "ical_feeds": "[]",
    "ui_theme": "dark",
}

# Impostazioni con segreti OAuth: cifrate con DPAPI, mai esportate nel backup
# ZIP e mai scritte nei log.
GOOGLE_SECRET_SETTINGS: tuple[str, ...] = (
    "google_access_token_enc",
    "google_refresh_token_enc",
    "google_client_secret_enc",
)
# Stato dell'account legato a questo PC: anche questo resta fuori dal backup,
# altrimenti un ripristino su un altro PC sembrerebbe "connesso" senza token.
GOOGLE_ACCOUNT_SETTINGS: tuple[str, ...] = GOOGLE_SECRET_SETTINGS + (
    "google_client_id",
    "google_token_expiry",
    "google_account_email",
    # Gli indirizzi iCal segreti danno accesso in lettura al calendario: sono
    # cifrati come i token e non finiscono nel backup.
    "ical_feed_urls_enc",
)

DEFAULT_BLOCKED_SITES: tuple[str, ...] = (
    "youtube.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "reddit.com",
    "netflix.com",
    "twitch.tv",
)

DEFAULT_BLOCKED_APPS: tuple[tuple[str, str], ...] = (
    ("Discord", "discord"),
    ("Steam", "steam"),
    ("Telegram", "telegram"),
    ("WhatsApp", "whatsapp"),
    ("Epic Games Launcher", "epicgameslauncher"),
    ("TikTok", "tiktok"),
)

HOSTS_BEGIN_MARKER = "# >>> LECTIO MANAGED BLOCK - DO NOT EDIT >>>"
HOSTS_END_MARKER = "# <<< LECTIO MANAGED BLOCK <<<"
# Marcatori usati prima del cambio nome: una sezione residua va comunque rimossa.
LEGACY_HOSTS_MARKERS: tuple[tuple[str, str], ...] = (
    ("# >>> STUDYFLOW MANAGED BLOCK - DO NOT EDIT >>>", "# <<< STUDYFLOW MANAGED BLOCK <<<"),
)
HOSTS_ENTRY_IP = "127.0.0.1"

TRAP_PORTS: tuple[int, ...] = (80, 443)
PROCESS_LOG_COOLDOWN_SECONDS = 45.0
WEB_LOG_COOLDOWN_SECONDS = 10.0


@dataclass(frozen=True)
class Palette:
    # Tema scuro in stile X ("Lights out"): nero puro, pannelli grafite con bordo
    # sottile e accento blu. Ogni valore deve restare unico: il cambio tema
    # converte i colori per valore (``ui.theme.color_map``).
    bg: str = "#000000"
    sidebar: str = "#010101"
    surface: str = "#16181C"
    surface_alt: str = "#1D1F23"
    surface_hover: str = "#25282D"
    border: str = "#2F3336"
    text: str = "#E7E9EA"
    text_muted: str = "#71767B"
    text_faint: str = "#536471"
    accent: str = "#1D9BF0"
    accent_hover: str = "#1A8CD8"
    accent_dark: str = "#0E4F7C"
    blue: str = "#38BDF8"
    amber: str = "#FFD400"
    red: str = "#F4212E"
    red_dark: str = "#7F1D1D"
    green: str = "#00BA7C"
    purple: str = "#A78BFA"
    chart_grid: str = "#202327"


# Palette di riferimento dei due temi. ``COLORS`` e' l'istanza usata dalle viste:
# ``ui.theme.apply_palette`` ne aggiorna i campi quando si cambia tema, cosi'
# i widget creati dopo il cambio usano gia' i colori giusti.
DARK_PALETTE = Palette()
LIGHT_PALETTE = Palette(
    bg="#EEF2F7",
    sidebar="#FEFEFE",
    surface="#FBFCFE",
    surface_alt="#F1F5F9",
    surface_hover="#E3E9F1",
    border="#CDD6E2",
    text="#0F172A",
    text_muted="#475569",
    text_faint="#64748B",
    accent="#0D9488",
    accent_hover="#0F766E",
    accent_dark="#9FE7D6",
    blue="#0284C7",
    amber="#B45309",
    red="#DC2626",
    red_dark="#FECACA",
    green="#16A34A",
    purple="#7C3AED",
    chart_grid="#E5EAF1",
)

COLORS = Palette()

PHASE_LABELS = {
    "work": "Lavoro",
    "short_break": "Pausa breve",
    "long_break": "Pausa lunga",
}

STATUS_LABELS = {
    "idle": "Pronto",
    "running": "In corso",
    "paused": "In pausa",
}

FONT_FAMILY = "Segoe UI"
MONO_FONT_FAMILY = "Consolas"


def quality_score(stats: dict) -> float:
    """Quality Score 0-100.

    Componenti:
      - 35% completamento dei pomodori avviati;
      - 40% pomodori completati senza alcuna distrazione;
      - 25% attenzione, penalizzata dalle interruzioni per ora di studio.

    La formula premia studio effettivo e sessioni pulite; non dipende da dati
    inventati e restituisce 0 quando non esiste storico nel periodo.
    """
    started = int(stats.get("started", 0) or 0)
    completed = int(stats.get("completed", 0) or 0)
    clean = int(stats.get("clean_completed", 0) or 0)
    minutes = int(stats.get("minutes", 0) or 0)
    attempts = int(stats.get("attempts", 0) or 0)
    if started <= 0:
        return 0.0

    completion_rate = min(1.0, completed / started)
    clean_rate = (clean / completed) if completed else 0.0
    hours = max(minutes / 60.0, 0.25)
    interruptions_per_hour = attempts / hours
    attention = max(0.0, 100.0 - interruptions_per_hour * 9.0)
    score = (
        completion_rate * 35.0
        + clean_rate * 40.0
        + (attention / 100.0) * 25.0
    )
    return round(max(0.0, min(100.0, score)), 1)
