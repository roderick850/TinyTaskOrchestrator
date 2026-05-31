"""
TinyTask Orchestrator — Mini Bar
================================
Compacta ventana always-on-top que muestra el progreso de ejecución.
Ideal para gaming en monitores únicos — no tapa el juego.
Se puede arrastrar a cualquier esquina y fijar/desfijar.
Al cerrar (X) se oculta; se restaura desde el menú View.
"""

import tkinter as tk
from tkinter import ttk
import os
import ctypes

# ── Dark Theme (mismos colores que la app principal) ──────────────
DARK_COLORS = {
    "bg":           "#1e1e2e",
    "surface":      "#282840",
    "surface_alt":  "#313148",
    "border":       "#3b3b56",
    "text":         "#cdd6f4",
    "text_dim":     "#8b8da8",
    "accent":       "#7c7cf8",
    "accent_hover": "#9696ff",
    "green":        "#5cce8e",
    "red":          "#e06070",
    "yellow":       "#e0b860",
    "blue":         "#6090e0",
    "purple":       "#b090e0",
}


def _apply_dark_titlebar(toplevel, retries=5):
    """Dark title bar on Windows 10/11 with retry logic.
    Also forces the window to redraw so the dark mode takes effect."""
    if os.name != "nt":
        return
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    for attempt in range(retries):
        try:
            toplevel.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(toplevel.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(1)),
                ctypes.sizeof(ctypes.c_int(1)),
            )
            # Force redraw
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                0x0002 | 0x0001
            )
            break
        except Exception:
            if attempt < retries - 1:
                import time
                time.sleep(0.1)
    try:
        toplevel.update_idletasks()
        hwnd2 = toplevel.winfo_id()
        for attr in (19, 20):
            try:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd2, attr,
                    ctypes.byref(ctypes.c_int(1)),
                    ctypes.sizeof(ctypes.c_int(1)),
                )
            except Exception:
                pass
    except Exception:
        pass


def format_time(seconds):
    """Formato compacto para el mini bar."""
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    if m > 0:
        return f"{m}:{s:02d}"
    return f"0:{s:02d}"


