import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import subprocess
import threading
import time
import ctypes

from config_manager import load_config, save_config, DEFAULT_SETTINGS
from executor import Executor
from hotkey import HotkeyListener
from mini_bar import MiniBar, format_time as mini_format_time


def format_time(seconds):
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


# ── Dark Theme Colors ──────────────────────────────────────────────
DARK_COLORS = {
    "bg":           "#1e1e2e",   # main background
    "surface":      "#282840",   # frames, cards
    "surface_alt":  "#313148",   # alternate surface (treeview rows)
    "border":       "#3b3b56",   # subtle borders
    "text":         "#cdd6f4",   # primary text
    "text_dim":     "#8b8da8",   # secondary text
    "accent":       "#7c7cf8",   # buttons, highlights
    "accent_hover": "#9696ff",   # hover state
    "green":        "#5cce8e",   # success / ready
    "green_dim":    "#3a8a5e",   # darker green
    "red":          "#e06070",   # stop / error
    "yellow":       "#e0b860",   # warning
    "blue":         "#6090e0",   # running
    "purple":       "#b090e0",   # waiting
    "menu_bg":      "#252538",   # menu bar background
    "menu_fg":      "#cdd6f4",   # menu bar text
    "menu_active":  "#3b3b56",   # menu hover
}


def _apply_dark_titlebar(hwnd):
    if os.name != "nt":
        return
    try:
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(ctypes.c_int(1)),
            ctypes.sizeof(ctypes.c_int(1)),
        )
    except Exception:
        pass


class OrchestratorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TinyTask Orchestrator")
        self.root.minsize(600, 380)
        self.root.configure(bg=DARK_COLORS["bg"])

        # Dark title bar on Windows
        self.root.update_idletasks()
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            _apply_dark_titlebar(hwnd)
        except Exception:
            pass

        # Estado
        config = load_config()
        self.playlist = config["playlist"]
        self.settings = config["settings"]

        # Restore saved window geometry, or use default
        saved_geometry = self.settings.get("window_geometry", "")
        if saved_geometry:
            try:
                self.root.geometry(saved_geometry)
            except tk.TclError:
                self.root.geometry("750x500")
        else:
            self.root.geometry("750x500")
        self.executor_thread = None
        self.stop_event = threading.Event()
        self.launch_event = threading.Event()
        self.is_running = False

        # Hotkey global configurable (toggles: start all / stop)
        self.saved_hotkey = self.settings.get("hotkey", "f10")
        self.hotkey = HotkeyListener()
        self.hotkey.start(self.saved_hotkey, self._hotkey_toggle)
        self.hotkey_var_set_to = self.saved_hotkey.upper()

        # Setup dark theme before building UI
        self._setup_dark_theme()

        # ── Mini Bar state (must be before _build_menu) ──
        self.mini_bar = None
        self._mini_bar_enabled = self.settings.get("mini_bar_enabled", True)

        # ── Menu Bar ──
        self._build_menu()

        # Construir UI
        self._build_ui()
        self._refresh_list()
        self._update_time_labels()

        # Guardar al cerrar
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ═══════════════════════════════════════════════════════════════
    # MENU BAR
    # ═══════════════════════════════════════════════════════════════

    def _build_menu(self):
        c = DARK_COLORS
        menubar = tk.Menu(self.root, bg=c["menu_bg"], fg=c["menu_fg"],
                          activebackground=c["menu_active"], activeforeground="#ffffff",
                          borderwidth=0, font=("Segoe UI", 9))
        self.root.config(menu=menubar)

        # ── File ──
        file_menu = tk.Menu(menubar, tearoff=0,
                            bg=c["menu_bg"], fg=c["menu_fg"],
                            activebackground=c["menu_active"], activeforeground="#ffffff",
                            font=("Segoe UI", 9))
        file_menu.add_command(label="💾 Guardar playlist", command=self._menu_save,
                              accelerator="Ctrl+S")
        file_menu.add_separator()
        file_menu.add_command(label="🚪 Salir", command=self._on_close, accelerator="Alt+F4")
        menubar.add_cascade(label="Archivo", menu=file_menu)

        # ── View ──
        view_menu = tk.Menu(menubar, tearoff=0,
                            bg=c["menu_bg"], fg=c["menu_fg"],
                            activebackground=c["menu_active"], activeforeground="#ffffff",
                            font=("Segoe UI", 9))
        self._mini_bar_var = tk.BooleanVar(value=self._mini_bar_enabled)
        view_menu.add_checkbutton(label="📊 Mini Bar siempre visible",
                                  variable=self._mini_bar_var,
                                  command=self._toggle_mini_bar)
        view_menu.add_separator()
        view_menu.add_command(label="🪟 Restaurar tamaño", command=self._menu_reset_size)
        menubar.add_cascade(label="Ver", menu=view_menu)

        # ── Help ──
        help_menu = tk.Menu(menubar, tearoff=0,
                            bg=c["menu_bg"], fg=c["menu_fg"],
                            activebackground=c["menu_active"], activeforeground="#ffffff",
                            font=("Segoe UI", 9))
        help_menu.add_command(label="ℹ️ Acerca de TinyTask Orchestrator",
                              command=self._menu_about)
        menubar.add_cascade(label="Ayuda", menu=help_menu)

        # Ctrl+S shortcut
        self.root.bind_all("<Control-s>", lambda e: self._menu_save())

    def _menu_save(self):
        """Guardar playlist actual."""
        settings = self._gather_settings()
        save_config(self.playlist, settings)
        self._dark_dialog("Guardado", "Playlist y configuración guardadas.", "success")

    def _menu_reset_size(self):
        """Restaurar tamaño default."""
        self.root.geometry("750x500")
        self._dark_dialog("Tamaño", "Ventana restaurada a 750×500.", "info")

    def _menu_about(self):
        """Mostrar diálogo Acerca de."""
        msg = (
            "TinyTask Orchestrator\n\n"
            "Automatización de tareas con ejecución\n"
            "por tiempos fijos, loops y hotkeys globales.\n\n"
            "Modo Mini Bar para gaming en monitor único.\n\n"
            "Creado por Roderick + Hefesto 🛠️"
        )
        self._dark_dialog("Acerca de", msg, "info")

    def _toggle_mini_bar(self):
        """Activar/desactivar Mini Bar desde el menú."""
        enabled = self._mini_bar_var.get()
        self._mini_bar_enabled = enabled
        if enabled:
            if self.mini_bar is None:
                self._create_mini_bar()
            self.mini_bar.show()
        else:
            if self.mini_bar is not None:
                self.mini_bar.hide()

    def _create_mini_bar(self):
        """Crear la Mini Bar si no existe."""
        if self.mini_bar is not None:
            return
        self.mini_bar = MiniBar(self, self.settings)
        self.mini_bar.root.lift()

    def _ensure_mini_bar(self):
        """Asegurar que la mini bar existe y está visible."""
        if self.mini_bar is None:
            self._create_mini_bar()
        if not self.mini_bar.is_visible():
            self.mini_bar.show()

    # ═══════════════════════════════════════════════════════════════
    # DARK THEME (sin cambios de lógica, solo colores)
    # ═══════════════════════════════════════════════════════════════

    def _setup_dark_theme(self):
        """Configure ttk styles for a compact dark theme (clam-based)."""
        style = ttk.Style()
        style.theme_use("clam")

        c = DARK_COLORS

        # ── Global defaults ──
        style.configure(".", background=c["bg"], foreground=c["text"],
                        font=("Segoe UI", 9), borderwidth=0)

        # ── Frame ──
        style.configure("TFrame", background=c["bg"])
        style.configure("Dark.TFrame", background=c["surface"])

        # ── LabelFrame ──
        style.configure("TLabelframe", background=c["bg"], foreground=c["text_dim"],
                        bordercolor=c["border"], borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["text_dim"],
                        font=("Segoe UI", 9))

        # ── Label ──
        style.configure("TLabel", background=c["bg"], foreground=c["text"],
                        font=("Segoe UI", 9))
        style.configure("Dark.TLabel", background=c["surface"], foreground=c["text"],
                        font=("Segoe UI", 9))
        style.configure("Dim.TLabel", foreground=c["text_dim"], font=("Segoe UI", 9))
        style.configure("Bold.TLabel", foreground=c["text"], font=("Segoe UI", 9, "bold"))

        # ── Button ──
        style.configure("TButton", background=c["accent"], foreground="#ffffff",
                        borderwidth=0, focusthickness=0, relief="flat",
                        padding=(8, 3), font=("Segoe UI", 9))
        style.map("TButton",
                  background=[("active", c["accent_hover"]),
                              ("disabled", c["surface_alt"])],
                  foreground=[("disabled", c["text_dim"])])

        # ── Entry ──
        style.configure("TEntry", fieldbackground=c["surface_alt"],
                        foreground=c["text"], borderwidth=1,
                        bordercolor=c["border"], relief="solid",
                        padding=(4, 2), insertcolor=c["text"])

        # ── Combobox ──
        style.configure("TCombobox", fieldbackground=c["surface_alt"],
                        background=c["surface_alt"], foreground=c["text"],
                        arrowcolor=c["text"], borderwidth=1,
                        bordercolor=c["border"], relief="solid",
                        padding=(4, 2))
        style.map("TCombobox",
                  fieldbackground=[("readonly", c["surface_alt"]),
                                   ("disabled", c["surface"])],
                  background=[("readonly", c["surface_alt"])],
                  foreground=[("readonly", c["text"]),
                              ("disabled", c["text_dim"])])
        self.root.option_add("*TCombobox*Listbox.background", c["surface_alt"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 9))

        # ── Treeview ──
        style.configure("Treeview", background=c["surface"],
                        foreground=c["text"], fieldbackground=c["surface"],
                        borderwidth=1, bordercolor=c["border"],
                        relief="solid", rowheight=22)
        style.configure("Treeview.Heading", background=c["surface_alt"],
                        foreground=c["text"], font=("Segoe UI", 8, "bold"),
                        borderwidth=0, relief="flat", padding=(4, 2))
        style.map("Treeview",
                  background=[("selected", c["accent"])],
                  foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading",
                  background=[("active", c["border"])])

        # ── Scrollbar ──
        style.configure("TScrollbar", background=c["bg"],
                        troughcolor=c["surface_alt"], borderwidth=0,
                        arrowsize=12, arrowcolor=c["text_dim"])
        style.map("TScrollbar",
                  background=[("active", c["border"])])

        # ── Progressbar ──
        style.configure("TProgressbar", background=c["green"],
                        troughcolor=c["surface_alt"], borderwidth=0,
                        thickness=8)

        # ── Spinbox ──
        style.configure("TSpinbox", fieldbackground=c["surface_alt"],
                        foreground=c["text"], borderwidth=1,
                        bordercolor=c["border"], relief="solid",
                        padding=(4, 2), arrowcolor=c["text"],
                        insertcolor=c["text"])

        # ── Compact variants ──
        style.configure("Compact.TButton", padding=(5, 1), font=("Segoe UI", 8))
        style.configure("Compact.TLabel", font=("Segoe UI", 8))
        style.configure("Compact.TEntry", padding=(2, 1), font=("Segoe UI", 8))

    def _build_ui(self):
        c = DARK_COLORS

        # ===== Frame Configuración del Loop (compacto) =====
        loop_frame = ttk.LabelFrame(self.root, text=" Loop ", padding=5)
        loop_frame.pack(fill=tk.X, padx=5, pady=(5, 3))

        ttk.Label(loop_frame, text="Modo:", style="Compact.TLabel").pack(side=tk.LEFT, padx=(0, 3))
        self.loop_mode_var = tk.StringVar(value=self.settings.get("loop_mode", "once"))
        mode_combo = ttk.Combobox(
            loop_frame,
            textvariable=self.loop_mode_var,
            values=["once", "fixed", "infinite"],
            width=10,
            state="readonly",
        )
        mode_combo.pack(side=tk.LEFT, padx=2)
        mode_combo.bind("<<ComboboxSelected>>", self._on_loop_mode_change)

        ttk.Label(loop_frame, text="×", style="Compact.TLabel").pack(side=tk.LEFT, padx=(8, 3))
        self.loop_count_var = tk.StringVar(value=str(self.settings.get("loop_count", 1)))
        self.loop_count_entry = ttk.Entry(loop_frame, textvariable=self.loop_count_var, width=6, validate="key")
        self.loop_count_entry.config(validatecommand=(self.root.register(self._validate_int_positive), "%P"))
        self.loop_count_entry.pack(side=tk.LEFT, padx=2)

        ttk.Label(loop_frame, text="Pausa:", style="Compact.TLabel").pack(side=tk.LEFT, padx=(10, 3))
        self.loop_delay_var = tk.StringVar(value=str(self.settings.get("loop_delay", 0)))
        self.loop_delay_entry = ttk.Entry(loop_frame, textvariable=self.loop_delay_var, width=5, validate="key")
        self.loop_delay_entry.config(validatecommand=(self.root.register(self._validate_int_non_negative), "%P"))
        self.loop_delay_entry.pack(side=tk.LEFT, padx=2)
        ttk.Label(loop_frame, text="s", style="Dim.TLabel").pack(side=tk.LEFT)

        # Tiempo estimado total
        self.total_time_label = ttk.Label(loop_frame, text="Total: 0s", style="Dim.TLabel")
        self.total_time_label.pack(side=tk.RIGHT, padx=5)

        self._on_loop_mode_change(None)

        # ===== Frame lista (principal, expande) =====
        list_frame = ttk.Frame(self.root)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=3)

        columns = ("orden", "hab", "nombre", "reps", "duracion", "pausa", "tiempo")
        self.tree = ttk.Treeview(
            list_frame, columns=columns, show="headings", selectmode="browse"
        )
        self.tree.heading("orden", text="#")
        self.tree.heading("hab", text="✓")
        self.tree.heading("nombre", text="Script")
        self.tree.heading("reps", text="Reps")
        self.tree.heading("duracion", text="Dur (s)")
        self.tree.heading("pausa", text="Pausa (s)")
        self.tree.heading("tiempo", text="Tiempo")

        self.tree.column("orden", width=28, anchor="center")
        self.tree.column("hab", width=26, anchor="center")
        self.tree.column("nombre", width=200, anchor="w")
        self.tree.column("reps", width=55, anchor="center")
        self.tree.column("duracion", width=60, anchor="center")
        self.tree.column("pausa", width=60, anchor="center")
        self.tree.column("tiempo", width=75, anchor="center")

        # Click on checkbox column toggles enabled/disabled
        self.tree.bind("<ButtonRelease-1>", self._on_tree_click)
        # Double-click on editable columns for inline editing
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self._inline_entry = None

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # ===== Frame botones (compacto) =====
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=5, pady=(0, 3))

        ttk.Button(btn_frame, text="➕ Agregar", command=self._add_script, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_frame, text="✏️ Editar", command=self._edit_script, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_frame, text="🗑️ Quitar", command=self._remove_script, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_frame, text="⬆", command=self._move_up, style="Compact.TButton", width=3).pack(
            side=tk.LEFT, padx=(8, 1)
        )
        ttk.Button(btn_frame, text="⬇", command=self._move_down, style="Compact.TButton", width=3).pack(
            side=tk.LEFT, padx=1
        )

        # ===== Frame ejecución (compacto) =====
        exec_frame = ttk.LabelFrame(self.root, text=" Ejecución ", padding=5)
        exec_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        # Status visual con colores sobre fondo oscuro
        self.status_label = tk.Label(
            exec_frame,
            text=" LISTO ",
            font=("Segoe UI", 9, "bold"),
            fg="#ffffff",
            bg=c["green"],
            padx=8,
            pady=2,
        )
        self.status_label.pack(anchor=tk.W, pady=(0, 3))

        # Progress bar + percentage label
        progress_frame = ttk.Frame(exec_frame)
        progress_frame.pack(fill=tk.X, pady=2)

        self.progress = ttk.Progressbar(
            progress_frame, orient=tk.HORIZONTAL, mode="determinate"
        )
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.progress_pct_label = ttk.Label(progress_frame, text="0%", width=5, style="Compact.TLabel")
        self.progress_pct_label.pack(side=tk.LEFT, padx=(3, 0))

        # Botones de acción
        ttk.Button(exec_frame, text="▶ Iniciar", command=self._start, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(exec_frame, text="▶1 Seleccionado", command=self._run_selected, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(exec_frame, text="⏹ Detener", command=self._stop, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )

        # Hotkey configurable
        ttk.Label(exec_frame, text="Hotkey:", style="Compact.TLabel").pack(side=tk.LEFT, padx=(10, 3))
        self.hotkey_var = tk.StringVar(value=self.hotkey_var_set_to)
        hotkey_combo = ttk.Combobox(
            exec_frame,
            textvariable=self.hotkey_var,
            values=["F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"],
            width=4,
            state="readonly",
        )
        hotkey_combo.pack(side=tk.LEFT, padx=2)
        ttk.Label(exec_frame, text="(solo ▶ Iniciar todo / ⏹ Detener)", style="Dim.TLabel").pack(side=tk.LEFT, padx=(3, 0))
        hotkey_combo.bind("<<ComboboxSelected>>", self._on_hotkey_change)

        # Countdown timer
        self.countdown_label = ttk.Label(exec_frame, text="⏱ --:--", style="Bold.TLabel")
        self.countdown_label.pack(side=tk.RIGHT, padx=5)

    # ═══════════════════════════════════════════════════════════════
    # HOTKEY
    # ═══════════════════════════════════════════════════════════════

    def _on_hotkey_change(self, event):
        new_key = self.hotkey_var.get().lower()
        self.hotkey.stop()
        self.hotkey.start(new_key, self._hotkey_toggle)
        self.saved_hotkey = new_key
        self.settings["hotkey"] = new_key

    def _hotkey_toggle(self):
        """Called by the global hotkey.
        - If running: stops execution (works for any running mode).
        - If idle: starts the entire playlist."""
        def action():
            if self.is_running:
                self._stop()
            else:
                self._start()
        self.root.after(0, action)

    # ═══════════════════════════════════════════════════════════════
    # VALIDATION
    # ═══════════════════════════════════════════════════════════════

    def _validate_int_positive(self, value):
        if value == "":
            return True
        try:
            v = int(value)
            return v >= 1
        except ValueError:
            return False

    def _validate_int_non_negative(self, value):
        if value == "":
            return True
        try:
            v = int(value)
            return v >= 0
        except ValueError:
            return False

    # ═══════════════════════════════════════════════════════════════
    # TIME CALCULATIONS
    # ═══════════════════════════════════════════════════════════════

    # Overhead constants (must match executor.py)
    _LAUNCH_BUFFER = 2.0     # Post-launch buffer per execution
    _INITIAL_SLEEP = 1.0     # Initial sleep before first execution

    def _calc_item_time(self, item):
        reps = item["repetitions"]
        duration = item["duration"]
        pause = item["pause"]
        # Last repetition has no trailing pause
        task_time = (duration + pause) * reps - pause
        # Each execution has a launch buffer overhead
        overhead = self._LAUNCH_BUFFER * reps
        return max(task_time + overhead, 0)

    def _parse_int(self, var, default=0):
        try:
            return int(var.get())
        except (ValueError, TypeError):
            return default

    def _calc_total_time(self, playlist=None):
        if playlist is None:
            # When showing the UI estimate, only count enabled items
            playlist = [item for item in self.playlist if item.get("enabled", True)]
        target = playlist
        # Sum item times (already includes per-launch buffer overhead)
        loop_time = sum(self._calc_item_time(item) for item in target)
        # Add initial sleep overhead (once per run)
        loop_time += self._INITIAL_SLEEP
        mode = self.loop_mode_var.get()
        if mode == "infinite":
            return None  # Infinite
        count = self._parse_int(self.loop_count_var, 1) if mode == "fixed" else 1
        delay = self._parse_int(self.loop_delay_var, 0)
        total = loop_time * count + delay * max(count - 1, 0)
        return total

    def _update_time_labels(self):
        total = self._calc_total_time()
        if total is None:
            self.total_time_label.config(text="Total: ∞")
        else:
            self.total_time_label.config(text=f"Total: {format_time(total)}")

    def _on_loop_mode_change(self, event):
        mode = self.loop_mode_var.get()
        if mode == "infinite":
            self.loop_count_entry.config(state="disabled")
        else:
            self.loop_count_entry.config(state="normal")
        self._update_time_labels()

    # ═══════════════════════════════════════════════════════════════
    # PLAYLIST UI
    # ═══════════════════════════════════════════════════════════════

    def _refresh_list(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for idx, item in enumerate(self.playlist):
            item_time = self._calc_item_time(item)
            # Backward compat: items without "enabled" default to True
            enabled = item.get("enabled", True)
            check = "✅" if enabled else "❌"
            self.tree.insert(
                "",
                tk.END,
                values=(
                    idx + 1,
                    check,
                    os.path.basename(item["path"]),
                    item["repetitions"],
                    item["duration"],
                    item["pause"],
                    format_time(item_time),
                ),
            )
        self._update_time_labels()

    def _on_tree_click(self, event):
        """Toggle enabled/disabled when clicking the checkbox column."""
        region = self.tree.identify_region(event.x, event.y)
        column = self.tree.identify_column(event.x)
        item_id = self.tree.identify_row(event.y)

        # Only act on the checkbox column (#2 = "hab")
        if region != "cell" or column != "#2" or not item_id:
            return

        idx = self.tree.index(item_id)
        # Toggle
        current = self.playlist[idx].get("enabled", True)
        self.playlist[idx]["enabled"] = not current
        self._refresh_list()
        # Re-select the toggled item
        children = self.tree.get_children()
        if idx < len(children):
            self.tree.selection_set(children[idx])

    def _on_tree_double_click(self, event):
        """Inline editing: double-click on reps/duration/pause cell to edit directly."""
        # Dismiss any previous inline entry
        self._dismiss_inline_edit()

        region = self.tree.identify_region(event.x, event.y)
        column = self.tree.identify_column(event.x)
        item_id = self.tree.identify_row(event.y)

        # Editable columns: "#4"=reps, "#5"=duration, "#6"=pause
        editable_columns = {"#4": "repetitions", "#5": "duration", "#6": "pause"}
        if region != "cell" or column not in editable_columns or not item_id:
            return

        idx = self.tree.index(item_id)
        field = editable_columns[column]
        current_value = self.playlist[idx][field]

        # Get cell bounding box
        bbox = self.tree.bbox(item_id, column)
        if not bbox:
            return

        x, y, width, height = bbox

        # Create entry overlay on the cell
        entry = ttk.Entry(self.tree, justify="center")
        entry.place(x=x, y=y, width=width, height=height)
        entry.insert(0, str(current_value))
        entry.select_range(0, tk.END)
        entry.focus_set()
        self._inline_entry = entry

        # Validation function per field
        if field == "repetitions":
            validate_fn = self._validate_int_positive
        else:
            validate_fn = self._validate_int_non_negative

        def save_edit(*args):
            value = entry.get().strip()
            if value == "":
                # Empty — revert to original (don't save)
                self._dismiss_inline_edit()
                return
            if not validate_fn(value):
                # Invalid — revert
                self._dismiss_inline_edit()
                return
            try:
                new_val = int(value)
            except ValueError:
                self._dismiss_inline_edit()
                return

            self.playlist[idx][field] = new_val
            self._refresh_list()
            # Re-select the edited item
            children = self.tree.get_children()
            if idx < len(children):
                self.tree.selection_set(children[idx])
            self._dismiss_inline_edit()

        def cancel_edit(*args):
            self._dismiss_inline_edit()

        entry.bind("<Return>", save_edit)
        entry.bind("<Escape>", cancel_edit)
        entry.bind("<FocusOut>", save_edit)

    def _dismiss_inline_edit(self):
        """Destroy the inline editing entry if one exists."""
        if self._inline_entry is not None:
            try:
                self._inline_entry.destroy()
            except tk.TclError:
                pass
            self._inline_entry = None

    # ═══════════════════════════════════════════════════════════════
    # DIALOGS
    # ═══════════════════════════════════════════════════════════════

    def _dark_dialog(self, title, message, kind="info"):
        """Custom dark-themed dialog to replace native messagebox."""
        colors = {"info": DARK_COLORS["blue"], "warning": DARK_COLORS["yellow"],
                  "error": DARK_COLORS["red"], "success": DARK_COLORS["green"]}
        accent = colors.get(kind, DARK_COLORS["blue"])

        dlg = tk.Toplevel(self.root, bg=DARK_COLORS["bg"])
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.lift()

        frame = ttk.Frame(dlg, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=message, style="Bold.TLabel",
                  wraplength=350, justify=tk.CENTER).pack(pady=(5, 12))

        btn = tk.Button(frame, text="  Aceptar  ",
                        bg=accent, fg="#ffffff", font=("Segoe UI", 9, "bold"),
                        borderwidth=0, activebackground=DARK_COLORS["accent_hover"],
                        cursor="hand2", padx=20, pady=4,
                        command=dlg.destroy)
        btn.pack()

        # Center on parent
        dlg.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        dw, dh = dlg.winfo_width(), dlg.winfo_height()
        dlg.geometry(f"+{px + (pw - dw)//2}+{py + (ph - dh)//2}")

        dlg.wait_window()

    def _add_script(self):
        path = filedialog.askopenfilename(
            title="Seleccionar script TinyTask",
            filetypes=[("Ejecutables", "*.exe"), ("Todos", "*.*")],
        )
        if not path:
            return

        if not os.path.isfile(path):
            self._dark_dialog("Error", f"El archivo no existe:\n{path}", "error")
            return

        win = tk.Toplevel(self.root, bg=DARK_COLORS["bg"])
        win.title("Agregar script")
        win.geometry("280x260")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.lift()

        form = ttk.Frame(win, padding=10)
        form.pack(fill=tk.BOTH, expand=True)

        ttk.Label(form, text=f"Script: {os.path.basename(path)}", style="Dim.TLabel").pack(pady=(0, 10))

        ttk.Label(form, text="Repeticiones:", style="Compact.TLabel").pack(anchor=tk.W)
        reps_var = tk.IntVar(value=1)
        ttk.Spinbox(form, from_=1, to=999, textvariable=reps_var, width=8).pack(anchor=tk.W, pady=(0, 6))

        ttk.Label(form, text="Duración (s):", style="Compact.TLabel").pack(anchor=tk.W)
        dur_var = tk.IntVar(value=10)
        ttk.Spinbox(form, from_=1, to=9999, textvariable=dur_var, width=8).pack(anchor=tk.W, pady=(0, 6))

        ttk.Label(form, text="Pausa entre reps (s):", style="Compact.TLabel").pack(anchor=tk.W)
        pause_var = tk.IntVar(value=0)
        ttk.Spinbox(form, from_=0, to=9999, textvariable=pause_var, width=8).pack(anchor=tk.W, pady=(0, 6))

        time_preview = ttk.Label(form, text="Tiempo: 10s", style="Dim.TLabel")
        time_preview.pack(anchor=tk.W, pady=(0, 8))

        def update_preview(*args):
            total = (dur_var.get() + pause_var.get()) * reps_var.get() - pause_var.get()
            total = max(total, 0)
            time_preview.config(text=f"Tiempo: {format_time(total)}")

        reps_var.trace_add("write", update_preview)
        dur_var.trace_add("write", update_preview)
        pause_var.trace_add("write", update_preview)

        def save():
            self.playlist.append(
                {
                    "path": path,
                    "repetitions": reps_var.get(),
                    "duration": dur_var.get(),
                    "pause": pause_var.get(),
                    "enabled": True,
                }
            )
            self._refresh_list()
            win.destroy()

        ttk.Button(form, text="Guardar", command=save, style="Compact.TButton").pack()

    def _edit_script(self):
        sel = self.tree.selection()
        if not sel:
            self._dark_dialog("Seleccionar", "Seleccioná un script de la lista para editarlo.", "info")
            return
        idx = self.tree.index(sel[0])
        item = self.playlist[idx]

        win = tk.Toplevel(self.root, bg=DARK_COLORS["bg"])
        win.title("Editar script")
        win.geometry("280x280")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.lift()

        form = ttk.Frame(win, padding=10)
        form.pack(fill=tk.BOTH, expand=True)

        ttk.Label(form, text=f"Script: {os.path.basename(item['path'])}", style="Dim.TLabel").pack(pady=(0, 10))

        ttk.Label(form, text="Repeticiones:", style="Compact.TLabel").pack(anchor=tk.W)
        reps_var = tk.IntVar(value=item["repetitions"])
        ttk.Spinbox(form, from_=1, to=999, textvariable=reps_var, width=8).pack(anchor=tk.W, pady=(0, 6))

        ttk.Label(form, text="Duración (s):", style="Compact.TLabel").pack(anchor=tk.W)
        dur_var = tk.IntVar(value=item["duration"])
        ttk.Spinbox(form, from_=1, to=9999, textvariable=dur_var, width=8).pack(anchor=tk.W, pady=(0, 6))

        ttk.Label(form, text="Pausa entre reps (s):", style="Compact.TLabel").pack(anchor=tk.W)
        pause_var = tk.IntVar(value=item["pause"])
        ttk.Spinbox(form, from_=0, to=9999, textvariable=pause_var, width=8).pack(anchor=tk.W, pady=(0, 6))

        time_preview = ttk.Label(form, text=f"Tiempo: {format_time(self._calc_item_time(item))}", style="Dim.TLabel")
        time_preview.pack(anchor=tk.W, pady=(0, 8))

        def update_preview(*args):
            total = (dur_var.get() + pause_var.get()) * reps_var.get() - pause_var.get()
            total = max(total, 0)
            time_preview.config(text=f"Tiempo: {format_time(total)}")

        reps_var.trace_add("write", update_preview)
        dur_var.trace_add("write", update_preview)
        pause_var.trace_add("write", update_preview)

        def save():
            self.playlist[idx] = {
                "path": item["path"],
                "repetitions": reps_var.get(),
                "duration": dur_var.get(),
                "pause": pause_var.get(),
            }
            self._refresh_list()
            win.destroy()

        ttk.Button(form, text="Guardar cambios", command=save, style="Compact.TButton").pack()

    def _remove_script(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        del self.playlist[idx]
        self._refresh_list()

    def _move_up(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        if idx > 0:
            self.playlist[idx], self.playlist[idx - 1] = (
                self.playlist[idx - 1],
                self.playlist[idx],
            )
            self._refresh_list()
            self.tree.selection_set(self.tree.get_children()[idx - 1])

    def _move_down(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        if idx < len(self.playlist) - 1:
            self.playlist[idx], self.playlist[idx + 1] = (
                self.playlist[idx + 1],
                self.playlist[idx],
            )
            self._refresh_list()
            self.tree.selection_set(self.tree.get_children()[idx + 1])

    # ═══════════════════════════════════════════════════════════════
    # EXECUTION
    # ═══════════════════════════════════════════════════════════════

    def _gather_settings(self):
        settings = {
            "loop_mode": self.loop_mode_var.get(),
            "loop_count": self._parse_int(self.loop_count_var, 1),
            "loop_delay": self._parse_int(self.loop_delay_var, 0),
            "hotkey": self.hotkey_var.get().lower(),
            "window_geometry": self.root.geometry(),
            "mini_bar_enabled": self._mini_bar_enabled,
        }
        if self.mini_bar is not None:
            mb = self.mini_bar.get_settings()
            settings.update(mb)
        return settings

    def _start(self):
        if not self.playlist:
            self._dark_dialog("Vacío", "No hay scripts en la lista.", "warning")
            return
        # Only run enabled items
        active = [item for item in self.playlist if item.get("enabled", True)]
        if not active:
            self._dark_dialog("Sin habilitados", "No hay scripts habilitados. Activá alguno con el checkbox ✅.", "warning")
            return
        self._execute(active, self._gather_settings())

    def _run_selected(self):
        sel = self.tree.selection()
        if not sel:
            self._dark_dialog("Seleccionar", "Seleccioná un script de la lista para ejecutarlo solo.", "info")
            return
        idx = self.tree.index(sel[0])
        item = self.playlist[idx]

        # Force single-run settings for the selected item only
        override_settings = {
            "loop_mode": "once",
            "loop_count": 1,
            "loop_delay": 0,
        }
        self._execute([item], override_settings)

    def _execute(self, playlist, settings):
        if self.is_running:
            return
        if not playlist:
            return

        # Ensure any previous thread has fully terminated
        if self.executor_thread is not None and self.executor_thread.is_alive():
            self.executor_thread.join(timeout=5)

        # Fresh state for every new run
        self.is_running = True
        self.stop_event = threading.Event()
        self.launch_event = threading.Event()

        # Compute real total time based on the actual playlist being run
        self._exec_total_time = self._calc_total_time(playlist)

        # ── Show mini bar if enabled ──
        if self._mini_bar_enabled:
            self._ensure_mini_bar()

        callbacks = {
            "on_start_run": lambda total_global, total_per_loop, max_loops: self.root.after(
                0, lambda: self._cb_start_run(total_global, total_per_loop, max_loops)
            ),
            "on_start_loop": lambda current, max_loops, total_global: self.root.after(
                0, lambda: self._cb_start_loop(current, max_loops, total_global)
            ),
            "on_start_item": lambda idx, name, reps: self.root.after(
                0, lambda: self._cb_start_item(idx, name, reps)
            ),
            "on_repeat": lambda global_rep, total_global, total_per_loop, name, current, total_item, loop, max_loops: self.root.after(
                0,
                lambda: self._cb_repeat(
                    global_rep, total_global, total_per_loop, name, current, total_item, loop, max_loops
                ),
            ),
            "on_loop_delay": lambda current, delay, total_global: self.root.after(
                0, lambda: self._cb_loop_delay(current, delay, total_global)
            ),
            "on_finish": lambda msg, done, total_global, total_per_loop, loops, max_loops: self.root.after(
                0, lambda: self._cb_finish(msg, done, total_global, total_per_loop, loops, max_loops)
            ),
            "on_error": lambda msg: self.root.after(0, lambda: self._cb_error(msg)),
            "on_launch": lambda path: self.root.after(0, lambda: self._do_launch(path)),
        }

        self.executor_thread = Executor(
            playlist, settings, callbacks, self.stop_event, self.launch_event
        )
        self.executor_thread.start()

    def _stop(self):
        if not self.is_running:
            return
        self.stop_event.set()
        self._set_status("DETENIENDO...", DARK_COLORS["yellow"])
        # Update mini bar
        if self.mini_bar is not None:
            elapsed = time.time() - self._exec_start_time
            self.mini_bar.update("Deteniendo...", 0, 1, mini_format_time(int(elapsed)), True)

    def _do_launch(self, path):
        """Launch the .exe using os.startfile, the most native Windows way.
        This is exactly what happens when you double-click a file in Explorer.
        It runs completely detached from Python with zero inheritance issues.
        
        On failure, does NOT set launch_event — the executor will timeout
        and report the error properly instead of silently continuing."""
        try:
            if os.name == "nt":
                os.startfile(path)
            else:
                subprocess.Popen([path], shell=False)
        except Exception as e:
            self._dark_dialog(
                "Error al lanzar",
                f"No se pudo ejecutar:\n{path}\n\nError: {e}",
                "error"
            )
            # Do NOT set launch_event on error — executor timeout will catch it
            return
        self.launch_event.set()

    def _update_progress(self, value, maximum):
        """Update progress bar and percentage label."""
        self.progress["maximum"] = maximum
        self.progress["value"] = value
        pct = (value / maximum * 100) if maximum > 0 else 0
        self.progress_pct_label.config(text=f"{int(pct)}%")

    def _poll_timer(self):
        """Update progress bar and countdown based on real elapsed time."""
        if not self.is_running:
            return
        elapsed = time.time() - self._exec_start_time
        if self._exec_total_time is not None:
            remaining = max(self._exec_total_time - elapsed, 0)
            self.countdown_label.config(text=f"⏱️ {format_time(int(remaining))}")
            prog = min(int(elapsed), self._exec_total_time)
            self._update_progress(prog, self._exec_total_time)

            # ── Update mini bar ──
            if self.mini_bar is not None and self.mini_bar.is_visible():
                self.mini_bar.update(
                    self.status_label.cget("text").replace(" EJECUTANDO | ", ""),
                    prog,
                    self._exec_total_time,
                    f"-{mini_format_time(int(remaining))}",
                    True,
                )
        else:
            # Infinite mode: show elapsed time
            self.countdown_label.config(text=f"⏱️ {format_time(int(elapsed))}")
            if self.mini_bar is not None and self.mini_bar.is_visible():
                self.mini_bar.update(
                    self.status_label.cget("text").replace(" EJECUTANDO | ", ""),
                    elapsed % 100, 100,
                    mini_format_time(int(elapsed)),
                    True,
                )
        self.root.after(500, self._poll_timer)

    def _cb_start_run(self, total_global, total_per_loop, max_loops):
        self._exec_start_time = time.time()
        if max_loops is None:
            self._exec_total_time = None
            self._update_progress(0, total_per_loop)
            status_text = f"EJECUTANDO | Loop ∞ | Reps/loop: {total_per_loop}"
            self._set_status(status_text, DARK_COLORS["blue"])
        else:
            self._update_progress(0, self._exec_total_time or 1)
            status_text = f"EJECUTANDO | Loop 1/{max_loops} | Total reps: {total_global}"
            self._set_status(status_text, DARK_COLORS["blue"])
        self._poll_timer()

    def _cb_start_loop(self, current, max_loops, total_global):
        if max_loops is None:
            total_per_loop = self.progress["maximum"]
            self._update_progress(0, total_per_loop)
        if max_loops is None:
            status_text = f"EJECUTANDO | Loop {current} (∞)"
        else:
            status_text = f"EJECUTANDO | Loop {current}/{max_loops}"
        self._set_status(status_text, DARK_COLORS["blue"])

    def _cb_start_item(self, idx, name, reps):
        pass

    def _cb_repeat(self, global_rep, total_global, total_per_loop, name, current, total_item, loop, max_loops):
        # Infinite mode: track per-loop progress by rep count (bar is reset each loop)
        if max_loops is None:
            loop_progress = ((global_rep - 1) % total_per_loop) + 1
            self._update_progress(loop_progress, total_per_loop)

        loop_str = f"L{loop}" if max_loops is None else f"L{loop}/{max_loops}"
        if total_global is None:
            total_str = "∞"
        else:
            total_str = f"{global_rep}/{total_global}"
        status_text = (
            f"EJECUTANDO | {loop_str} | {name}: {current}/{total_item} | Total: {total_str}"
        )
        self._set_status(status_text, DARK_COLORS["blue"])

        # ── Update mini bar with more detail ──
        if self.mini_bar is not None and self.mini_bar.is_visible():
            elapsed = time.time() - self._exec_start_time
            short_status = f"{name}: {current}/{total_item} | {loop_str}"
            progress_max = total_global if total_global is not None else total_per_loop
            progress_val = global_rep
            if self._exec_total_time is not None:
                remaining = max(self._exec_total_time - elapsed, 0)
                time_text = f"-{mini_format_time(int(remaining))}"
            else:
                time_text = mini_format_time(int(elapsed))
            self.mini_bar.update(short_status, progress_val, progress_max, time_text, True)

    def _cb_loop_delay(self, current, delay, total_global):
        self._set_status(f"ESPERANDO | Loop {current} → pausa {delay}s", DARK_COLORS["purple"])

    def _cb_finish(self, msg, done, total_global, total_per_loop, loops, max_loops):
        self.is_running = False
        loop_str = f"{loops} loops" if max_loops is None else f"{loops}/{max_loops} loops"
        total_str = f"{done}/{total_global}" if total_global is not None else f"{done} (∞)"
        if msg == "Detenido":
            self._set_status(f"DETENIDO | {loop_str} | {total_str} reps", DARK_COLORS["red"])
        elif msg == "Completado":
            self._set_status(f"COMPLETADO | {loop_str} | {total_str} reps", DARK_COLORS["green"])
            self._dark_dialog("Finalizado", f"Ejecución completada.\n{loop_str}\n{total_str} reps realizadas.", "success")
        else:
            self._set_status(f"{msg} | {loop_str} | {total_str} reps", "#7f8c8d")
        self._update_progress(self._exec_total_time or done, self._exec_total_time or total_per_loop or 1)

        # ── Reset mini bar ──
        if self.mini_bar is not None:
            elapsed = time.time() - self._exec_start_time
            self.mini_bar.update(f"{msg}", 0, 1, mini_format_time(int(elapsed)), False)

    def _set_status(self, text, color):
        """Update the status label with text and background color."""
        self.status_label.config(text=f" {text} ", bg=color)

    def _cb_error(self, msg):
        self._dark_dialog("Error", msg, "error")
        self.is_running = False
        self._set_status(f"Error: {msg}", DARK_COLORS["red"])
        if self.mini_bar is not None:
            self.mini_bar.reset()

    def _on_close(self):
        settings = self._gather_settings()
        save_config(self.playlist, settings)
        self.hotkey.stop()
        if self.mini_bar is not None:
            self.mini_bar.close()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = OrchestratorApp(root)
    root.mainloop()
