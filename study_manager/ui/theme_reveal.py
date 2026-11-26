"""Animazione del cambio tema: il nuovo tema si allarga a cerchio dal basso a sinistra.

Prima del cambio si fotografa la finestra e la foto viene mostrata in una
finestra sovrapposta, esclusa dalle catture dello schermo
(``SetWindowDisplayAffinity``). Sotto di essa l'app si ricolora; poi si
fotografa il nuovo aspetto e si anima il passaggio fra le due immagini.
"""

from __future__ import annotations

import ctypes
import logging
import math
import time
import tkinter as tk

LOGGER = logging.getLogger(__name__)

_WDA_EXCLUDEFROMCAPTURE = 0x11  # Windows 10 2004+
DURATION = 0.55


class ThemeReveal:
    @classmethod
    def capture(cls, app: tk.Misc) -> "ThemeReveal | None":
        """Copre la finestra con la sua foto; None se non e' possibile (si cambia senza animazione)."""
        try:
            return cls(app)
        except Exception:
            LOGGER.debug("Animazione del cambio tema non disponibile.", exc_info=True)
            return None

    def __init__(self, app: tk.Misc):
        from PIL import ImageGrab, ImageTk

        self._grab = ImageGrab.grab
        app.update_idletasks()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        self.box = (x, y, x + w, y + h)
        self.old = self._grab(self.box, all_screens=True)
        self.top = tk.Toplevel(app)
        try:
            self.top.overrideredirect(True)
            self.top.geometry(f"{w}x{h}+{x}+{y}")
            self.top.attributes("-topmost", True)
            self.photo = ImageTk.PhotoImage(self.old)
            tk.Label(self.top, image=self.photo, bd=0, highlightthickness=0).pack(fill="both", expand=True)
            self.top.update()
            hwnd = ctypes.windll.user32.GetParent(self.top.winfo_id())
            if not ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, _WDA_EXCLUDEFROMCAPTURE):
                raise OSError("SetWindowDisplayAffinity non riuscita")
        except Exception:
            self.top.destroy()
            raise

    def play(self, app: tk.Misc) -> None:
        """Fotografa il nuovo tema (sotto la copertura) e lo rivela a cerchio."""
        from PIL import Image, ImageDraw

        try:
            app.update()
            new = self._grab(self.box, all_screens=True)
        except Exception:
            LOGGER.debug("Cattura del nuovo tema non riuscita.", exc_info=True)
            self.top.destroy()
            return
        w, h = self.old.size
        radius_max = math.hypot(w, h)
        started = time.perf_counter()

        def frame() -> None:
            try:
                t = min(1.0, (time.perf_counter() - started) / DURATION)
                if t >= 1.0:
                    self.top.destroy()
                    return
                eased = 1 - (1 - t) ** 3  # ease-out: parte veloce e rallenta
                r = radius_max * eased
                mask = Image.new("L", (w, h), 0)
                ImageDraw.Draw(mask).ellipse((-r, h - r, r, h + r), fill=255)  # centro: angolo in basso a sinistra
                self.photo.paste(Image.composite(new, self.old, mask))
                self.top.after(1, frame)
            except tk.TclError:
                pass

        frame()