class MiniBar:
    """Barra compacta always-on-top con progreso de ejecución."""

    def __init__(self, app, settings=None):
        """
        Args:
            app: referencia a OrchestratorApp (necesita .root, ._stop(), .is_running)
            settings: dict con 'mini_bar_geometry', 'mini_bar_pinned', etc.
        """
        self.app = app
        self.settings = settings or {}

        # Crear Toplevel independiente
        self.root = tk.Toplevel(app.root)
        self.root.title("TinyTask — Mini")
        self.root.configure(bg=DARK_COLORS["bg"])
        self.root.minsize(300, 36)
        self.root.maxsize(1200, 36)
        self.root.resizable(True, False)

        # Always-on-top por defecto, configurable
        pinned = self.settings.get("mini_bar_pinned", True)
        self.root.attributes("-topmost", pinned)

        # Restaurar geometría guardada
        saved_geo = self.settings.get("mini_bar_geometry", "450x36")
        try:
            self.root.geometry(saved_geo)
        except tk.TclError:
            self.root.geometry("450x36")

        # Dark title bar — apply after window is realized
        self.root.after(100, lambda: _apply_dark_titlebar(self.root, retries=3))

        # Opacity control - mouse wheel over the bar
        self._opacity = self.settings.get("mini_bar_opacity", 1.0)
        self.root.attributes("-alpha", self._opacity)
        # Bind to Toplevel covers all child widgets via bind tags
        self.root.bind("<MouseWheel>", self._on_mousewheel)

        # ── Construir UI ──
        self._build_ui(pinned)

        # ── Al cerrar, ocultar en vez de destruir ──
        self.root.protocol("WM_DELETE_WINDOW", self.hide)

        # ── Guardar geometría al cambiar tamaño ──
        self.root.bind("<Configure>", self._on_configure)

        self._visible = True

    # ── UI ─────────────────────────────────────────────────────────

    def _build_ui(self, pinned):
        c = DARK_COLORS

        bar = tk.Frame(self.root, bg=c["bg"], height=30)
        bar.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)
        bar.pack_propagate(False)

        # ── Paso actual (label que se trunca automáticamente) ──
        self.step_label = tk.Label(
            bar, text="Listo",
            fg=c["text"], bg=c["bg"],
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.step_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))

        # ── Mini barra de progreso ──
        style = ttk.Style()
        style.configure("Mini.Horizontal.TProgressbar",
                        background=c["green"], troughcolor=c["surface_alt"],
                        borderwidth=0, thickness=6)
        self.progress = ttk.Progressbar(
            bar, orient=tk.HORIZONTAL, mode="determinate",
            style="Mini.Horizontal.TProgressbar", length=70,
        )
        self.progress.pack(side=tk.LEFT, padx=2)

        # ── Tiempo (script countdown | total sesión) ──
        self.time_label = tk.Label(
            bar, text="--:--",
            fg=c["text_dim"], bg=c["bg"],
            font=("Segoe UI", 8),
            width=16,
        )
        self.time_label.pack(side=tk.LEFT, padx=3)

        # ── Botón Detener ──
        self.stop_btn = tk.Button(
            bar, text="⏹", command=self._on_stop,
            bg=c["surface_alt"], fg=c["text_dim"],
            font=("Segoe UI", 10, "bold"),
            borderwidth=0, width=2, height=1,
            cursor="hand2",
            activebackground=c["red"],
            state="disabled",
        )
        self.stop_btn.pack(side=tk.LEFT, padx=1)

        # ── Botón Pin (always-on-top toggle) ──
        self._pinned = pinned
        self.pin_btn = tk.Button(
            bar, text="📌", command=self._toggle_pin,
            bg=c["surface"] if pinned else c["bg"],
            fg=c["text"] if pinned else c["text_dim"],
            font=("Segoe UI", 8),
            borderwidth=0, width=2, height=1,
            cursor="hand2",
        )
        self.pin_btn.pack(side=tk.LEFT, padx=1)

        # ── Botón restaurar ventana principal ──
        self.restore_btn = tk.Button(
            bar, text="⬆", command=self._restore_main,
            bg=c["surface"], fg=c["text_dim"],
            font=("Segoe UI", 8),
            borderwidth=0, width=2, height=1,
            cursor="hand2",
        )
        self.restore_btn.pack(side=tk.LEFT, padx=1)

    # ── Actualización desde la app ─────────────────────────────────

    def update(self, status_text, progress_val, progress_max, time_text, is_running):
        """Llamado desde el callback de ejecución para refrescar la UI.
        
        Args:
            status_text: texto del paso actual (max ~50 chars)
            progress_val: valor actual de progreso
            progress_max: valor máximo de progreso
            time_text: string de tiempo (ej: "5:23")
            is_running: bool, True = ejecutando, False = idle/completado
        """
        # Truncar texto largo
        if len(status_text) > 50:
            status_text = status_text[:47] + "..."

        self.step_label.config(text=status_text)

        if progress_max > 0:
            self.progress["maximum"] = progress_max
            self.progress["value"] = min(progress_val, progress_max)
        else:
            self.progress["maximum"] = 1
            self.progress["value"] = 0

        self.time_label.config(text=time_text)

        # Estado del botón stop
        if is_running:
            self.stop_btn.config(
                bg=DARK_COLORS["red"], fg="#ffffff", state="normal",
            )
        else:
            self.stop_btn.config(
                bg=DARK_COLORS["surface_alt"], fg=DARK_COLORS["text_dim"],
                state="disabled",
            )

    def reset(self):
        """Volver a estado idle."""
        self.update("Listo", 0, 1, "--:--", False)

    # ── Acciones ───────────────────────────────────────────────────

    def _on_mousewheel(self, event):
        """Ajustar opacidad con rueda del mouse (5% por paso)."""
        delta = 0.05 if event.delta > 0 else -0.05
        self._set_opacity(self._opacity + delta)

    def _set_opacity(self, value):
        """Aplicar y guardar opacidad (min 15%, max 100%)."""
        self._opacity = round(max(0.15, min(1.0, value)), 2)
        self.root.attributes("-alpha", self._opacity)

    def _on_stop(self):
        self.app._stop()

    def _toggle_pin(self):
        self._pinned = not self._pinned
        self.root.attributes("-topmost", self._pinned)
        self.pin_btn.config(
            bg=DARK_COLORS["surface"] if self._pinned else DARK_COLORS["bg"],
            fg=DARK_COLORS["text"] if self._pinned else DARK_COLORS["text_dim"],
        )

    def _restore_main(self):
        """Mostrar la ventana principal."""
        self.app.root.deiconify()
        self.app.root.lift()

    def _on_configure(self, event):
        """Guardar geometría solo en cambios de tamaño (no posición)."""
        pass  # La geometría se guarda al cerrar la app

    # ── Visibilidad ────────────────────────────────────────────────

    def show(self):
        """Mostrar/restaurar el mini bar."""
        self.root.deiconify()
        self.root.lift()
        self._visible = True

    def hide(self):
        """Ocultar el mini bar (sin destruir)."""
        self.root.withdraw()
        self._visible = False

    def toggle(self):
        """Alternar visibilidad."""
        if self._visible:
            self.hide()
        else:
            self.show()
        return self._visible

    def is_visible(self):
        return self._visible

    def close(self):
        """Destruir definitivamente."""
        self.root.destroy()

    def get_settings(self):
        """Devolver settings para guardar en config."""
        return {
            "mini_bar_geometry": self.root.geometry(),
            "mini_bar_pinned": self._pinned,
            "mini_bar_opacity": self._opacity,
        }
