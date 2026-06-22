import tkinter as tk
from tkinter import ttk, filedialog
import customtkinter as ctk
import os
import sys
import subprocess
import threading
import time
import ctypes

from config_manager import (load_config, save_config, get_config_path,
                            DEFAULT_SETTINGS, list_profiles,
                            get_active_profile_name, set_active_profile_name,
                            delete_profile, rename_profile, clone_profile)
from executor import Executor
from hotkey import HotkeyListener
from mini_bar import MiniBar, format_time as mini_format_time
from gui_macro import MacroEditorWindow


def _get_icon_path():
    """Resolve app_icon.ico path in both dev and frozen (PyInstaller) modes."""
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'app_icon.ico')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app_icon.ico')


def format_time(seconds):
    if isinstance(seconds, float) and seconds == int(seconds):
        seconds = int(seconds)
    if isinstance(seconds, float):
        # Mostrar un decimal para tiempos fraccionarios
        if seconds < 60:
            return f"{seconds:.1f}s"
        m = int(seconds // 60)
        s = seconds % 60
        return f"{m}m {s:.1f}s"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


# ── Dark Theme Colors (synced with CTk "dark-blue" palette) ──────
DARK_COLORS = {
    "bg":           "#0d0d0d",   # main window bg — deep black
    "surface":      "#1a1a1a",   # CTkFrame fg_color
    "surface_alt":  "#212121",   # Treeview row bg
    "border":       "#3a3a3a",   # subtle borders
    "text":         "#e0e0e0",   # primary text
    "text_dim":     "#808080",   # secondary text
    "accent":       "#1f538d",   # CTkButton blue
    "accent_hover": "#14375e",   # button hover
    "green":        "#2e8b57",   # success / ready
    "green_dim":    "#1e6b3e",   # darker green
    "red":          "#c44545",   # stop / error
    "yellow":       "#c4a43d",   # warning
    "blue":         "#3a7ebf",   # running
    "purple":       "#7c5cbf",   # waiting
    "menu_bg":      "#0d0d0d",   # menu bar background
    "menu_fg":      "#e0e0e0",   # menu bar text
    "menu_active":  "#2a2a2a",   # menu hover
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
                ctypes.sizeof(ctypes.c_int(1)))
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
                    ctypes.sizeof(ctypes.c_int(1)))
            except Exception:
                pass
    except Exception:
        pass


class OrchestratorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AutoPulsar")
        self.root.minsize(600, 380)
        try:
            self.root.iconbitmap(_get_icon_path())
        except Exception:
            pass
        self.root.configure(fg_color=DARK_COLORS["bg"])

        # Dark title bar on Windows (with retry logic)
        _apply_dark_titlebar(self.root)

        # ── Perfiles ──
        self._current_profile = get_active_profile_name()
        self._profile_names = list_profiles()
        if self._current_profile not in self._profile_names:
            # Profile doesn't exist yet (first run / migration), create it
            self._profile_names.append(self._current_profile)

        # Estado
        config = load_config(self._current_profile)
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

        # ── Treeview item map (iid → type/index) ──
        self._item_map = {}

        # ── Menu Bar ──
        self._build_menu()

        # Construir UI
        self._build_ui()
        self._refresh_list()
        self._update_time_labels()
        self._update_title()

        # Guardar al cerrar
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ═══════════════════════════════════════════════════════════════
    # MENU BAR
    # ═══════════════════════════════════════════════════════════════

    def _build_menu(self):
        """Custom dark menu bar using Menubutton widgets.
        Native tk.Menu ignores bg on the horizontal bar in Windows -
        Menubutton gives full color control."""
        c = DARK_COLORS

        # Menu bar container frame
        self._menubar_frame = tk.Frame(
            self.root, bg=c["menu_bg"], height=28,
            highlightthickness=0, borderwidth=0
        )
        self._menubar_frame.pack(fill=tk.X, side=tk.TOP)
        self._menubar_frame.pack_propagate(False)

        # Archivo
        file_mb = tk.Menubutton(
            self._menubar_frame, text=" Archivo ",
            bg=c["menu_bg"], fg=c["menu_fg"],
            activebackground=c["menu_active"], activeforeground="#ffffff",
            font=("Segoe UI", 9), borderwidth=0,
            padx=6, pady=3, cursor="hand2")
        file_mb.pack(side=tk.LEFT)
        file_menu = tk.Menu(
            file_mb, tearoff=0,
            bg=c["menu_bg"], fg=c["menu_fg"],
            activebackground=c["menu_active"], activeforeground="#ffffff",
            font=("Segoe UI", 9), borderwidth=1, relief="solid")
        file_menu.add_command(label="💾 Guardar playlist", command=self._menu_save,
                              accelerator="Ctrl+S")
        file_menu.add_separator(background=c["border"])
        file_menu.add_command(label="🚪 Salir", command=self._on_close, accelerator="Alt+F4")
        file_mb.config(menu=file_menu)

        # Ver
        view_mb = tk.Menubutton(
            self._menubar_frame, text=" Ver ",
            bg=c["menu_bg"], fg=c["menu_fg"],
            activebackground=c["menu_active"], activeforeground="#ffffff",
            font=("Segoe UI", 9), borderwidth=0,
            padx=6, pady=3, cursor="hand2")
        view_mb.pack(side=tk.LEFT)
        view_menu = tk.Menu(
            view_mb, tearoff=0,
            bg=c["menu_bg"], fg=c["menu_fg"],
            activebackground=c["menu_active"], activeforeground="#ffffff",
            font=("Segoe UI", 9), borderwidth=1, relief="solid")
        self._mini_bar_var = tk.BooleanVar(value=self._mini_bar_enabled)
        view_menu.add_checkbutton(
            label="📊 Mini Bar siempre visible",
            variable=self._mini_bar_var,
            command=self._toggle_mini_bar,
            selectcolor=c["surface_alt"])
        view_menu.add_separator(background=c["border"])
        view_menu.add_command(label="🗟️ Restaurar tamaño", command=self._menu_reset_size)
        view_mb.config(menu=view_menu)

        # Ayuda
        help_mb = tk.Menubutton(
            self._menubar_frame, text=" Ayuda ",
            bg=c["menu_bg"], fg=c["menu_fg"],
            activebackground=c["menu_active"], activeforeground="#ffffff",
            font=("Segoe UI", 9), borderwidth=0,
            padx=6, pady=3, cursor="hand2")
        help_mb.pack(side=tk.LEFT)
        help_menu = tk.Menu(
            help_mb, tearoff=0,
            bg=c["menu_bg"], fg=c["menu_fg"],
            activebackground=c["menu_active"], activeforeground="#ffffff",
            font=("Segoe UI", 9), borderwidth=1, relief="solid")
        help_menu.add_command(label="📖 Manual de Usuario",
                              command=self._open_manual)
        help_menu.add_command(label="ℹ️ Acerca de AutoPulsar",
                              command=self._menu_about)
        help_mb.config(menu=help_menu)

        # Ctrl+S shortcut
        self.root.bind_all("<Control-s>", lambda e: self._menu_save())
    def _menu_save(self):
        """Guardar playlist actual."""
        settings = self._gather_settings()
        save_config({"playlist": self.playlist, "settings": settings}, self._current_profile)
        self._dark_dialog("Guardado", "Playlist y configuración guardadas.", "success")

    # ═══════════════════════════════════════════════════════════════
    # PERFILES
    # ═══════════════════════════════════════════════════════════════

    def _save_and_switch(self, target_profile):
        """Guardar perfil actual y cargar el nuevo."""
        if self._current_profile == target_profile:
            return
        # Save current
        settings = self._gather_settings()
        save_config({"playlist": self.playlist, "settings": settings}, self._current_profile)
        # Load target
        config = load_config(target_profile)
        self._current_profile = target_profile
        self.playlist = config["playlist"]
        self.settings = config["settings"]
        set_active_profile_name(target_profile)
        # Refresh UI
        self._refresh_profile_combo()
        self._refresh_list()
        self._sync_loop_controls()
        self._update_title()

    def _on_profile_switch(self, event=None):
        """Handler del Combobox de perfil."""
        new_profile = self.profile_var.get()
        if new_profile and new_profile != self._current_profile:
            self._save_and_switch(new_profile)

    def _refresh_profile_combo(self):
        """Actualizar la lista y selección del combobox de perfiles."""
        self._profile_names = list_profiles()
        if self._current_profile not in self._profile_names:
            self._profile_names.append(self._current_profile)
        self.profile_combo["values"] = sorted(set(self._profile_names))
        self.profile_var.set(self._current_profile)

    def _update_title(self):
        """Actualizar el título de la ventana con el perfil activo."""
        self.root.title(f"AutoPulsar — [{self._current_profile}]")

    def _new_profile(self):
        """Crear un nuevo perfil vacío."""
        # Save current first
        settings = self._gather_settings()
        save_config({"playlist": self.playlist, "settings": settings}, self._current_profile)
        # Ask name
        dlg = ctk.CTkToplevel(self.root, fg_color=DARK_COLORS["bg"])
        _apply_dark_titlebar(dlg)
        dlg.title("Nuevo perfil")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        frame = ttk.Frame(dlg, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)
        ctk.CTkLabel(frame, text="Nombre del nuevo perfil:").pack(pady=(0, 8))
        name_var = tk.StringVar()
        entry = ctk.CTkEntry(frame, textvariable=name_var, width=24)
        entry.pack(pady=(0, 10))
        entry.focus_set()
        def _create():
            name = name_var.get().strip()
            if not name:
                return
            if name in list_profiles():
                self._dark_dialog("Error", f"El perfil '{name}' ya existe.", "error")
                return
            save_config({"playlist": [], "settings": DEFAULT_SETTINGS.copy()}, name)
            dlg.destroy()
            self._save_and_switch(name)
        ctk.CTkButton(frame, text="Crear", command=_create).pack()
        entry.bind("<Return>", lambda e: _create())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        # Center
        dlg.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        dw, dh = dlg.winfo_width(), dlg.winfo_height()
        dlg.geometry(f"+{px + (pw - dw)//2}+{py + (ph - dh)//2}")
        dlg.wait_window()

    def _rename_profile(self):
        """Renombrar el perfil activo."""
        old_name = self._current_profile
        dlg = ctk.CTkToplevel(self.root, fg_color=DARK_COLORS["bg"])
        _apply_dark_titlebar(dlg)
        dlg.title("Renombrar perfil")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        frame = ttk.Frame(dlg, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)
        ctk.CTkLabel(frame, text=f"Renombrar '{old_name}' a:").pack(pady=(0, 8))
        name_var = tk.StringVar(value=old_name)
        entry = ctk.CTkEntry(frame, textvariable=name_var, width=24)
        entry.pack(pady=(0, 10))
        entry.focus_set()
        entry.select_range(0, tk.END)
        def _rename():
            new_name = name_var.get().strip()
            if not new_name or new_name == old_name:
                dlg.destroy()
                return
            if new_name in list_profiles():
                self._dark_dialog("Error", f"El perfil '{new_name}' ya existe.", "error")
                return
            if rename_profile(old_name, new_name):
                # Reload from the renamed file
                config = load_config(new_name)
                self._current_profile = new_name
                self.playlist = config["playlist"]
                self.settings = config["settings"]
                self._refresh_profile_combo()
                self._refresh_list()
                self._sync_loop_controls()
                self._update_title()
            dlg.destroy()
        ctk.CTkButton(frame, text="Renombrar", command=_rename).pack()
        entry.bind("<Return>", lambda e: _rename())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        dw, dh = dlg.winfo_width(), dlg.winfo_height()
        dlg.geometry(f"+{px + (pw - dw)//2}+{py + (ph - dh)//2}")
        dlg.wait_window()

    def _delete_profile(self):
        """Eliminar un perfil."""
        names = list_profiles()
        if len(names) <= 1:
            self._dark_dialog("Error", "No se puede eliminar el único perfil.", "error")
            return
        # Delete current profile → switch to another
        name_to_delete = self._current_profile
        confirm = self._dark_confirm(
            "Eliminar perfil",
            f"¿Eliminar el perfil '{name_to_delete}'?\n\n"
            f"Esta acción no se puede deshacer.\n"
            f"Los scripts y configuraciones se perderán.")
        if not confirm:
            return
        delete_profile(name_to_delete)
        # Switch to first available — but do NOT use _save_and_switch
        # because it saves back to the deleted profile, undoing the delete
        remaining = list_profiles()
        if not remaining:
            remaining = ["default"]
            save_config({"playlist": [], "settings": DEFAULT_SETTINGS.copy()}, "default")
        self._current_profile = remaining[0]
        set_active_profile_name(remaining[0])
        config = load_config(remaining[0])
        self.playlist = config["playlist"]
        self.settings = config["settings"]
        self._refresh_profile_combo()
        self._refresh_list()
        self._sync_loop_controls()
        self._update_title()

    def _clone_profile(self):
        """Clonar (duplicar) el perfil activo."""
        source = self._current_profile
        dlg = ctk.CTkToplevel(self.root, fg_color=DARK_COLORS["bg"])
        _apply_dark_titlebar(dlg)
        dlg.title("Clonar perfil")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        frame = ttk.Frame(dlg, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)
        ctk.CTkLabel(frame, text=f"Clonar '{source}' como:").pack(pady=(0, 8))
        name_var = tk.StringVar(value=f"{source} (copia)")
        entry = ctk.CTkEntry(frame, textvariable=name_var, width=24)
        entry.pack(pady=(0, 10))
        entry.focus_set()
        entry.select_range(0, tk.END)
        def _clone():
            new_name = name_var.get().strip()
            if not new_name:
                return
            if new_name in list_profiles():
                self._dark_dialog("Error", f"El perfil '{new_name}' ya existe.", "error")
                return
            # Save current first
            settings = self._gather_settings()
            save_config({"playlist": self.playlist, "settings": settings}, source)
            if clone_profile(source, new_name):
                dlg.destroy()
                self._save_and_switch(new_name)
        ctk.CTkButton(frame, text="Clonar", command=_clone).pack()
        entry.bind("<Return>", lambda e: _clone())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        dw, dh = dlg.winfo_width(), dlg.winfo_height()
        dlg.geometry(f"+{px + (pw - dw)//2}+{py + (ph - dh)//2}")
        dlg.wait_window()

    def _sync_loop_controls(self):
        """Sincronizar los controles de loop con self.settings."""
        self.loop_mode_var.set(self.settings.get("loop_mode", "once"))
        self.loop_count_var.set(str(self.settings.get("loop_count", 1)))
        self.loop_delay_var.set(str(self.settings.get("loop_delay", 0)))
        self._on_loop_mode_change(None)

    def _menu_reset_size(self):
        """Restaurar tamaño default."""
        self.root.geometry("750x500")
        self._dark_dialog("Tamaño", "Ventana restaurada a 750×500.", "info")

    def _open_manual(self):
        """Abrir el manual de usuario en el navegador."""
        import sys
        import os
        import tempfile
        import shutil

        # Buscar manual.html — en bundle o en desarrollo
        manual_path = None
        img_dir = None

        if getattr(sys, 'frozen', False):
            # PyInstaller bundle
            base = sys._MEIPASS
            manual_path = os.path.join(base, "manual.html")
            img_dir = os.path.join(base, "manual_images")
        else:
            # Desarrollo
            base = os.path.dirname(os.path.abspath(__file__))
            manual_path = os.path.join(base, "manual.html")
            img_dir = os.path.join(base, "manual_images")

        if not os.path.exists(manual_path):
            self._dark_dialog("Manual no encontrado",
                "El archivo manual.html no se encontró.", "warning")
            return

        # Copiar a temp para que el navegador pueda abrirlo con imágenes
        tmpdir = tempfile.mkdtemp(prefix="ap_manual_")
        shutil.copy(manual_path, os.path.join(tmpdir, "manual.html"))
        if img_dir and os.path.isdir(img_dir):
            tmp_img = os.path.join(tmpdir, "manual_images")
            shutil.copytree(img_dir, tmp_img)

        html_path = os.path.join(tmpdir, "manual.html")
        os.startfile(html_path)

    def _menu_about(self):
        """Mostrar diálogo Acerca de."""
        msg = (
            "AutoPulsar v1.2.1\n\n"
            "Grabador y reproductor de macros\n"
            "de teclado y ratón con condiciones visuales.\n\n"
            "Macros nativas — graba, edita y reproduce\n"
            "secuencias sin depender de herramientas externas. 🎬\n\n"
            "Agrupamiento de scripts — organiza tareas\n"
            "en grupos y muévelos como bloques. 📁\n\n"
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

    def _hide_mini_bar(self):
        """Oculta la Mini Bar (llamado al finalizar ejecución)."""
        if self.mini_bar is not None and self.mini_bar.is_visible():
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
        """Tema oscuro: CTk para widgets modernos + ttk solo para Treeview/Combobox/Spinbox."""
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        # ── CustomTkinter global defaults (compactos, dark, sin alpha en hex) ──
        try:
            from customtkinter import ThemeManager
            # Frames
            ThemeManager.theme["CTkFrame"]["corner_radius"] = 6
            # Buttons
            ThemeManager.theme["CTkButton"]["corner_radius"] = 6
            ThemeManager.theme["CTkButton"]["height"] = 28
            ThemeManager.theme["CTkButton"]["font"] = ("Segoe UI", 9)
            ThemeManager.theme["CTkButton"]["border_width"] = 1
            ThemeManager.theme["CTkButton"]["border_color"] = ["#3a3a3a", "#2a2a2a"]
            ThemeManager.theme["CTkButton"]["fg_color"] = ["#1a1a1a", "#141414"]
            ThemeManager.theme["CTkButton"]["hover_color"] = ["#2a2a2a", "#1a1a1a"]
            ThemeManager.theme["CTkButton"]["text_color"] = "#e0e0e0"
            # Entries
            ThemeManager.theme["CTkEntry"]["corner_radius"] = 4
            ThemeManager.theme["CTkEntry"]["border_width"] = 1
            ThemeManager.theme["CTkEntry"]["fg_color"] = ["#212121", "#1a1a1a"]
            ThemeManager.theme["CTkEntry"]["text_color"] = "#e0e0e0"
            ThemeManager.theme["CTkEntry"]["border_color"] = ["#3a3a3a", "#2a2a2a"]
            # Checkboxes y RadioButtons — tema oscuro integrado
            ThemeManager.theme["CTkCheckBox"]["corner_radius"] = 3
            ThemeManager.theme["CTkCheckBox"]["border_width"] = 2
            ThemeManager.theme["CTkCheckBox"]["fg_color"] = ["#1f538d", "#14375e"]
            ThemeManager.theme["CTkCheckBox"]["border_color"] = ["#505050", "#404040"]
            ThemeManager.theme["CTkCheckBox"]["checkmark_color"] = "#ffffff"
            ThemeManager.theme["CTkCheckBox"]["text_color"] = "#e0e0e0"
            ThemeManager.theme["CTkCheckBox"]["hover_color"] = ["#2a2a2a", "#1a1a1a"]
            # RadioButtons
            ThemeManager.theme["CTkRadioButton"]["corner_radius"] = 3
            ThemeManager.theme["CTkRadioButton"]["border_width"] = 2
            ThemeManager.theme["CTkRadioButton"]["fg_color"] = ["#1f538d", "#14375e"]
            ThemeManager.theme["CTkRadioButton"]["border_color"] = ["#505050", "#404040"]
            ThemeManager.theme["CTkRadioButton"]["text_color"] = "#e0e0e0"
            ThemeManager.theme["CTkRadioButton"]["hover_color"] = ["#2a2a2a", "#1a1a1a"]
            # ProgressBar
            ThemeManager.theme["CTkProgressBar"]["corner_radius"] = 2
            ThemeManager.theme["CTkProgressBar"]["fg_color"] = ["#1f538d", "#14375e"]
            ThemeManager.theme["CTkProgressBar"]["progress_color"] = ["#1f538d", "#14375e"]
        except Exception:
            pass

        c = DARK_COLORS
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # ── Treeview (ttk — optimizado para lectura) ──
        style.configure("Treeview",
                        background=c["surface_alt"],
                        foreground=c["text"],
                        fieldbackground=c["surface_alt"],
                        borderwidth=0,
                        rowheight=24)
        style.configure("Treeview.Heading",
                        background=c["surface"],
                        foreground=c["text_dim"],
                        borderwidth=0,
                        relief="flat",
                        padding=(8, 4),
                        font=("Segoe UI", 8, "bold"))
        style.map("Treeview.Heading",
                  background=[("active", c["surface_alt"])],
                  foreground=[("active", c["text"])])
        style.map("Treeview",
                  background=[("selected", c["accent"])],
                  foreground=[("selected", "#ffffff")])

        # ── Scrollbar (ttk — estilizado moderno, sin flechas) ──
        style.configure("TScrollbar",
                        background=c["surface_alt"],
                        troughcolor=c["surface"],
                        bordercolor=c["bg"],
                        arrowcolor=c["surface_alt"],
                        relief="flat", borderwidth=0, arrowsize=14)
        style.map("TScrollbar",
                  background=[("active", c["border"])],
                  arrowcolor=[("active", c["text_dim"])])

        # ── Separator ──
        style.configure("TSeparator", background=c["border"])

        # ── LabelFrame (ttk — editor de condiciones) ──
        style.configure("TLabelframe", background=c["bg"], foreground=c["text"],
                        bordercolor=c["border"], borderwidth=1)
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["text"],
                        font=("Segoe UI", 9, "bold"))

        # ── Combobox / Spinbox (ttk — estilizado moderno) ──
        style.configure("TCombobox",
                        fieldbackground=c["surface_alt"],
                        foreground=c["text"],
                        background=c["surface_alt"],
                        arrowcolor=c["text_dim"],
                        bordercolor=c["border"],
                        borderwidth=1, relief="solid",
                        padding=(10, 4))
        style.map("TCombobox",
                  fieldbackground=[("readonly", c["surface_alt"]),
                                  ("focus", c["surface_alt"]),
                                  ("active", c["surface_alt"])],
                  foreground=[("readonly", c["text"]),
                             ("focus", c["text"]),
                             ("active", c["text"])],
                  arrowcolor=[("active", c["text"]),
                             ("focus", c["text"])],
                  bordercolor=[("focus", c["accent"]),
                              ("active", c["accent"])],
                  selectbackground=[("readonly", c["accent"])],
                  selectforeground=[("readonly", "#ffffff")])
        self.root.option_add("*TCombobox*Listbox.background", c["surface_alt"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 9))
        style.configure("TSpinbox",
                        fieldbackground=c["surface_alt"],
                        foreground=c["text"],
                        bordercolor=c["border"],
                        borderwidth=1, relief="solid",
                        arrowcolor=c["text_dim"],
                        background=c["surface_alt"],
                        padding=(6, 3))
        style.map("TSpinbox",
                  fieldbackground=[("disabled", c["surface_alt"]),
                                  ("readonly", c["surface_alt"])],
                  foreground=[("disabled", c["text_dim"]),
                             ("readonly", c["text"]),
                             ("focus", c["text"]),
                             ("active", c["text"])],
                  bordercolor=[("focus", c["accent"]),
                              ("active", c["accent"])],
                  arrowcolor=[("active", c["text"]),
                             ("focus", c["text"])],
                  background=[("disabled", c["surface_alt"]),
                             ("readonly", c["surface_alt"])])

        # ── Frame (ttk) ──
        style.configure("TFrame", background=c["bg"])

        # ── Menubar (fallback si CTk no lo cubre) ──
        style.configure("TMenubutton", background=c["menu_bg"],
                        foreground=c["menu_fg"])

    def _build_ui(self):
        c = DARK_COLORS

        # ===== Barra de Perfiles =====
        profile_frame = ttk.LabelFrame(self.root, text=" Perfil ", padding=3)
        profile_frame.pack(fill=tk.X, padx=5, pady=(5, 0))

        ctk.CTkLabel(profile_frame, text="Activo:").pack(side=tk.LEFT, padx=(0, 4))
        self.profile_var = tk.StringVar(value=self._current_profile)
        self.profile_combo = ttk.Combobox(
            profile_frame,
            textvariable=self.profile_var,
            values=self._profile_names,
            width=16,
            state="readonly")
        self.profile_combo.pack(side=tk.LEFT, padx=2)
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_switch)

        ctk.CTkButton(profile_frame, text="+", width=26,
                   command=self._new_profile).pack(side=tk.LEFT, padx=(6, 1))
        ctk.CTkButton(profile_frame, text="✎", width=26,
                   command=self._rename_profile).pack(side=tk.LEFT, padx=1)
        ctk.CTkButton(profile_frame, text="🗑", width=26,
                   command=self._delete_profile).pack(side=tk.LEFT, padx=1)
        ctk.CTkButton(profile_frame, text="⧉", width=26,
                   command=self._clone_profile).pack(side=tk.LEFT, padx=1)

        # ===== Frame Configuración del Loop (compacto) =====
        loop_frame = ttk.LabelFrame(self.root, text=" Loop ", padding=5)
        loop_frame.pack(fill=tk.X, padx=5, pady=(5, 3))

        ctk.CTkLabel(loop_frame, text="Modo:").pack(side=tk.LEFT, padx=(0, 3))
        self.loop_mode_var = tk.StringVar(value=self.settings.get("loop_mode", "once"))
        mode_combo = ttk.Combobox(
            loop_frame,
            textvariable=self.loop_mode_var,
            values=["once", "fixed", "infinite", "first_match"],
            width=10,
            state="readonly")
        mode_combo.pack(side=tk.LEFT, padx=2)
        mode_combo.bind("<<ComboboxSelected>>", self._on_loop_mode_change)

        ctk.CTkLabel(loop_frame, text="×").pack(side=tk.LEFT, padx=(8, 3))
        self.loop_count_var = tk.StringVar(value=str(self.settings.get("loop_count", 1)))
        self.loop_count_entry = ctk.CTkEntry(loop_frame, textvariable=self.loop_count_var, width=60, validate="key")
        self.loop_count_entry.configure(validatecommand=(self.root.register(self._validate_int_positive), "%P"))
        self.loop_count_entry.pack(side=tk.LEFT, padx=2)

        ctk.CTkLabel(loop_frame, text="Pausa:").pack(side=tk.LEFT, padx=(10, 3))
        self.loop_delay_var = tk.StringVar(value=str(self.settings.get("loop_delay", 0)))
        self.loop_delay_entry = ctk.CTkEntry(loop_frame, textvariable=self.loop_delay_var, width=50, validate="key")
        self.loop_delay_entry.configure(validatecommand=(self.root.register(self._validate_int_non_negative), "%P"))
        self.loop_delay_entry.pack(side=tk.LEFT, padx=2)
        ctk.CTkLabel(loop_frame, text="s").pack(side=tk.LEFT)

        # Tiempo estimado total
        self.total_time_label = ctk.CTkLabel(loop_frame, text="Total: 0s")
        self.total_time_label.pack(side=tk.RIGHT, padx=5)

        self._on_loop_mode_change(None)

        # ===== Frame lista (principal, expande) =====
        list_frame = ttk.Frame(self.root)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=3)

        columns = ("orden", "hab", "primero", "icono", "nombre", "reps", "duracion", "pausa", "tiempo")
        self.tree = ttk.Treeview(
            list_frame, columns=columns, show="tree headings", selectmode="extended")
        # Column #0 = tree column (expander arrows for groups)
        self.tree.column("#0", width=30, minwidth=24, stretch=False, anchor="w")
        self.tree.heading("#0", text="")

        self.tree.heading("orden", text="#")
        self.tree.heading("hab", text="✓")
        self.tree.heading("primero", text="1°")
        self.tree.heading("icono", text="🖼")
        self.tree.heading("nombre", text="Script")
        self.tree.heading("reps", text="Reps")
        self.tree.heading("duracion", text="Dur (s)")
        self.tree.heading("pausa", text="Pausa (s)")
        self.tree.heading("tiempo", text="Tiempo")

        self.tree.column("orden", width=28, anchor="center")
        self.tree.column("hab", width=26, anchor="center")
        self.tree.column("primero", width=28, anchor="center")
        self.tree.column("icono", width=26, anchor="center")
        self.tree.column("nombre", width=175, anchor="w")
        self.tree.column("reps", width=50, anchor="center")
        self.tree.column("duracion", width=55, anchor="center")
        self.tree.column("pausa", width=55, anchor="center")
        self.tree.column("tiempo", width=70, anchor="center")

        # Click on checkbox column toggles enabled/disabled
        self.tree.bind("<ButtonRelease-1>", self._on_tree_click)
        # Double-click on editable columns for inline editing
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        # Right-click context menu
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        # Track collapse/expand in real time so persisted state is always accurate
        self.tree.bind("<<TreeviewOpen>>", self._on_group_expand_collapse)
        self.tree.bind("<<TreeviewClose>>", self._on_group_expand_collapse)
        self._inline_entry = None

        vsb = ttk.Scrollbar(list_frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        # Ensure treeview respects its container height (don't expand to show all rows)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # Mousewheel scrolling
        def _on_mousewheel(event):
            self.tree.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.tree.bind("<MouseWheel>", _on_mousewheel)

        # ===== Frame botones — fila A =====
        btn_frame_a = ttk.Frame(self.root)
        btn_frame_a.pack(fill=tk.X, padx=5, pady=(0, 1))

        ctk.CTkButton(btn_frame_a, text="🎬 Macro", command=self._add_macro).pack(
            side=tk.LEFT, padx=2
        )
        ctk.CTkButton(btn_frame_a, text="➕ Agregar", command=self._add_script).pack(
            side=tk.LEFT, padx=2
        )
        ctk.CTkButton(btn_frame_a, text="✏️ Editar", command=self._edit_script).pack(
            side=tk.LEFT, padx=2
        )
        ctk.CTkButton(btn_frame_a, text="📋 Clonar", command=self._clone_script).pack(
            side=tk.LEFT, padx=2
        )
        ctk.CTkButton(btn_frame_a, text="🗑️ Quitar", command=self._remove_script).pack(
            side=tk.LEFT, padx=2
        )

        # ===== Frame botones — fila B (movimiento) =====
        btn_frame_b = ttk.Frame(self.root)
        btn_frame_b.pack(fill=tk.X, padx=5, pady=(0, 3))

        ctk.CTkButton(btn_frame_b, text="⬆", command=self._move_up, width=30).pack(
            side=tk.LEFT, padx=(2, 1)
        )
        ctk.CTkButton(btn_frame_b, text="⬇", command=self._move_down, width=30).pack(
            side=tk.LEFT, padx=1
        )

        # ===== Frame botones de grupo =====
        group_btn_frame = ttk.Frame(self.root)
        group_btn_frame.pack(fill=tk.X, padx=5, pady=(0, 3))

        ctk.CTkButton(group_btn_frame, text="📁 Agrupar", command=self._group_selected).pack(
            side=tk.LEFT, padx=2
        )
        ctk.CTkButton(group_btn_frame, text="✂️ Desagrupar", command=self._ungroup_selected).pack(
            side=tk.LEFT, padx=2
        )
        ctk.CTkButton(group_btn_frame, text="🏷️ Renombrar", command=self._rename_group).pack(
            side=tk.LEFT, padx=2
        )

        # ===== Frame ejecución (compacto) =====
        exec_frame = ttk.LabelFrame(self.root, text=" Ejecución ", padding=5)
        exec_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        # Status visual con colores sobre fondo oscuro
        self.status_label = ctk.CTkLabel(
            exec_frame,
            text=" LISTO ",
            font=("Segoe UI", 9, "bold"),
            text_color="#ffffff",
            fg_color=c["green"],
            corner_radius=4)
        self.status_label.pack(anchor=tk.W, pady=(0, 3))

        # Progress bar + percentage label
        progress_frame = ttk.Frame(exec_frame)
        progress_frame.pack(fill=tk.X, pady=2)

        self.progress = ctk.CTkProgressBar(
            progress_frame, mode="determinate"
        )
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.progress_pct_label = ctk.CTkLabel(progress_frame, text="0%", width=35)
        self.progress_pct_label.pack(side=tk.LEFT, padx=(3, 0))

        # Botones de acción
        ctk.CTkButton(exec_frame, text="▶ Iniciar", command=self._start).pack(
            side=tk.LEFT, padx=2
        )
        ctk.CTkButton(exec_frame, text="▶1 Seleccionado", command=self._run_selected).pack(
            side=tk.LEFT, padx=2
        )
        ctk.CTkButton(exec_frame, text="⏹ Detener", command=self._stop).pack(
            side=tk.LEFT, padx=2
        )

        # Hotkey configurable
        ctk.CTkLabel(exec_frame, text="Hotkey:").pack(side=tk.LEFT, padx=(10, 3))
        self.hotkey_var = tk.StringVar(value=self.hotkey_var_set_to)
        hotkey_combo = ttk.Combobox(
            exec_frame,
            textvariable=self.hotkey_var,
            values=["F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"],
            width=4,
            state="readonly")
        hotkey_combo.pack(side=tk.LEFT, padx=2)
        ctk.CTkLabel(exec_frame, text="(solo ▶ Iniciar todo / ⏹ Detener)").pack(side=tk.LEFT, padx=(3, 0))
        hotkey_combo.bind("<<ComboboxSelected>>", self._on_hotkey_change)

        # Countdown timer
        self.countdown_label = ctk.CTkLabel(exec_frame, text="⏱ --:--")
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
        # Debounce: ignore presses within 500ms to prevent accidental double-tap
        now = time.time()
        last = getattr(self, "_last_hotkey", 0)
        if now - last < 0.5:
            return
        self._last_hotkey = now
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
        if item.get("type") == "macro" and "macro_data" in item:
            total = 0.0
            for act in item["macro_data"].get("actions", []):
                total += act.get("wait_before", 0) + act.get("press_duration", 0.05)
            reps = item.get("repetitions", 1)
            pause = item.get("pause", 0)
            # Sin overhead de lanzamiento para macros, pero con reps y pause
            return max((total + pause) * reps - pause, 0.1)

        reps = item["repetitions"]
        duration = item["duration"]
        pause = item["pause"]
        task_time = (duration + pause) * reps - pause
        overhead = self._LAUNCH_BUFFER * reps
        return max(task_time + overhead, 0)

    def _parse_int(self, var, default=0):
        try:
            return int(var.get())
        except (ValueError, TypeError):
            return default

    def _calc_total_time(self, playlist=None, settings=None):
        if playlist is None:
            # When showing the UI estimate, only count enabled items
            playlist = [item for item in self.playlist if item.get("enabled", True)]
        target = playlist
        # Sum item times (already includes per-launch buffer overhead)
        loop_time = sum(self._calc_item_time(item) for item in target)
        # Add initial sleep overhead (once per run)
        loop_time += self._INITIAL_SLEEP
        # Use settings if provided (from _execute), otherwise read from UI
        if settings:
            mode = settings.get("loop_mode", "once")
            count = settings.get("loop_count", 1) if mode == "fixed" else 1
            delay = settings.get("loop_delay", 0)
        else:
            mode = self.loop_mode_var.get()
            count = self._parse_int(self.loop_count_var, 1) if mode == "fixed" else 1
            delay = self._parse_int(self.loop_delay_var, 0)
        if mode == "infinite" or mode == "first_match":
            return None  # Infinite
        # first_loop_only items count only once regardless of loop count
        once_time = sum(self._calc_item_time(item) for item in target
                        if item.get("first_loop_only", False))
        repeat_time = sum(self._calc_item_time(item) for item in target
                          if not item.get("first_loop_only", False))
        total = once_time + repeat_time * count + delay * max(count - 1, 0)
        return total

    def _update_time_labels(self):
        total = self._calc_total_time()
        if total is None:
            self.total_time_label.configure(text="Total: ∞")
        else:
            self.total_time_label.configure(text=f"Total: {format_time(total)}")

    def _on_loop_mode_change(self, event):
        mode = self.loop_mode_var.get()
        if mode == "infinite" or mode == "first_match":
            self.loop_count_entry.configure(state="disabled")
        else:
            self.loop_count_entry.configure(state="normal")
        self._update_time_labels()

    # ═══════════════════════════════════════════════════════════════
    # PLAYLIST UI
    # ═══════════════════════════════════════════════════════════════

    def _refresh_list(self):
        """Rebuild treeview from flat playlist using group paths for hierarchy."""
        # ── Capture current open/close state of all groups ──
        old_open_state = {}
        for iid, (typ, data) in self._item_map.items():
            if typ == "group":
                old_open_state[data] = self.tree.item(iid, "open")

        # ── First load: restore from persisted settings ──
        if not old_open_state:
            persisted = self.settings.get("collapsed_groups", [])
            for path in persisted:
                old_open_state[path] = False

        for i in self.tree.get_children():
            self.tree.delete(i)
        self._item_map.clear()

        # Tag styles for visual differentiation
        self.tree.tag_configure("group_row",
            font=("Segoe UI", 9, "bold"),
            background="#1a3048",  # azul más intenso para headers de grupo
            foreground="#cdd6f4")
        self.tree.tag_configure("script_grouped",
            font=("Segoe UI", 9),
            background="#1e2a3a",  # azul oscuro — contraste claro con surface
            foreground=DARK_COLORS["text"])
        self.tree.tag_configure("script_ungrouped",
            font=("Segoe UI", 9),
            background=DARK_COLORS["surface"],
            foreground=DARK_COLORS["text"])

        # group_nodes: {group_path: treeview_iid}
        group_nodes = {}

        for idx, item in enumerate(self.playlist):
            group = item.get("group", None)
            item_time = self._calc_item_time(item)
            enabled = item.get("enabled", True)
            check = "✅" if enabled else "❌"
            primero = "🔂" if item.get("first_loop_only", False) else ""
            icono = "🖼️" if item.get("icon_path", "") else ""
            conds = item.get("conditions", {}).get("items", [])
            n_cond = sum(1 for c in conds if c.get("enabled", True))
            cond_text = f"⚙️{n_cond}" if n_cond else ""
            repeat_until_mark = "🔄" if item.get("repeat_until_enabled") and item.get("repeat_until_icon") else ""

            if group:
                parts = group.split("/")
                parent_iid = ""
                current_path = ""

                for depth, part in enumerate(parts):
                    current_path = f"{current_path}/{part}" if current_path else part
                    if current_path not in group_nodes:
                        # Indent subgroup names same formula as scripts
                        group_indent = "  " + "    " * depth
                        group_iid = self.tree.insert(
                            parent_iid, tk.END,
                            text=" ",  # columna árbol necesita contenido para jerarquía
                            values=("", "", "", "", f"{group_indent}📁 {part}", "", "", "", ""),
                            tags=("group_row"),
                            open=old_open_state.get(current_path, True))
                        group_nodes[current_path] = group_iid
                        self._item_map[group_iid] = ("group", current_path)
                    parent_iid = group_nodes[current_path]

                # Script inside a group — tinted background
                # Indent script name: 2 spaces base + 4 per extra nesting depth
                indent = "  " + "    " * (len(parts) - 1)
                script_iid = self.tree.insert(
                    parent_iid, tk.END,
                    text=" ",
                    values=(idx + 1, check, primero, cond_text, f"{indent}{repeat_until_mark}{os.path.basename(item['path'])}",
                            item["repetitions"], item["duration"],
                            item["pause"], format_time(item_time)),
                    tags=("script_grouped"))
            else:
                # Ungrouped — default surface background
                script_iid = self.tree.insert(
                    "", tk.END,
                    text=" ",
                    values=(idx + 1, check, primero, cond_text, f"{repeat_until_mark}{os.path.basename(item['path'])}",
                            item["repetitions"], item["duration"],
                            item["pause"], format_time(item_time)),
                    tags=("script_ungrouped"))
            self._item_map[script_iid] = ("script", idx)

        # ── Update group rows with enabled/primero summary ──
        for path, iid in group_nodes.items():
            indices = [
                i for i, item in enumerate(self.playlist)
                if item.get("group") and
                (item["group"] == path or item["group"].startswith(path + "/"))
            ]
            if indices:
                enabled_count = sum(1 for i in indices if self.playlist[i].get("enabled", True))
                primero_count = sum(1 for i in indices if self.playlist[i].get("first_loop_only", False))
                total = len(indices)
                hab_text = "✅" if enabled_count == total else f"{enabled_count}/{total}"
                primero_text = "🔂" if primero_count == total and total > 0 else (str(primero_count) if primero_count else "")
                current = list(self.tree.item(iid, "values"))
                current[1] = hab_text   # col #2 (hab)
                current[2] = primero_text  # col #3 (primero)
                self.tree.item(iid, values=current)

        self._update_time_labels()

        # ── Persist collapsed group state ──
        collapsed = []
        for iid, (typ, data) in self._item_map.items():
            if typ == "group" and not self.tree.item(iid, "open"):
                collapsed.append(data)
        self.settings["collapsed_groups"] = collapsed

        # Force scrollbar to update (sometimes gets stale after full rebuild)
        self.tree.update_idletasks()

    def _on_tree_click(self, event):
        """Toggle enabled/disabled or first_loop_only when clicking their columns.
        On group rows, toggles all scripts inside the group."""
        region = self.tree.identify_region(event.x, event.y)
        column = self.tree.identify_column(event.x)
        item_id = self.tree.identify_row(event.y)

        if region != "cell" or not item_id:
            return

        info = self._item_map.get(item_id)
        if info is None:
            return

        # ── Group toggle: apply to all scripts in the group ──
        if info[0] == "group":
            group_path = info[1]
            indices = self._get_group_indices(group_path)
            if not indices:
                return
            if column == "#2":  # hab
                any_enabled = any(self.playlist[i].get("enabled", True) for i in indices)
                new_state = not any_enabled
                for i in indices:
                    self.playlist[i]["enabled"] = new_state
                self._refresh_list()
            elif column == "#3":  # primero
                any_primero = any(self.playlist[i].get("first_loop_only", False) for i in indices)
                new_state = not any_primero
                for i in indices:
                    self.playlist[i]["first_loop_only"] = new_state
                self._refresh_list()
            return

        # ── Script toggle ──
        if info[0] != "script":
            return

        idx = info[1]

        if column == "#2":  # hab (enabled/disabled)
            current = self.playlist[idx].get("enabled", True)
            self.playlist[idx]["enabled"] = not current
            self._refresh_list()
        elif column == "#3":  # primero (first_loop_only)
            current = self.playlist[idx].get("first_loop_only", False)
            self.playlist[idx]["first_loop_only"] = not current
            self._refresh_list()
        elif column == "#4":  # condiciones
            self._edit_conditions(idx)

    def _on_group_expand_collapse(self, event):
        """Real-time sync of collapsed state when user clicks expand/collapse arrows."""
        # Update saved collapsed list immediately from current tree state
        collapsed = []
        for iid, (typ, data) in self._item_map.items():
            if typ == "group" and not self.tree.item(iid, "open"):
                collapsed.append(data)
        self.settings["collapsed_groups"] = collapsed

    def _on_tree_double_click(self, event):
        """Inline editing: double-click on reps/duration/pause cell to edit directly.
        Double-click on group header renames the group."""
        self._dismiss_inline_edit()

        region = self.tree.identify_region(event.x, event.y)
        column = self.tree.identify_column(event.x)
        item_id = self.tree.identify_row(event.y)

        info = self._item_map.get(item_id)
        if info is None:
            return

        # ── Double-click on group header → rename ──
        if info[0] == "group":
            self._rename_group_dialog(info[1])
            return

        # ── Script editing ──
        if region != "cell":
            return
        idx = info[1]

        editable_columns = {"#6": "repetitions", "#7": "duration", "#8": "pause"}
        if column not in editable_columns:
            return
        field = editable_columns[column]
        current_value = self.playlist[idx][field]

        # Get cell bounding box
        bbox = self.tree.bbox(item_id, column)
        if not bbox:
            return

        x, y, width, height = bbox

        # Create entry overlay on the cell
        # Use tk.Entry (not CTkEntry) for reliable overlay on ttk.Treeview.
        # CTkEntry as child of Treeview can have rendering/z-order issues
        # after the clam theme borderwidth=0 + rowheight changes.
        c = DARK_COLORS
        entry = tk.Entry(
            self.tree,
            justify="center",
            bg=c["surface_alt"],
            fg=c["text"],
            insertbackground=c["text"],
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        entry.place(x=x, y=y, width=width, height=height)
        entry.insert(0, str(current_value))
        entry.select_range(0, tk.END)
        entry.lift()  # ensure z-order above Treeview internal canvas
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
    # RIGHT-CLICK CONTEXT MENU
    # ═══════════════════════════════════════════════════════════════

    def _on_tree_right_click(self, event):
        """Show context menu on right-click. Does NOT modify selection."""
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return

        info = self._item_map.get(item_id)
        if info is None:
            return

        menu = tk.Menu(self.root, tearoff=0,
                       bg=DARK_COLORS["menu_bg"], fg=DARK_COLORS["menu_fg"],
                       activebackground=DARK_COLORS["menu_active"],
                       activeforeground="#ffffff",
                       font=("Segoe UI", 9))

        if info[0] == "group":
            group_path = info[1]
            menu.add_command(label="📁 Crear subgrupo aquí",
                           command=lambda: self._context_create_subgroup(group_path))
            menu.add_command(label="➕ Agregar scripts al grupo",
                           command=lambda: self._context_add_to_group(group_path))
            menu.add_command(label="🏷️ Renombrar",
                           command=lambda: self._rename_group_dialog(group_path))
            menu.add_separator()
            menu.add_command(label="✂️ Desagrupar todo",
                           command=lambda: self._context_ungroup(group_path))
            menu.add_command(label="🗑️ Eliminar grupo",
                           command=lambda: self._context_remove_group(group_path))

        elif info[0] == "script":
            idx = info[1]
            item = self.playlist[idx]

            if item.get("type") == "macro":
                menu.add_command(label="🎬 Editar macro",
                               command=lambda i=idx: self._edit_macro(i))
            menu.add_command(label="⚙️ Condiciones",
                           command=lambda i=idx: self._edit_conditions(i))
            menu.add_separator()
            menu.add_command(label="📁 Agrupar seleccionados",
                           command=self._group_selected)
            menu.add_command(label="✂️ Desagrupar",
                           command=self._ungroup_selected)

        menu.tk_popup(event.x_root, event.y_root)

    def _context_create_subgroup(self, parent_path):
        """Create a subgroup under parent_path using currently selected scripts."""
        sel = self.tree.selection()
        indices = []
        for iid in sel:
            info = self._item_map.get(iid)
            if info and info[0] == "script":
                indices.append(info[1])

        if not indices:
            self._dark_dialog("Subgrupo",
                "Seleccioná los scripts que querés mover al subgrupo,\n"
                "luego clic derecho en el grupo destino → Crear subgrupo.", "info")
            return

        label = f"Crear subgrupo dentro de «{parent_path}»\nNombre:"
        self._ask_group_name(lambda name: self._do_group(indices, name, parent_path),
                           label=label)

    def _context_add_to_group(self, group_path):
        """Add currently selected scripts to an existing group (right-click menu)."""
        sel = self.tree.selection()
        indices = []
        for iid in sel:
            info = self._item_map.get(iid)
            if info and info[0] == "script":
                indices.append(info[1])

        if not indices:
            self._dark_dialog("Agregar a grupo",
                "Seleccioná los scripts que querés agregar,\n"
                "luego clic derecho en el grupo destino → Agregar scripts.", "info")
            return

        # Filter: only add scripts not already in this group
        existing = set(self._get_group_indices(group_path))
        new_indices = [i for i in indices if i not in existing]
        if not new_indices:
            self._dark_dialog("Ya están",
                "Esos scripts ya pertenecen al grupo «{}».".format(group_path), "info")
            return

        # Assign group to the new scripts
        for idx in new_indices:
            self.playlist[idx]["group"] = group_path

        # Find where the group block currently sits
        all_group = self._get_group_indices(group_path)
        last_group_pos = max(all_group)

        # Extract the new items from their current positions (reverse order)
        new_items = []
        for idx in sorted(new_indices, reverse=True):
            new_items.insert(0, self.playlist.pop(idx))

        # Recalculate insertion point (may have shifted after removals)
        all_group = self._get_group_indices(group_path)
        insert_at = max(all_group) + 1 if all_group else last_group_pos

        # Insert new items after the last existing group item
        for item in new_items:
            self.playlist.insert(insert_at, item)
            insert_at += 1

        self._refresh_list()

    def _context_ungroup(self, group_path):
        """Ungroup all items in a group (via right-click menu)."""
        for idx in self._get_group_indices(group_path):
            self.playlist[idx]["group"] = None
        self._refresh_list()

    def _context_remove_group(self, group_path):
        """Remove all items in a group (via right-click menu)."""
        indices = self._get_group_indices(group_path)
        for i in sorted(indices, reverse=True):
            del self.playlist[i]
        self._refresh_list()

    # ═══════════════════════════════════════════════════════════════
    # DIALOGS
    # ═══════════════════════════════════════════════════════════════

    def _dark_dialog(self, title, message, kind="info"):
        """Custom dark-themed dialog to replace native messagebox."""
        colors = {"info": DARK_COLORS["blue"], "warning": DARK_COLORS["yellow"],
                  "error": DARK_COLORS["red"], "success": DARK_COLORS["green"]}
        accent = colors.get(kind, DARK_COLORS["blue"])

        dlg = ctk.CTkToplevel(self.root, fg_color=DARK_COLORS["bg"])
        # Dark titlebar on dialog
        dlg.after(50, lambda: _apply_dark_titlebar(dlg, retries=3))
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.lift()

        frame = ttk.Frame(dlg, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        ctk.CTkLabel(frame, text=message,
                  wraplength=350, justify=tk.CENTER).pack(pady=(5, 12))

        btn = ctk.CTkButton(frame, text="  Aceptar  ",
                        fg_color=accent, text_color="#ffffff", font=("Segoe UI", 9, "bold"),
                        border_width=0, hover_color=DARK_COLORS["accent_hover"],
                        cursor="hand2", width=20,
                        command=dlg.destroy)
        btn.pack()

        # Center on parent
        dlg.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        dw, dh = dlg.winfo_width(), dlg.winfo_height()
        dlg.geometry(f"+{px + (pw - dw)//2}+{py + (ph - dh)//2}")

        dlg.wait_window()

    def _dark_confirm(self, title, message):
        """Custom dark-themed Yes/No confirmation dialog.
        Returns True if user clicks 'Sí', False otherwise."""
        dlg = ctk.CTkToplevel(self.root, fg_color=DARK_COLORS["bg"])
        dlg.after(50, lambda: _apply_dark_titlebar(dlg, retries=3))
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.lift()

        frame = ttk.Frame(dlg, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        ctk.CTkLabel(frame, text=message,
                  wraplength=350, justify=tk.CENTER).pack(pady=(5, 12))

        result = tk.BooleanVar(value=False)
        btn_row = ttk.Frame(frame)
        btn_row.pack()
        ctk.CTkButton(btn_row, text="  Sí, eliminar  ",
                  fg_color=DARK_COLORS["red"], text_color="#ffffff",
                  font=("Segoe UI", 9, "bold"), border_width=0,
                  hover_color="#d45555", cursor="hand2", width=20,
                  command=lambda: (result.set(True), dlg.destroy())
                  ).pack(side=tk.LEFT, padx=(0, 8))
        ctk.CTkButton(btn_row, text="  Cancelar  ",
                  fg_color=DARK_COLORS["surface_alt"], text_color=DARK_COLORS["text"],
                  font=("Segoe UI", 9), border_width=1,
                  border_color=DARK_COLORS["border"],
                  hover_color=DARK_COLORS["border"], cursor="hand2", width=20,
                  command=dlg.destroy
                  ).pack(side=tk.LEFT)

        # Center on parent
        dlg.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        dw, dh = dlg.winfo_width(), dlg.winfo_height()
        dlg.geometry(f"+{px + (pw - dw)//2}+{py + (ph - dh)//2}")

        dlg.wait_window()
        return result.get()

    # ═══════════════════════════════════════════════════════════════
    # CONDICIONES (iconos)
    # ═══════════════════════════════════════════════════════════════

    def _edit_conditions(self, idx):
        """Abrir ventana editor de condiciones para un script."""
        item = self.playlist[idx]
        name = os.path.basename(item["path"])
        conditions = item.get("conditions", {"mode": "and", "items": [], "action": "require"})

        # ── Migrar campos antiguos repeat_until_* si existen ──
        if item.get("repeat_until_enabled") and item.get("repeat_until_icon"):
            old_icon = item["repeat_until_icon"]
            old_mode = item.get("repeat_until_mode", "match")
            old_thresh = item.get("repeat_until_threshold", 0.08)
            old_max = item.get("repeat_until_max_iterations", 0)
            conditions = {
                "action": "repeat_until",
                "mode": "and",
                "items": [{
                    "type": "require",
                    "icon_path": old_icon,
                    "label": os.path.basename(old_icon),
                    "threshold": old_thresh,
                }],
                "repeat": {
                    "stop_when": old_mode,
                    "max_iterations": old_max,
                    "check_interval": item.get("repeat_until_check_interval", 0.5),
                },
                "retry": {"enabled": False, "count": 3, "delay": 5},
                "fallback": {"enabled": False, "threshold": 3, "script": "", "delay_after": 0},
            }
            # Limpiar campos antiguos
            for k in ("repeat_until_enabled", "repeat_until_mode", "repeat_until_icon",
                      "repeat_until_threshold", "repeat_until_max_iterations",
                      "repeat_until_check_interval"):
                item.pop(k, None)

        # Trabajar con copia para no modificar hasta Guardar
        cond_copy = {
            "action": conditions.get("action", "require"),
            "mode": conditions.get("mode", "and"),
            "items": [dict(c) for c in conditions.get("items", [])],
            "repeat": dict(conditions.get("repeat", {
                "stop_when": "match",
                "max_iterations": 0,
                "check_interval": 0.5,
            })),
            "retry": dict(conditions.get("retry", {"enabled": False, "count": 3, "delay": 5})),
            "fallback": dict(conditions.get("fallback", {"enabled": False, "threshold": 3, "type": "exe", "script": "", "macro_name": "", "delay_after": 0})),
        }

        dlg = ctk.CTkToplevel(self.root, fg_color=DARK_COLORS["bg"])
        dlg.title(f"Condiciones — {name}")
        dlg.minsize(700, 500)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.lift()
        _apply_dark_titlebar(dlg, retries=3)

        # Restore saved geometry or default + center
        self._restore_win_geometry(dlg, "condition_editor_geometry", "750x620")
        self._bind_geo_save(dlg, "condition_editor_geometry")

        pad = {"padx": 8, "pady": 4}
        c = DARK_COLORS

        # ── Modo AND/OR ──
        mode_frame = ttk.Frame(dlg)
        mode_frame.pack(fill=tk.X, **pad)
        ctk.CTkLabel(mode_frame, text="Modo:").pack(side=tk.LEFT)
        mode_var = tk.StringVar(value=cond_copy["mode"])
        mode_combo = ttk.Combobox(mode_frame, textvariable=mode_var,
                                  values=["and", "or"], width=6, state="readonly")
        mode_combo.pack(side=tk.LEFT, padx=6)

        mode_help = {
            "and": "⚠️ TODAS las condiciones deben cumplirse para ejecutar",
            "or":  "✅ AL MENOS UNA condición debe cumplirse para ejecutar",
        }

        mode_hint = ctk.CTkLabel(mode_frame, text=mode_help.get(cond_copy["mode"], ""))
        mode_hint.pack(side=tk.LEFT)

        def _on_mode_change(*_args):
            mode_hint.configure(text=mode_help.get(mode_var.get(), ""))

        mode_var.trace_add("write", _on_mode_change)

        # ── Acción (requerir vs repetir hasta) ──
        action_frame = ttk.Frame(dlg)
        action_frame.pack(fill=tk.X, **pad)
        ctk.CTkLabel(action_frame, text="Acción:").pack(side=tk.LEFT)
        action_var = tk.StringVar(value=cond_copy["action"])
        action_combo = ttk.Combobox(action_frame, textvariable=action_var,
                                     values=["require", "repeat_until"],
                                     width=14, state="readonly")
        action_combo.pack(side=tk.LEFT, padx=6)
        action_labels = {
            "require": "✅ Requerir — solo ejecutar si se cumplen",
            "repeat_until": "🔄 Repetir hasta — ejecutar hasta que se cumplan",
        }
        action_hint = ctk.CTkLabel(action_frame,
                                 text=action_labels.get(cond_copy["action"], ""))
        action_hint.pack(side=tk.LEFT)

        def _on_action_change(*_args):
            action_hint.configure(
                text=action_labels.get(action_var.get(), ""))
            _toggle_repeat_ui()

        action_var.trace_add("write", _on_action_change)

        # ── Repeat-until settings (visible solo cuando action=repeat_until) ──
        ru_frame = ttk.LabelFrame(dlg, text="🔄 Configuración de repetición", padding=5)

        ru_stop_var = tk.StringVar(value=cond_copy["repeat"]["stop_when"])
        ru_stop_row = ttk.Frame(ru_frame)
        ru_stop_row.pack(fill=tk.X)
        ctk.CTkLabel(ru_stop_row, text="Detener al:").pack(side=tk.LEFT)
        ctk.CTkRadioButton(ru_stop_row, text="Encontrar", variable=ru_stop_var,
                        value="match").pack(side=tk.LEFT, padx=(4, 0))
        ctk.CTkRadioButton(ru_stop_row, text="Desaparecer", variable=ru_stop_var,
                        value="no_match").pack(side=tk.LEFT, padx=2)

        ru_max_var = tk.IntVar(value=cond_copy["repeat"]["max_iterations"])
        ru_max_row = ttk.Frame(ru_frame)
        ru_max_row.pack(fill=tk.X, pady=(4, 0))
        ctk.CTkLabel(ru_max_row, text="Máx. intentos:").pack(side=tk.LEFT)
        ttk.Spinbox(ru_max_row, from_=0, to=99999,
                    textvariable=ru_max_var, width=8).pack(side=tk.LEFT, padx=4)
        ctk.CTkLabel(ru_max_row, text="(0=sin límite)").pack(side=tk.LEFT)

        ru_interval_var = tk.DoubleVar(
            value=cond_copy["repeat"]["check_interval"])
        ru_interval_row = ttk.Frame(ru_frame)
        ru_interval_row.pack(fill=tk.X, pady=(4, 0))
        ctk.CTkLabel(ru_interval_row,
                  text="Intervalo de verificación (s):").pack(side=tk.LEFT)
        ttk.Spinbox(ru_interval_row, from_=0.1, to=60.0,
                    increment=0.1,
                    textvariable=ru_interval_var,
                    width=8).pack(side=tk.LEFT, padx=4)

        def _toggle_repeat_ui():
            if action_var.get() == "repeat_until":
                ru_frame.pack(fill=tk.X, padx=8, pady=(6, 0))
            else:
                ru_frame.pack_forget()

        _toggle_repeat_ui()

        # ── Lista de condiciones ──
        list_frame = ttk.Frame(dlg)
        list_frame.pack(fill=tk.BOTH, expand=True, **pad)

        columns = ("hab", "tipo", "label", "icono", "umbral")
        tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=6)
        tree.heading("hab", text="✓")
        tree.heading("tipo", text="Tipo")
        tree.heading("label", text="Etiqueta")
        tree.heading("icono", text="Icono")
        tree.heading("umbral", text="Tolerancia")
        tree.column("hab", width=28, anchor="center")
        tree.column("tipo", width=72, anchor="center")
        tree.column("label", width=140, anchor="w")
        tree.column("icono", width=100, anchor="center")
        tree.column("umbral", width=60, anchor="center")
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        vsb = ttk.Scrollbar(list_frame, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        def _refresh_cond_list():
            for i in tree.get_children():
                tree.delete(i)
            for ci, cond in enumerate(cond_copy["items"]):
                ctype = cond.get("type", "require")
                label = cond.get("label", "")
                ipath = cond.get("icon_path", "")
                enabled = cond.get("enabled", True)
                hab_display = "✅" if enabled else "❌"
                tipo_display = "Requerir" if ctype == "require" else "Bloquear"
                icon_display = os.path.basename(ipath) if ipath else "(sin icono)"
                umbral = cond.get("threshold", 0.08)
                samples = cond.get("samples", 1)
                confidence = cond.get("confidence", 1.0)
                if samples > 1:
                    umbral_display = f"{umbral:.2f} | {samples}×{int(confidence*100)}%"
                else:
                    umbral_display = f"{umbral:.2f}"
                tree.insert("", tk.END, iid=str(ci),
                           values=(hab_display, tipo_display, label, icon_display, umbral_display))

        _refresh_cond_list()

        # ── Click en columna ✓ para habilitar/deshabilitar ──
        def _on_tree_click(event):
            col = tree.identify_column(event.x)
            item_id = tree.identify_row(event.y)
            if not item_id or col != "#1":  # columna hab
                return
            i = int(item_id)
            if 0 <= i < len(cond_copy["items"]):
                current = cond_copy["items"][i].get("enabled", True)
                cond_copy["items"][i]["enabled"] = not current
                _refresh_cond_list()

        tree.bind("<ButtonRelease-1>", _on_tree_click)

        # ── Botones de acción (fila A) ──
        btn_frame_a = ttk.Frame(dlg)
        btn_frame_a.pack(fill=tk.X, **pad)

        def _add_condition():
            """Abre captura de pantalla — si hay múltiples monitores, pregunta cuál usar."""
            import mss
            from PIL import Image

            with mss.mss() as sct:
                monitors = list(sct.monitors)  # [0]=virtual, [1..N]=físicos

            num_physical = len(monitors) - 1  # sin contar el virtual

            def _do_capture(monitor):
                """Captura `monitor` y abre overlay de selección encima de él."""
                nonlocal monitors
                try:
                    with mss.mss() as sct2:
                        screen = sct2.grab(monitor)
                        img2 = Image.frombytes("RGB", screen.size, screen.bgra, "raw", "BGRX")
                except Exception:
                    from PIL import ImageGrab
                    img2 = ImageGrab.grab(all_screens=True)

                cap_win = ctk.CTkToplevel(dlg, fg_color="black")
                # Posicionar en el monitor destino ANTES de fullscreen
                cap_win.geometry(f"+{monitor['left']}+{monitor['top']}")
                cap_win.attributes("-fullscreen", True)
                cap_win.attributes("-topmost", True)
                cap_win.configure(cursor="crosshair")

                from PIL import ImageTk
                photo = ImageTk.PhotoImage(img2)
                canvas = tk.Canvas(cap_win, bg="black", highlightthickness=0)
                canvas.pack(fill=tk.BOTH, expand=True)
                canvas.create_image(0, 0, anchor=tk.NW, image=photo)
                canvas._photo_ref = photo

                rect = None
                start = [0, 0]

                def on_down(e):
                    start[0], start[1] = e.x, e.y
                    nonlocal rect
                    if rect:
                        canvas.delete(rect)
                    rect = canvas.create_rectangle(e.x, e.y, e.x, e.y,
                        outline="#7c7cf8", width=2, dash=(4,2))

                def on_drag(e):
                    nonlocal rect
                    if rect:
                        canvas.coords(rect, start[0], start[1], e.x, e.y)

                def on_up(e):
                    x1, y1 = min(start[0], e.x), min(start[1], e.y)
                    x2, y2 = max(start[0], e.x), max(start[1], e.y)
                    cap_win.destroy()
                    if x2 - x1 < 10 or y2 - y1 < 10:
                        return
                    # Recortar y guardar
                    cropped = img2.crop((x1, y1, x2, y2))
                    icons_dir = os.path.join(os.path.dirname(get_config_path()), "icons")
                    os.makedirs(icons_dir, exist_ok=True)
                    fname = f"cond_{idx}_{len(cond_copy['items'])}_{int(time.time())}.png"
                    save_path = os.path.join(icons_dir, fname)
                    cropped.save(save_path)

                    # ── Guardar región de búsqueda (coord absolutas + padding) ──
                    m_left = monitor.get("left", monitor.get("x", 0))
                    m_top = monitor.get("top", monitor.get("y", 0))
                    padding = 80  # margen extra por si el icono se mueve
                    search_x = max(0, x1 + m_left - padding)
                    search_y = max(0, y1 + m_top - padding)
                    search_w = (x2 - x1) + padding * 2
                    search_h = (y2 - y1) + padding * 2

                    cond_copy["items"].append({
                        "type": "require",
                        "icon_path": save_path,
                        "label": "",
                        "threshold": 0.08,
                        "samples": 1,
                        "confidence": 1.0,
                        "region": [search_x, search_y, search_w, search_h],
                        "enabled": True,
                    })
                    _refresh_cond_list()

                canvas.bind("<ButtonPress-1>", on_down)
                canvas.bind("<B1-Motion>", on_drag)
                canvas.bind("<ButtonRelease-1>", on_up)
                cap_win.bind("<Escape>", lambda e: cap_win.destroy())

                canvas.create_text(img2.width // 2, 30,
                    text="Selecciona el área del icono (clic + arrastrar) | ESC para cancelar",
                    fill="#cdd6f4", font=("Segoe UI", 11, "bold"), anchor=tk.N)

            if num_physical <= 1:
                _do_capture(monitors[1])
                return

            # ── Más de un monitor: mostrar selector ──
            sel_win = ctk.CTkToplevel(dlg)
            sel_win.title("Seleccionar monitor")
            sel_win.configure(bg="#1e1e2e")
            sel_win.resizable(False, False)

            ctk.CTkLabel(sel_win, text="¿De qué monitor capturar?",
                      font=("Segoe UI", 11, "bold"),
                      background="#1e1e2e", foreground="#cdd6f4").pack(
                          pady=(15, 10), padx=30)

            btn_row = ttk.Frame(sel_win)
            btn_row.pack(pady=(0, 15))

            for i in range(1, num_physical + 1):
                m = monitors[i]
                size = f"{m['width']}×{m['height']}"
                btn = ctk.CTkButton(btn_row,
                    text=f"Monitor {i}\n({size})",
                    command=lambda m=m: [_do_capture(m), sel_win.destroy()])
                btn.pack(side=tk.LEFT, padx=5)

            # Centrar sobre el diálogo padre
            sel_win.transient(dlg)
            sel_win.grab_set()
            sel_win.update_idletasks()
            px = dlg.winfo_rootx() + (dlg.winfo_width() - sel_win.winfo_width()) // 2
            py = dlg.winfo_rooty() + (dlg.winfo_height() - sel_win.winfo_height()) // 2
            sel_win.geometry(f"+{px}+{py}")

        def _remove_condition():
            sel = tree.selection()
            if not sel:
                return
            indices = sorted([int(s) for s in sel], reverse=True)
            for i in indices:
                if 0 <= i < len(cond_copy["items"]):
                    # Delete the icon file
                    ipath = cond_copy["items"][i].get("icon_path", "")
                    if ipath and os.path.exists(ipath):
                        try:
                            os.remove(ipath)
                        except OSError:
                            pass
                    del cond_copy["items"][i]
            _refresh_cond_list()

        def _toggle_type():
            sel = tree.selection()
            if not sel:
                return
            i = int(sel[0])
            if 0 <= i < len(cond_copy["items"]):
                current = cond_copy["items"][i]["type"]
                cond_copy["items"][i]["type"] = "block" if current == "require" else "require"
                _refresh_cond_list()

        def _edit_label():
            sel = tree.selection()
            if not sel:
                return
            i = int(sel[0])
            if 0 <= i < len(cond_copy["items"]):
                # Simple entry popup
                lbl_win = ctk.CTkToplevel(dlg, fg_color=c["bg"])
                lbl_win.title("Etiqueta")
                lbl_win.geometry("260x80")
                lbl_win.transient(dlg)
                lbl_win.grab_set()
                lbl_var = tk.StringVar(value=cond_copy["items"][i].get("label", ""))
                entry = ctk.CTkEntry(lbl_win, textvariable=lbl_var, width=30)
                entry.pack(padx=10, pady=(10, 5))
                entry.select_range(0, tk.END)
                entry.focus_set()

                def _save_label():
                    cond_copy["items"][i]["label"] = lbl_var.get().strip()
                    lbl_win.destroy()
                    _refresh_cond_list()

                entry.bind("<Return>", lambda e: _save_label())
                ctk.CTkButton(lbl_win, text="Guardar", command=_save_label).pack()

        ctk.CTkButton(btn_frame_a, text="➕ Agregar", command=_add_condition).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(btn_frame_a, text="🗑️ Quitar", command=_remove_condition).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(btn_frame_a, text="🔄 Cambiar tipo", command=_toggle_type).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(btn_frame_a, text="🏷️ Etiqueta", command=_edit_label).pack(side=tk.LEFT, padx=2)

        def _edit_threshold():
            """Editar la tolerancia de la condición seleccionada."""
            sel = tree.selection()
            if not sel:
                return
            i = int(sel[0])
            if 0 <= i < len(cond_copy["items"]):
                current = cond_copy["items"][i].get("threshold", 0.08)
                thr_win = ctk.CTkToplevel(dlg, fg_color=c["bg"])
                thr_win.title("Tolerancia")
                thr_win.geometry("240x80")
                thr_win.transient(dlg)
                thr_win.grab_set()
                thr_var = tk.DoubleVar(value=current)
                row = ttk.Frame(thr_win)
                row.pack(padx=10, pady=(10, 5))
                ctk.CTkLabel(row, text="Tolerancia (0-1):").pack(side=tk.LEFT)
                ttk.Spinbox(row, from_=0.01, to=1.0, increment=0.01,
                            textvariable=thr_var, width=6).pack(side=tk.LEFT, padx=4)

                def _save_thr():
                    cond_copy["items"][i]["threshold"] = thr_var.get()
                    thr_win.destroy()
                    _refresh_cond_list()

                ctk.CTkButton(thr_win, text="Guardar", command=_save_thr).pack()
                thr_win.bind("<Return>", lambda e: _save_thr())
                # Center on conditions dialog
                thr_win.update_idletasks()
                pdx = dlg.winfo_rootx() + (dlg.winfo_width() - thr_win.winfo_width()) // 2
                pdy = dlg.winfo_rooty() + (dlg.winfo_height() - thr_win.winfo_height()) // 2
                thr_win.geometry(f"+{pdx}+{pdy}")

        ctk.CTkButton(btn_frame_a, text="🎯 Tolerancia", command=_edit_threshold).pack(side=tk.LEFT, padx=2)

        # ── Botones de acción (fila B) ──
        btn_frame_b = ttk.Frame(dlg)
        btn_frame_b.pack(fill=tk.X, **pad)

        # ── Botón Muestreo ──
        def _edit_sampling():
            sel = tree.selection()
            if not sel:
                return
            i = int(sel[0])
            if i < 0 or i >= len(cond_copy["items"]):
                return

            smp_win = ctk.CTkToplevel(dlg, fg_color=c["bg"])
            smp_win.title("Muestreo")
            smp_win.geometry("280x160")
            smp_win.resizable(False, False)
            smp_win.transient(dlg)
            smp_win.grab_set()

            cur_samples = cond_copy["items"][i].get("samples", 1)
            cur_conf = cond_copy["items"][i].get("confidence", 1.0)

            ctk.CTkLabel(smp_win, text="Verificar N veces:").pack(pady=(10, 2))
            samples_var = tk.IntVar(value=cur_samples)
            ttk.Spinbox(smp_win, from_=1, to=20, width=6,
                        textvariable=samples_var).pack()

            ctk.CTkLabel(smp_win, text="Aceptar si ≥ X% matchea:").pack(pady=(10, 2))
            conf_pct_var = tk.IntVar(value=int(cur_conf * 100))
            ttk.Spinbox(smp_win, from_=10, to=100, increment=10, width=6,
                        textvariable=conf_pct_var).pack()
            ctk.CTkLabel(smp_win, text="%").pack()

            ctk.CTkLabel(smp_win,
                text=f"Ej: 5 veces al 60% → necesita 3+ aciertos").pack(pady=(6, 0))

            def _save_smp():
                cond_copy["items"][i]["samples"] = samples_var.get()
                cond_copy["items"][i]["confidence"] = conf_pct_var.get() / 100.0
                smp_win.destroy()
                _refresh_cond_list()

            ctk.CTkButton(smp_win, text="Guardar", command=_save_smp).pack(pady=8)
            smp_win.bind("<Return>", lambda e: _save_smp())
            smp_win.update_idletasks()
            pdx = dlg.winfo_rootx() + (dlg.winfo_width() - smp_win.winfo_width()) // 2
            pdy = dlg.winfo_rooty() + (dlg.winfo_height() - smp_win.winfo_height()) // 2
            smp_win.geometry(f"+{pdx}+{pdy}")

        ctk.CTkButton(btn_frame_b, text="📊 Muestreo", command=_edit_sampling).pack(side=tk.LEFT, padx=2)

        def _preview_icon():
            """Abrir la imagen del icono seleccionado con el visor del sistema."""
            sel = tree.selection()
            if not sel:
                return
            i = int(sel[0])
            if 0 <= i < len(cond_copy["items"]):
                ipath = cond_copy["items"][i].get("icon_path", "")
                if ipath and os.path.exists(ipath):
                    os.startfile(ipath)
                else:
                    self._dark_dialog("Icono no encontrado",
                        f"El archivo no existe:\n{ipath}", "warning")

        ctk.CTkButton(btn_frame_b, text="👁️ Ver", command=_preview_icon).pack(side=tk.LEFT, padx=2)

        # ── Botón Probar (diagnóstico) ──
        def _test_icon():
            """Diagnostica el icono: 5 capturas rapidas, muestra
            min/max/avg y tasa de acierto real a la tolerancia actual."""
            sel = tree.selection()
            if not sel:
                self._dark_dialog("Seleccionar",
                    "Selecciona una condición de la lista para probar.", "info")
                return
            i = int(sel[0])
            if i < 0 or i >= len(cond_copy["items"]):
                return
            cond = cond_copy["items"][i]
            ipath = cond.get("icon_path", "")
            if not ipath or not os.path.exists(ipath):
                self._dark_dialog("Sin icono",
                    "Esta condición no tiene un icono asignado.", "warning")
                return

            current_threshold = cond.get("threshold", 0.08)
            search_region = cond.get("region")

            from icon_detector import diagnose_icon

            # ── 5 tests rápidos ──
            diffs = []
            hits = 0
            for test_n in range(1, 6):
                self._set_status(
                    f"🔍 Probando... {test_n}/5", DARK_COLORS["purple"])
                dlg.update()
                try:
                    result = diagnose_icon(
                        ipath, region=search_region,
                        threshold=current_threshold)
                except Exception as e:
                    self._dark_dialog("Error",
                        f"No se pudo probar el icono:\n{e}", "error")
                    return
                d = result["min_diff"]
                if d < 1.0:
                    diffs.append(d)
                if result["found"]:
                    hits += 1

            if not diffs:
                self._dark_dialog("No encontrado",
                    "No se encontró el icono en ninguna de las 5 pruebas.\n"
                    "¿Está visible en pantalla?", "warning")
                return

            d_min = min(diffs)
            d_max = max(diffs)
            d_avg = sum(diffs) / len(diffs)
            spread = d_max - d_min
            rate = hits / 5 * 100

            # ── Determinar tolerancia segura ──
            safe_tolerance = round(d_max * 1.15, 3)

            # ── Construir mensaje ──
            if rate >= 80:
                emoji, status = "✅", "CONFIABLE"
                msg_type = "success"
            elif rate >= 40:
                emoji, status = "⚠️", "INESTABLE"
                msg_type = "info"
            else:
                emoji, status = "❌", "NO CONFIABLE"
                msg_type = "info"

            msg = (
                f"{emoji} Tasa de acierto: {rate:.0f}% ({hits}/5) — {status}\n\n"
                f"📊 Min diff:  {d_min:.4f} ({d_min*100:.1f}%)\n"
                f"📊 Max diff:  {d_max:.4f} ({d_max*100:.1f}%)\n"
                f"📊 Avg diff:  {d_avg:.4f} ({d_avg*100:.1f}%)\n"
                f"📐 Variación: {spread:.4f} ({(spread/d_avg)*100:.0f}% de dispersión)\n\n"
                f"📏 Tolerancia actual: {current_threshold:.3f}\n"
                f"💡 Tolerancia SEGURA:  {safe_tolerance:.3f}\n"
                f"   (max diff + 15% margen)\n"
            )

            if search_region:
                msg += f"\n🎯 Con región de búsqueda"
            else:
                msg += f"\n🌐 Buscando en toda la pantalla"

            if rate < 100:
                msg += (
                    f"\n\n⚠️ El juego NO renderiza igual cada frame.\n"
                    f"Usá la tolerancia SEGURA ({safe_tolerance:.3f})\n"
                    f"para cubrir el peor caso."
                )

            self._dark_dialog(
                f"Diagnóstico — {os.path.basename(ipath)}",
                msg, msg_type
            )

        ctk.CTkButton(btn_frame_b, text="🔍 Probar", command=_test_icon).pack(side=tk.LEFT, padx=2)

        # ── Botón Probar TODAS las condiciones ──
        def _test_all():
            """Evalúa todas las condiciones juntas (AND/OR, require/block)
            y muestra cuáles pasan, cuáles fallan, y el resultado final."""
            items = cond_copy["items"]
            if not items:
                self._dark_dialog("Sin condiciones",
                    "No hay condiciones para evaluar.", "info")
                return

            # Filtrar solo habilitadas
            enabled_items = [c for c in items if c.get("enabled", True)]
            if not enabled_items:
                self._dark_dialog("Sin condiciones habilitadas",
                    "Todas las condiciones están deshabilitadas.\nEl script se ejecutará sin restricciones.", "info")
                return

            mode = cond_copy.get("mode", "and")
            action = cond_copy.get("action", "require")

            from icon_detector import diagnose_icon

            self._set_status("🔍 Evaluando todas...", DARK_COLORS["purple"])
            dlg.update()

            results = []
            for ci, cond in enumerate(enabled_items):
                ipath = cond.get("icon_path", "")
                ctype = cond.get("type", "require")
                threshold = cond.get("threshold", 0.08)
                region = cond.get("region")
                label = cond.get("label", "") or os.path.basename(ipath)

                if not ipath or not os.path.exists(ipath):
                    results.append((label, ctype, None, "sin icono"))
                    continue

                r = diagnose_icon(ipath, region=region, threshold=threshold)
                results.append((label, ctype, r["found"], r["min_diff"]))

            # Evaluar lógica
            passed_list = []
            for label, ctype, found, diff in results:
                if ctype == "require":
                    p = found if found is not None else False
                else:  # block
                    p = not found if found is not None else True
                passed_list.append(p)

            if mode == "or":
                overall = any(passed_list)
            else:
                overall = all(passed_list)

            # ── Construir mensaje ──
            lines = []
            if action == "repeat_until":
                lines.append("🔄 Modo: REPETIR HASTA")
            else:
                lines.append("✅ Modo: REQUERIR (solo ejecutar si cumple)")
            lines.append(f"🧩 Combinación: {mode.upper()}")
            lines.append("")

            for i, ((label, ctype, found, diff), p) in enumerate(zip(results, passed_list)):
                emoji = "✅" if p else "❌"
                if found is None:
                    status = "⚠️ sin icono"
                elif ctype == "require":
                    status = "visible" if found else f"NO visible (diff={diff:.3f})"
                elif ctype == "block":
                    status = "bloquea (visible)" if found else f"permite (NO visible, diff={diff:.3f})"
                lines.append(f"{emoji} [{ctype}] {label}: {status}")

            lines.append("")
            if overall:
                lines.append(f"✅ RESULTADO: condiciones CUMPLIDAS → script se ejecuta")
            else:
                lines.append(f"❌ RESULTADO: condiciones NO cumplidas → script NO se ejecuta")

            self._dark_dialog(
                "Evaluación de condiciones",
                "\n".join(lines),
                "success" if overall else "info"
            )

        ctk.CTkButton(btn_frame_b, text="🧪 Todas", command=_test_all).pack(side=tk.LEFT, padx=2)

        # ── Reintentos ──
        retry_frame = ttk.LabelFrame(dlg, text="⏳ Reintentos", padding=5)
        retry_frame.pack(fill=tk.X, padx=8, pady=(8, 0))

        retry_enabled_var = tk.BooleanVar(value=cond_copy["retry"]["enabled"])
        retry_count_var = tk.IntVar(value=cond_copy["retry"]["count"])
        retry_delay_var = tk.IntVar(value=cond_copy["retry"]["delay"])

        retry_row = ttk.Frame(retry_frame)
        retry_row.pack(fill=tk.X)
        ctk.CTkCheckBox(retry_row, text="Reintentar si no se cumple",
                        variable=retry_enabled_var).pack(side=tk.LEFT)
        ctk.CTkLabel(retry_row, text="  cada").pack(side=tk.LEFT)
        ttk.Spinbox(retry_row, from_=1, to=60, width=4,
                    textvariable=retry_delay_var).pack(side=tk.LEFT)
        ctk.CTkLabel(retry_row, text="seg, hasta").pack(side=tk.LEFT)
        ttk.Spinbox(retry_row, from_=1, to=30, width=4,
                    textvariable=retry_count_var).pack(side=tk.LEFT)
        ctk.CTkLabel(retry_row, text="veces").pack(side=tk.LEFT)

        # ── Script de recuperación ──
        fallback_frame = ttk.LabelFrame(dlg, text="🆘 Script de recuperación", padding=5)
        fallback_frame.pack(fill=tk.X, padx=8, pady=(8, 0))

        fallback_enabled_var = tk.BooleanVar(value=cond_copy["fallback"]["enabled"])
        fallback_threshold_var = tk.IntVar(value=cond_copy["fallback"]["threshold"])
        fallback_script_var = tk.StringVar(value=cond_copy["fallback"]["script"])
        fallback_delay_var = tk.IntVar(value=cond_copy["fallback"].get("delay_after", 0))
        fallback_type_var = tk.StringVar(value=cond_copy["fallback"].get("type", "exe"))
        fallback_macro_name = cond_copy["fallback"].get("macro_name", "")

        fb_row1 = ttk.Frame(fallback_frame)
        fb_row1.pack(fill=tk.X)
        ctk.CTkCheckBox(fb_row1, text="Si falla",
                        variable=fallback_enabled_var).pack(side=tk.LEFT)
        ttk.Spinbox(fb_row1, from_=1, to=30, width=4,
                    textvariable=fallback_threshold_var).pack(side=tk.LEFT)
        ctk.CTkLabel(fb_row1, text="veces seguidas, ejecutar:").pack(side=tk.LEFT)

        # ── Tipo: Exe o Macro ──
        fb_type_row = ttk.Frame(fallback_frame)
        fb_type_row.pack(fill=tk.X, pady=(4, 0))
        ctk.CTkLabel(fb_type_row, text="Tipo:").pack(side=tk.LEFT)
        ctk.CTkRadioButton(fb_type_row, text="Externo (.exe)", variable=fallback_type_var,
                        value="exe", command=lambda: _toggle_fb_type()).pack(side=tk.LEFT, padx=(4, 8))
        ctk.CTkRadioButton(fb_type_row, text="Macro", variable=fallback_type_var,
                        value="macro", command=lambda: _toggle_fb_type()).pack(side=tk.LEFT)

        # ── Selector de exe ──
        fb_exe_row = ttk.Frame(fallback_frame)

        fb_script_label = ctk.CTkLabel(fb_exe_row, textvariable=fallback_script_var, width=280)
        fb_script_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def _pick_fallback():
            path = filedialog.askopenfilename(
                title="Seleccionar script de recuperación",
                filetypes=[("Ejecutables", "*.exe"), ("Todos", "*.*")])
            if path:
                fallback_script_var.set(path)

        ctk.CTkButton(fb_exe_row, text="📂 Elegir", command=_pick_fallback).pack(side=tk.RIGHT, padx=(4, 0))

        # ── Selector de macro ──
        fb_macro_row = ttk.Frame(fallback_frame)
        ctk.CTkLabel(fb_macro_row, text="Macro:").pack(side=tk.LEFT)

        # Recopilar macros de la playlist
        macro_names = ["(ninguna)"]
        for pl_item in self.playlist:
            if pl_item.get("type") == "macro":
                md = pl_item.get("macro_data", {})
                mn = md.get("name", "")
                if mn:
                    macro_names.append(mn)
        fb_macro_combo_var = tk.StringVar(value=fallback_macro_name if fallback_macro_name in macro_names else "(ninguna)")
        fb_macro_combo = ttk.Combobox(fb_macro_row, textvariable=fb_macro_combo_var,
                                       values=macro_names, width=30, state="readonly")
        fb_macro_combo.pack(side=tk.LEFT, padx=4)

        def _toggle_fb_type():
            if fallback_type_var.get() == "macro":
                fb_exe_row.pack_forget()
                fb_macro_row.pack(fill=tk.X, pady=(4, 0))
            else:
                fb_macro_row.pack_forget()
                fb_exe_row.pack(fill=tk.X, pady=(4, 0))

        _toggle_fb_type()  # estado inicial

        fb_row3 = ttk.Frame(fallback_frame)
        fb_row3.pack(fill=tk.X, pady=(4, 0))
        ctk.CTkLabel(fb_row3, text="Esperar").pack(side=tk.LEFT)
        ttk.Spinbox(fb_row3, from_=0, to=999, width=5,
                    textvariable=fallback_delay_var).pack(side=tk.LEFT, padx=3)
        ctk.CTkLabel(fb_row3, text="s tras recuperación antes de continuar").pack(side=tk.LEFT)

        # ── Guardar / Cancelar ──
        bottom = ttk.Frame(dlg)
        bottom.pack(fill=tk.X, **pad)

        def _save():
            cond_copy["action"] = action_var.get()
            cond_copy["mode"] = mode_var.get()
            cond_copy["repeat"] = {
                "stop_when": ru_stop_var.get(),
                "max_iterations": ru_max_var.get(),
                "check_interval": ru_interval_var.get(),
            }
            cond_copy["retry"] = {
                "enabled": retry_enabled_var.get(),
                "count": retry_count_var.get(),
                "delay": retry_delay_var.get(),
            }
            cond_copy["fallback"] = {
                "enabled": fallback_enabled_var.get(),
                "threshold": fallback_threshold_var.get(),
                "type": fallback_type_var.get(),
                "script": fallback_script_var.get(),
                "macro_name": fb_macro_combo_var.get() if fallback_type_var.get() == "macro" else "",
                "delay_after": fallback_delay_var.get(),
            }
            item["conditions"] = {
                "action": cond_copy["action"],
                "mode": cond_copy["mode"],
                "items": cond_copy["items"],
                "repeat": cond_copy["repeat"],
                "retry": cond_copy["retry"],
                "fallback": cond_copy["fallback"],
            }
            # Limpiar campos antiguos de repeat_until si existían
            for k in ("repeat_until_enabled", "repeat_until_mode", "repeat_until_icon",
                      "repeat_until_threshold", "repeat_until_max_iterations",
                      "repeat_until_check_interval"):
                item.pop(k, None)
            self._refresh_list()
            dlg.destroy()

        def _clear_all():
            if self._dark_confirm("Limpiar",
                                   "¿Eliminar todas las condiciones?\n\n"
                                   "También se borrarán las imágenes capturadas."):
                for c in cond_copy["items"]:
                    ipath = c.get("icon_path", "")
                    if ipath and os.path.exists(ipath):
                        try:
                            os.remove(ipath)
                        except OSError:
                            pass
                cond_copy["items"].clear()
                _refresh_cond_list()

        ctk.CTkButton(bottom, text="🗑️ Limpiar todo", command=_clear_all).pack(side=tk.LEFT, padx=2)
        ctk.CTkButton(bottom, text="Cancelar", command=dlg.destroy).pack(side=tk.RIGHT, padx=2)
        ctk.CTkButton(bottom, text="✅ Guardar", command=_save).pack(side=tk.RIGHT, padx=2)

        dlg.wait_window()

    def _add_script(self):
        path = filedialog.askopenfilename(
            title="Seleccionar ejecutable",
            filetypes=[("Ejecutables", "*.exe"), ("Todos", "*.*")])
        if not path:
            return

        if not os.path.isfile(path):
            self._dark_dialog("Error", f"El archivo no existe:\n{path}", "error")
            return

        win = ctk.CTkToplevel(self.root, fg_color=DARK_COLORS["bg"])
        win.title("Agregar script")
        win.minsize(300, 380)
        win.transient(self.root)
        win.grab_set()
        win.lift()

        # Dark titlebar
        win.after(50, lambda: _apply_dark_titlebar(win, retries=3))

        # Restore saved geometry or default + center
        self._restore_win_geometry(win, "script_editor_geometry", "360x460")
        self._bind_geo_save(win, "script_editor_geometry")

        form = ttk.Frame(win, padding=10)
        form.pack(fill=tk.BOTH, expand=True)

        ctk.CTkLabel(form, text=f"Script: {os.path.basename(path)}").pack(pady=(0, 10))

        row1 = ttk.Frame(form)
        row1.pack(fill=tk.X, pady=(0, 6))
        ctk.CTkLabel(row1, text="Repeticiones:").pack(side=tk.LEFT)
        reps_var = tk.IntVar(value=1)
        ttk.Spinbox(row1, from_=1, to=999, textvariable=reps_var, width=8).pack(side=tk.RIGHT)

        row2 = ttk.Frame(form)
        row2.pack(fill=tk.X, pady=(0, 6))
        ctk.CTkLabel(row2, text="Duración (s):").pack(side=tk.LEFT)
        dur_var = tk.IntVar(value=10)
        ttk.Spinbox(row2, from_=1, to=9999, textvariable=dur_var, width=8).pack(side=tk.RIGHT)

        row3 = ttk.Frame(form)
        row3.pack(fill=tk.X, pady=(0, 6))
        ctk.CTkLabel(row3, text="Pausa entre reps (s):").pack(side=tk.LEFT)
        pause_var = tk.IntVar(value=0)
        ttk.Spinbox(row3, from_=0, to=9999, textvariable=pause_var, width=8).pack(side=tk.RIGHT)

        # ── Time preview ──
        time_preview = ctk.CTkLabel(form, text="Tiempo: 10s")
        time_preview.pack(pady=(8, 0))

        def update_preview(*args):
            total = (dur_var.get() + pause_var.get()) * reps_var.get() - pause_var.get()
            total = max(total, 0)
            time_preview.configure(text=f"Tiempo: {format_time(total)}")

        reps_var.trace_add("write", update_preview)
        dur_var.trace_add("write", update_preview)
        pause_var.trace_add("write", update_preview)

        def save():
            item = {
                "path": path,
                "repetitions": reps_var.get(),
                "duration": dur_var.get(),
                "pause": pause_var.get(),
                "enabled": True,
                "first_loop_only": False,
                "group": None,
            }
            self.playlist.append(item)
            self._refresh_list()
            win.destroy()

        ctk.CTkButton(form, text="Guardar", command=save).pack()

    def _add_macro(self):
        """Abre el editor de macros para grabar/editar una secuencia."""
        def on_save(macro_data):
            total_time = 0.0
            for act in macro_data["actions"]:
                total_time += act.get("wait_before", 0) + act.get("press_duration", 0.05)
            item = {
                "type": "macro",
                "path": f"macro:{macro_data['name']}",
                "name": macro_data["name"],
                "macro_data": macro_data,
                "repetitions": 1,
                "duration": round(total_time, 1),
                "pause": 0,
                "enabled": True,
                "first_loop_only": False,
                "group": None,
            }
            self.playlist.append(item)
            self._refresh_list()

        MacroEditorWindow(self.root, on_save=on_save, settings=self.settings)

    def _edit_macro(self, idx):
        """Abre el editor de macros para editar una macro existente."""
        item = self.playlist[idx]
        macro_data = item.get("macro_data", {})
        name = macro_data.get("name", item.get("name", "Macro"))
        actions = macro_data.get("actions", [])

        def on_save(new_macro_data):
            total_time = 0.0
            for act in new_macro_data["actions"]:
                total_time += act.get("wait_before", 0) + act.get("press_duration", 0.05)
            item["name"] = new_macro_data["name"]
            item["macro_data"] = new_macro_data
            item["path"] = f"macro:{new_macro_data['name']}"
            item["duration"] = round(total_time, 1)
            self._refresh_list()

        MacroEditorWindow(self.root, on_save=on_save,
                          initial_actions=actions, initial_name=name,
                          settings=self.settings)

    def _edit_script(self):
        sel = self.tree.selection()
        if not sel:
            self._dark_dialog("Seleccionar", "Seleccioná un script de la lista para editarlo.", "info")
            return
        info = self._item_map.get(sel[0])
        if info is None or info[0] != "script":
            self._dark_dialog("Grupo", "Seleccioná un script, no un header de grupo.", "info")
            return
        idx = info[1]
        item = self.playlist[idx]

        # Si es una macro, abrir el editor de macros
        if item.get("type") == "macro":
            self._edit_macro(idx)
            return

        win = ctk.CTkToplevel(self.root, fg_color=DARK_COLORS["bg"])
        win.title("Editar script")
        win.minsize(300, 380)
        win.transient(self.root)
        win.grab_set()
        win.lift()

        # Dark titlebar
        win.after(50, lambda: _apply_dark_titlebar(win, retries=3))

        # Restore saved geometry or default + center
        self._restore_win_geometry(win, "script_editor_geometry", "360x460")
        self._bind_geo_save(win, "script_editor_geometry")

        form = ttk.Frame(win, padding=10)
        form.pack(fill=tk.BOTH, expand=True)

        ctk.CTkLabel(form, text=f"Script: {os.path.basename(item['path'])}").pack(pady=(0, 10))

        row1 = ttk.Frame(form)
        row1.pack(fill=tk.X, pady=(0, 6))
        ctk.CTkLabel(row1, text="Repeticiones:").pack(side=tk.LEFT)
        reps_var = tk.IntVar(value=item["repetitions"])
        ttk.Spinbox(row1, from_=1, to=999, textvariable=reps_var, width=8).pack(side=tk.RIGHT)

        row2 = ttk.Frame(form)
        row2.pack(fill=tk.X, pady=(0, 6))
        ctk.CTkLabel(row2, text="Duración (s):").pack(side=tk.LEFT)
        dur_var = tk.IntVar(value=item["duration"])
        ttk.Spinbox(row2, from_=1, to=9999, textvariable=dur_var, width=8).pack(side=tk.RIGHT)

        row3 = ttk.Frame(form)
        row3.pack(fill=tk.X, pady=(0, 6))
        ctk.CTkLabel(row3, text="Pausa entre reps (s):").pack(side=tk.LEFT)
        pause_var = tk.IntVar(value=item["pause"])
        ttk.Spinbox(row3, from_=0, to=9999, textvariable=pause_var, width=8).pack(side=tk.RIGHT)

        # ── Time preview ──
        initial_total = (item["duration"] + item["pause"]) * item["repetitions"] - item["pause"]
        initial_total = max(initial_total, 0)
        time_preview = ctk.CTkLabel(form, text=f"Tiempo: {format_time(initial_total)}")
        time_preview.pack(pady=(8, 0))

        def update_preview(*args):
            total = (dur_var.get() + pause_var.get()) * reps_var.get() - pause_var.get()
            total = max(total, 0)
            time_preview.configure(text=f"Tiempo: {format_time(total)}")

        reps_var.trace_add("write", update_preview)
        dur_var.trace_add("write", update_preview)
        pause_var.trace_add("write", update_preview)

        def save():
            # Preservar todos los campos existentes, solo actualizar los editables
            item["repetitions"] = reps_var.get()
            item["duration"] = dur_var.get()
            item["pause"] = pause_var.get()
            # Limpiar campos antiguos de repeat_until si existían
            for key in ("repeat_until_enabled", "repeat_until_mode", "repeat_until_icon",
                       "repeat_until_threshold", "repeat_until_max_iterations",
                       "repeat_until_check_interval"):
                item.pop(key, None)
            self._refresh_list()
            win.destroy()

        ctk.CTkButton(form, text="Guardar cambios", command=save).pack()

    def _remove_script(self):
        sel = self.tree.selection()
        if not sel:
            return
        info = self._item_map.get(sel[0])
        if info is None:
            return

        item_type, ref = info

        if item_type == "group":
            # Remove all items in the group
            indices = self._get_group_indices(ref)
            for i in sorted(indices, reverse=True):
                del self.playlist[i]
        elif item_type == "script":
            del self.playlist[ref]
        self._refresh_list()

    def _clone_script(self):
        """Duplicar el script seleccionado."""
        sel = self.tree.selection()
        if not sel:
            self._dark_dialog("Seleccionar", "Seleccioná un script de la lista para clonarlo.", "info")
            return
        info = self._item_map.get(sel[0])
        if info is None or info[0] != "script":
            self._dark_dialog("Grupo", "Seleccioná un script, no un header de grupo.", "info")
            return
        idx = info[1]
        original = self.playlist[idx]
        clone = dict(original)
        self.playlist.insert(idx + 1, clone)
        self._refresh_list()

    def _move_up(self):
        sel = self.tree.selection()
        if not sel:
            return
        info = self._item_map.get(sel[0])
        if info is None:
            return

        item_type, ref = info

        if item_type == "group":
            self._move_block_up(ref)
        elif item_type == "script":
            idx = ref
            if idx > 0:
                cur_group = self.playlist[idx].get("group", None)
                prev_group = self.playlist[idx - 1].get("group", None)
                if cur_group == prev_group:
                    # Same group — simple swap
                    self.playlist[idx], self.playlist[idx - 1] = (
                        self.playlist[idx - 1],
                        self.playlist[idx])
                    self._refresh_list()
                    self._reselect_script(idx - 1)
                else:
                    # Different group — try block-level swap
                    self._move_script_past_block(idx, direction="up")

    def _move_down(self):
        sel = self.tree.selection()
        if not sel:
            return
        info = self._item_map.get(sel[0])
        if info is None:
            return

        item_type, ref = info

        if item_type == "group":
            self._move_block_down(ref)
        elif item_type == "script":
            idx = ref
            if idx < len(self.playlist) - 1:
                cur_group = self.playlist[idx].get("group", None)
                nxt_group = self.playlist[idx + 1].get("group", None)
                if cur_group == nxt_group:
                    # Same group — simple swap
                    self.playlist[idx], self.playlist[idx + 1] = (
                        self.playlist[idx + 1],
                        self.playlist[idx])
                    self._refresh_list()
                    self._reselect_script(idx + 1)
                else:
                    # Different group — try block-level swap
                    self._move_script_past_block(idx, direction="down")

    # ═══════════════════════════════════════════════════════════════
    # GROUP / BLOCK MOVEMENT
    # ═══════════════════════════════════════════════════════════════

    def _move_script_past_block(self, idx, direction):
        """Swap a script with the entire adjacent block of items that share
        the same group (different from the script's own group)."""
        if direction == "up":
            if idx <= 0:
                return
            # Find the block above: items sharing the same group as the item directly above
            adj_group = self.playlist[idx - 1].get("group")
            block_start = idx - 1
            while block_start > 0 and self.playlist[block_start - 1].get("group") == adj_group:
                block_start -= 1
            above_block = self.playlist[block_start:idx]
            script = self.playlist[idx:idx + 1]
            # Swap: script moves before the block
            self.playlist = (self.playlist[:block_start] + script + above_block +
                             self.playlist[idx + 1:])
            new_idx = block_start
        else:  # down
            if idx >= len(self.playlist) - 1:
                return
            # Find the block below: items sharing the same group as the item directly below
            adj_group = self.playlist[idx + 1].get("group")
            block_end = idx + 2
            while (block_end < len(self.playlist) and
                   self.playlist[block_end].get("group") == adj_group):
                block_end += 1
            below_block = self.playlist[idx + 1:block_end]
            script = self.playlist[idx:idx + 1]
            # Swap: script moves after the block
            self.playlist = (self.playlist[:idx] + below_block + script +
                             self.playlist[block_end:])
            new_idx = idx + len(below_block)

        self._refresh_list()
        self._reselect_script(new_idx)

    def _get_group_indices(self, group_name):
        """Return playlist indices for items in a group, including nested children."""
        return [i for i, item in enumerate(self.playlist)
                if item.get("group") and
                (item["group"] == group_name or item["group"].startswith(group_name + "/"))]

    def _to_blocks(self):
        """Convert playlist to top-level blocks (grouped by first segment of group path)."""
        blocks = []
        i = 0
        while i < len(self.playlist):
            item = self.playlist[i]
            group = item.get("group", None)
            if group:
                top_group = group.split("/")[0]
                block = []
                while i < len(self.playlist) and self.playlist[i].get("group", "").startswith(top_group):
                    block.append(self.playlist[i])
                    i += 1
                blocks.append(block)
            else:
                blocks.append([item])
                i += 1
        return blocks

    def _blocks_to_playlist(self, blocks):
        """Flatten blocks back to playlist."""
        result = []
        for block in blocks:
            result.extend(block)
        return result

    def _find_block_index(self, blocks, group_name):
        """Find the index of the top-level block for a group (first segment)."""
        top = group_name.split("/")[0]
        for bi, block in enumerate(blocks):
            first_group = block[0].get("group", "")
            if first_group and first_group.startswith(top):
                return bi
        return -1

    def _reselect_group(self, group_path):
        """After _refresh_list, find and re-select the group header."""
        def _scan(parent):
            for iid in self.tree.get_children(parent):
                info = self._item_map.get(iid)
                if info and info[0] == "group" and info[1] == group_path:
                    self.tree.selection_set(iid)
                    self.tree.see(iid)
                    return True
                if _scan(iid):
                    return True
            return False
        _scan("")

    def _reselect_script(self, playlist_idx):
        """After _refresh_list, find and re-select a script by playlist index."""
        for iid, (typ, data) in self._item_map.items():
            if typ == "script" and data == playlist_idx:
                self.tree.selection_set(iid)
                self.tree.see(iid)
                return

    def _get_subgroup_range(self, group_path):
        """Return (start, end+1) indices in playlist for items in this group/subgroup.
        Returns None if group not found or items are scattered."""
        indices = [i for i, item in enumerate(self.playlist)
                   if item.get("group") == group_path or
                   (item.get("group") and item["group"].startswith(group_path + "/"))]
        if not indices:
            return None
        # Verify contiguity
        if indices != list(range(indices[0], indices[-1] + 1)):
            return None
        return (indices[0], indices[-1] + 1)

    def _move_block_up(self, group_path):
        """Move a group block up. Handles both top-level groups and sub-groups."""
        # Try sub-group range first
        rng = self._get_subgroup_range(group_path)
        if rng and rng[0] > 0:
            start, end = rng
            # Find the full block above (could be multi-item if it's another sub-group)
            prev_item = self.playlist[start - 1]
            prev_group = prev_item.get("group")
            above_start = start - 1
            while above_start > 0 and self.playlist[above_start - 1].get("group") == prev_group:
                above_start -= 1
            above_block = self.playlist[above_start:start]
            block = self.playlist[start:end]
            self.playlist = (self.playlist[:above_start] + block + above_block +
                             self.playlist[end:])
            self._refresh_list()
            self._reselect_group(group_path)
            return

        # Fall back to top-level block movement
        blocks = self._to_blocks()
        bi = self._find_block_index(blocks, group_path)
        if bi <= 0:
            return
        blocks[bi], blocks[bi - 1] = blocks[bi - 1], blocks[bi]
        self.playlist = self._blocks_to_playlist(blocks)
        self._refresh_list()
        self._reselect_group(group_path)

    def _move_block_down(self, group_path):
        """Move a group block down. Handles both top-level groups and sub-groups."""
        # Try sub-group range first
        rng = self._get_subgroup_range(group_path)
        if rng and rng[1] < len(self.playlist):
            start, end = rng
            # Find the full block below (could be multi-item if it's another sub-group)
            nxt_item = self.playlist[end]
            nxt_group = nxt_item.get("group")
            below_end = end + 1
            while (below_end < len(self.playlist) and
                   self.playlist[below_end].get("group") == nxt_group):
                below_end += 1
            below_block = self.playlist[end:below_end]
            block = self.playlist[start:end]
            self.playlist = (self.playlist[:start] + below_block + block +
                             self.playlist[below_end:])
            self._refresh_list()
            self._reselect_group(group_path)
            return

        # Fall back to top-level block movement
        blocks = self._to_blocks()
        bi = self._find_block_index(blocks, group_path)
        if bi < 0 or bi >= len(blocks) - 1:
            return
        blocks[bi], blocks[bi + 1] = blocks[bi + 1], blocks[bi]
        self.playlist = self._blocks_to_playlist(blocks)
        self._refresh_list()
        self._reselect_group(group_path)

    # ═══════════════════════════════════════════════════════════════
    # GROUP OPERATIONS
    # ═══════════════════════════════════════════════════════════════

    def _group_selected(self):
        """Assign selected script(s) to a group. If a group header is also selected,
        the new group nests inside it. Supports multi-select for batch grouping."""
        sel = self.tree.selection()
        if not sel:
            self._dark_dialog("Seleccionar", "Seleccioná scripts para agrupar.\n"
                              "Ctrl+Click en un 📁 grupo + scripts para crear subgrupo.", "info")
            return

        # Separate group headers and scripts from selection
        parent_path = None
        indices = []
        for iid in sel:
            info = self._item_map.get(iid)
            if info is None:
                continue
            if info[0] == "group":
                parent_path = info[1]  # Use last group in selection as parent
            elif info[0] == "script":
                indices.append(info[1])

        if not indices:
            self._dark_dialog("Grupo",
                "Seleccioná al menos un script junto con el grupo padre.\n\n"
                "1. Ctrl+Click en el 📁 grupo destino\n"
                "2. Ctrl+Click en los scripts a agrupar\n"
                "3. Clic en 📁 Agrupar", "info")
            return

        # Build descriptive label for the dialog
        if parent_path:
            label = f"Crear subgrupo dentro de «{parent_path}»\nNombre del subgrupo:"
        else:
            label = "Nombre del grupo:"

        self._ask_group_name(lambda name: self._do_group(indices, name, parent_path),
                           label=label)

    def _do_group(self, indices, group_name, parent_path=None):
        """Assign the given indices to `group_name`, optionally nested under parent_path."""
        if not group_name.strip():
            return
        group_name = group_name.strip()

        # Build full group path
        full_path = f"{parent_path}/{group_name}" if parent_path else group_name

        for idx in indices:
            self.playlist[idx]["group"] = full_path

        # Get ALL items with this group (including existing ones)
        all_group = set(self._get_group_indices(full_path))
        first_selected = min(indices)

        # Rebuild playlist: group items contiguous at first selected position
        before = [item for i, item in enumerate(self.playlist)
                  if i not in all_group and i < first_selected]
        group_items = [self.playlist[i] for i in sorted(all_group)]
        after = [item for i, item in enumerate(self.playlist)
                 if i not in all_group and i >= first_selected]

        self.playlist = before + group_items + after
        self._refresh_list()

    def _ungroup_selected(self):
        """Remove group assignment from selected scripts or entire group."""
        sel = self.tree.selection()
        if not sel:
            return

        indices = set()
        for iid in sel:
            info = self._item_map.get(iid)
            if info is None:
                continue
            if info[0] == "group":
                for i in self._get_group_indices(info[1]):
                    indices.add(i)
            elif info[0] == "script":
                indices.add(info[1])

        for idx in indices:
            self.playlist[idx]["group"] = None

        self._refresh_list()

    def _rename_group(self):
        """Rename the selected group."""
        sel = self.tree.selection()
        if not sel:
            return
        info = self._item_map.get(sel[0])
        if info is None or info[0] != "group":
            self._dark_dialog("Grupo", "Seleccioná un header de grupo para renombrar.", "info")
            return
        self._rename_group_dialog(info[1])

    def _rename_group_dialog(self, old_path):
        """Show dialog to rename a group."""
        # Pre-fill with last segment (the group's own name)
        last_part = old_path.rsplit("/", 1)[-1]
        self._ask_group_name(lambda new_name: self._do_rename_group(old_path, new_name))

    def _do_rename_group(self, old_path, new_name):
        """Rename a group: change the last segment of the path for all matching items."""
        if not new_name.strip():
            return
        new_name = new_name.strip()

        # Replace old_path with new path (keeping parent intact)
        parent = old_path.rsplit("/", 1)[0] if "/" in old_path else ""
        new_path = f"{parent}/{new_name}" if parent else new_name

        if new_path == old_path:
            return

        for idx in self._get_group_indices(old_path):
            old_group = self.playlist[idx]["group"]
            # Replace only the matching prefix
            if old_group == old_path:
                self.playlist[idx]["group"] = new_path
            elif old_group.startswith(old_path + "/"):
                self.playlist[idx]["group"] = new_path + old_group[len(old_path):]

        self._refresh_list()

    def _ask_group_name(self, callback, label="Nombre:"):
        """Show a small dialog to ask for a group name."""
        win = ctk.CTkToplevel(self.root, fg_color=DARK_COLORS["bg"])
        win.title("Nombre del grupo")
        win.geometry("300x130")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.lift()

        win.after(50, lambda: _apply_dark_titlebar(win, retries=3))
        win.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        dw, dh = win.winfo_width(), win.winfo_height()
        win.geometry(f"300x130+{px + (pw - dw)//2}+{py + (ph - dh)//2}")

        form = ttk.Frame(win, padding=10)
        form.pack(fill=tk.BOTH, expand=True)

        # Support multi-line labels
        for line in label.split("\n"):
            ctk.CTkLabel(form, text=line).pack(anchor=tk.W)
        ctk.CTkLabel(form, text="").pack()  # spacer
        name_var = tk.StringVar()
        entry = ctk.CTkEntry(form, textvariable=name_var, width=30)
        entry.pack(fill=tk.X, pady=(0, 8))
        entry.focus_set()

        def save():
            callback(name_var.get())
            win.destroy()

        entry.bind("<Return>", lambda e: save())
        ctk.CTkButton(form, text="Guardar", command=save).pack()

    # ═══════════════════════════════════════════════════════════════
    # EXECUTION
    # ═══════════════════════════════════════════════════════════════

    def _restore_win_geometry(self, win, settings_key, default_size, parent=None):
        """Restore saved geometry or center with default size on parent."""
        saved = self.settings.get(settings_key, "")
        if saved:
            try:
                win.geometry(saved)
                return
            except tk.TclError:
                pass
        # Default: set size and center on parent or screen
        win.geometry(default_size)
        win.update_idletasks()
        if parent is None:
            parent = self.root
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_x(), parent.winfo_y()
        dw, dh = win.winfo_width(), win.winfo_height()
        # Fallback to screen center if parent has no real dimensions yet
        if pw < 100 or ph < 100:
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            win.geometry(f"{default_size}+{(sw - dw)//2}+{(sh - dh)//2}")
        else:
            win.geometry(f"{default_size}+{px + (pw - dw)//2}+{py + (ph - dh)//2}")

    def _bind_geo_save(self, win, settings_key):
        """Save window geometry on close (WM_DELETE_WINDOW + Destroy)."""
        def _save_and_destroy():
            try:
                self.settings[settings_key] = win.geometry()
            except tk.TclError:
                pass
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _save_and_destroy)
        # Also save on any destroy (e.g., Cancel button via destroy())
        def _on_destroy(_event=None):
            try:
                self.settings[settings_key] = win.geometry()
            except tk.TclError:
                pass
        win.bind("<Destroy>", _on_destroy, add="+")

    def _gather_settings(self):
        settings = {
            "loop_mode": self.loop_mode_var.get(),
            "loop_count": self._parse_int(self.loop_count_var, 1),
            "loop_delay": self._parse_int(self.loop_delay_var, 0),
            "hotkey": self.hotkey_var.get().lower(),
            "window_geometry": self.root.geometry(),
            "mini_bar_enabled": self._mini_bar_enabled,
            # ── Carry over persisted state ──
            "collapsed_groups": self.settings.get("collapsed_groups", []),
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
        info = self._item_map.get(sel[0])
        if info is None or info[0] != "script":
            self._dark_dialog("Grupo", "Seleccioná un script, no un header de grupo.", "info")
            return
        idx = info[1]
        item = self.playlist[idx]

        # ── Verificar condiciones ANTES de ejecutar ──
        # Para "Ejecutar seleccionado", el comportamiento es "require":
        # solo ejecutar si las condiciones se cumplen.
        from icon_detector import check_icon as _ci
        conds = item.get("conditions", {})
        items = conds.get("items", [])
        mode = conds.get("mode", "and")

        # También verificar formato antiguo repeat_until_*
        if not items and item.get("repeat_until_enabled") and item.get("repeat_until_icon"):
            old_mode = item.get("repeat_until_mode", "match")
            old_icon = item["repeat_until_icon"]
            old_thresh = item.get("repeat_until_threshold", 0.08)
            items = [{
                "type": "require",
                "icon_path": old_icon,
                "threshold": old_thresh,
            }]
            mode = "and"

        if items:
            results = []
            for cond in items:
                ipath = cond.get("icon_path", "")
                ctype = cond.get("type", "require")
                threshold = cond.get("threshold", 0.08)
                if not ipath:
                    results.append(True if ctype == "block" else False)
                    continue
                found, _ = _ci(ipath, None, threshold)
                if ctype == "require":
                    results.append(found)
                else:  # block
                    results.append(not found)

            passed = all(results) if mode == "and" else any(results)
            if not passed:
                name = os.path.basename(item["path"])
                self._set_status(
                    f"⏭️  Condición no cumplida: {name}", DARK_COLORS["yellow"])
                return
            else:
                name = os.path.basename(item["path"])
                self._set_status(
                    f"✅ Condición OK: {name}", DARK_COLORS["green"])

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

        # Save active playlist for duration lookups during execution
        self._exec_playlist = playlist

        # Ensure any previous thread has fully terminated
        if self.executor_thread is not None and self.executor_thread.is_alive():
            self.executor_thread.join(timeout=5)

        # Fresh state for every new run
        self.is_running = True
        self.stop_event = threading.Event()
        self.launch_event = threading.Event()
        self._ru_done = False  # reset repeat_until completion flag

        # Compute real total time based on the actual playlist being run
        self._exec_total_time = self._calc_total_time(playlist, settings)

        # Per-item timing tracking (for infinite mode script countdown)
        self._cur_item_start_time = 0
        self._cur_item_total_time = 0

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
            "on_start_item": lambda idx, name, reps, mode_label="": self.root.after(
                0, lambda: self._cb_start_item(idx, name, reps, mode_label)
            ),
            "on_repeat": lambda global_rep, total_global, total_per_loop, name, current, total_item, loop, max_loops: self.root.after(
                0,
                lambda: self._cb_repeat(
                    global_rep, total_global, total_per_loop, name, current, total_item, loop, max_loops
                )),
            "on_repeat_until_check": lambda idx, name, iteration, ru_max, found, condition_met: self.root.after(
                0, lambda: self._cb_repeat_until_check(idx, name, iteration, ru_max, found, condition_met)
            ),
            "on_repeat_until_done": lambda idx, name, iteration: self.root.after(
                0, lambda: self._cb_repeat_until_done(idx, name, iteration)
            ),
            "on_repeat_until_max": lambda idx, name, ru_max: self.root.after(
                0, lambda: self._cb_repeat_until_max(idx, name, ru_max)
            ),
            "on_loop_delay": lambda current, delay, total_global: self.root.after(
                0, lambda: self._cb_loop_delay(current, delay, total_global)
            ),
            "on_finish": lambda msg, done, total_global, total_per_loop, loops, max_loops: self.root.after(
                0, lambda: self._cb_finish(msg, done, total_global, total_per_loop, loops, max_loops)
            ),
            "on_error": lambda msg: self.root.after(0, lambda: self._cb_error(msg)),
            "on_launch": lambda path: self.root.after(0, lambda: self._do_launch(path)),
            "on_skip_icon": lambda idx, name: self.root.after(0, lambda: self._cb_skip_icon(idx, name)),
            "on_retry_wait": lambda idx, name, attempt, total: self.root.after(
                0, lambda: self._cb_retry_wait(idx, name, attempt, total)),
            "on_fallback_trigger": lambda idx, name, fb_name: self.root.after(
                0, lambda: self._cb_fallback_trigger(idx, name, fb_name)),
            "on_fallback_wait": lambda idx, name, delay: self.root.after(
                0, lambda: self._cb_fallback_wait(idx, name, delay)),
            "on_fallback_error": lambda fb_name, error: self.root.after(
                0, lambda: self._cb_fallback_error(fb_name, error)),
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
        """Launch the .exe using os.startfile, or handle macro playback.
        Macros start with 'Macro:' prefix and are already played by the executor
        — we just acknowledge by setting launch_event."""
        try:
            if path.startswith("Macro:"):
                # Macro — ya se ejecutó en el executor, solo confirmar
                pass
            elif os.name == "nt":
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
        """Update progress bar and percentage label. CTk uses 0.0-1.0."""
        self._progress_max = maximum
        self.progress.set(value / maximum if maximum > 0 else 0)
        pct = (value / maximum * 100) if maximum > 0 else 0
        self.progress_pct_label.configure(text=f"{int(pct)}%")

    def _poll_timer(self):
        """Update progress bar and countdown based on real elapsed time."""
        if not self.is_running:
            return
        elapsed = time.time() - self._exec_start_time
        if self._exec_total_time is not None:
            remaining = max(self._exec_total_time - elapsed, 0)
            self.countdown_label.configure(text=f"⏱️ {format_time(int(remaining))}")
            prog = min(int(elapsed), self._exec_total_time)
            self._update_progress(prog, self._exec_total_time)

            # ── Update mini bar ──
            if self.mini_bar is not None and self.mini_bar.is_visible():
                self.mini_bar.update(
                    self.status_label.cget("text").replace(" EJECUTANDO | ", ""),
                    prog,
                    self._exec_total_time,
                    f"-{mini_format_time(int(remaining))}",
                    True)
        else:
            # Infinite mode: show script countdown + total session elapsed
            elapsed = time.time() - self._exec_start_time
            item_elapsed = time.time() - self._cur_item_start_time
            item_remaining = max(self._cur_item_total_time - item_elapsed, 0)
            self.countdown_label.configure(
                text=f"⏱️ -{format_time(int(item_remaining))} │ {format_time(int(elapsed))}"
            )
            if self.mini_bar is not None and self.mini_bar.is_visible():
                prog_max = self._cur_item_total_time if self._cur_item_total_time > 0 else 1
                prog_val = int(item_elapsed) % max(prog_max, 1)
                time_text = f"-{mini_format_time(int(item_remaining))}│{mini_format_time(int(elapsed))}"
                self.mini_bar.update(
                    self.status_label.cget("text").replace(" EJECUTANDO | ", ""),
                    prog_val,
                    prog_max,
                    time_text,
                    True)
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
            total_per_loop = getattr(self, '_progress_max', self._exec_total_time or 1)
            self._update_progress(0, total_per_loop)
        if max_loops is None:
            status_text = f"EJECUTANDO | Loop {current} (∞)"
        else:
            status_text = f"EJECUTANDO | Loop {current}/{max_loops}"
        self._set_status(status_text, DARK_COLORS["blue"])

    def _cb_start_item(self, idx, name, reps, mode_label=""):
        # Track per-script timing for infinite mode countdown
        if hasattr(self, '_exec_playlist') and idx < len(self._exec_playlist):
            item = self._exec_playlist[idx]
            self._cur_item_start_time = time.time()
            self._cur_item_total_time = self._calc_item_time(item)
        if mode_label:
            self._set_status(f"EJECUTANDO | {name}: {mode_label}", DARK_COLORS["blue"])

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
                item_elapsed = time.time() - self._cur_item_start_time
                item_remaining = max(self._cur_item_total_time - item_elapsed, 0)
                time_text = f"-{mini_format_time(int(item_remaining))}│{mini_format_time(int(elapsed))}"
            self.mini_bar.update(short_status, progress_val, progress_max, time_text, True)

    def _cb_loop_delay(self, current, delay, total_global):
        self._set_status(f"ESPERANDO | Loop {current} → pausa {delay}s", DARK_COLORS["purple"])

    def _cb_finish(self, msg, done, total_global, total_per_loop, loops, max_loops):
        self.is_running = False
        loop_str = f"{loops} loops" if max_loops is None else f"{loops}/{max_loops} loops"
        total_str = f"{done}/{total_global}" if total_global is not None else f"{done} (∞)"
        # Si fue detenido por repeat_until exitoso, mostrar COMPLETADO
        if msg == "Detenido" and getattr(self, '_ru_done', False):
            msg = "Completado"
        if msg == "Detenido":
            self._set_status(f"DETENIDO | {loop_str} | {total_str} reps", DARK_COLORS["red"])
        elif msg == "Completado":
            self._set_status(f"COMPLETADO | {loop_str} | {total_str} reps", DARK_COLORS["green"])
            # Update mini bar + schedule auto-hide BEFORE the blocking dialog
            if self.mini_bar is not None:
                elapsed = time.time() - self._exec_start_time
                self.mini_bar.update(f"{msg}", 0, 1, mini_format_time(int(elapsed)), False)
                self.root.after(3000, self._hide_mini_bar)
            self._dark_dialog("Finalizado", f"Ejecución completada.\n{loop_str}\n{total_str} reps realizadas.", "success")
        else:
            self._set_status(f"{msg} | {loop_str} | {total_str} reps", "#7f8c8d")
        self._update_progress(self._exec_total_time or done, self._exec_total_time or total_per_loop or 1)

        # ── Mini Bar: mostrar estado final y ocultar ──
        # (Completado already handles its own auto-hide above)
        if self.mini_bar is not None and msg != "Completado":
            elapsed = time.time() - self._exec_start_time
            self.mini_bar.update(f"{msg}", 0, 1, mini_format_time(int(elapsed)), False)
            self.root.after(3000, self._hide_mini_bar)

    def _set_status(self, text, color):
        """Update the status label with text and background color."""
        self.status_label.configure(text=f" {text} ", fg_color=color, text_color="#ffffff")

    def _cb_skip_icon(self, idx, name):
        """Called when a script is skipped because its required icon is not visible."""
        self._set_status(f"⏭️  Icono no visible: {name}", DARK_COLORS["yellow"])

    def _cb_retry_wait(self, idx, name, attempt, total):
        """Called when waiting between retry attempts."""
        self._set_status(
            f"⏳ Reintento {attempt}/{total} para: {name}",
            DARK_COLORS["yellow"])

    def _cb_fallback_trigger(self, idx, name, fb_name):
        """Called when fallback script is triggered after consecutive failures."""
        self._set_status(
            f"🆘 Recuperación ejecutada: {fb_name} (falló {name})",
            DARK_COLORS["red"])

    def _cb_fallback_wait(self, idx, name, delay):
        """Called when waiting after fallback script before continuing."""
        self._set_status(
            f"⏳ Esperando {delay}s tras recuperación de: {name}",
            DARK_COLORS["blue"])

    def _cb_fallback_error(self, fb_name, error):
        """Called when fallback script fails to launch (no detiene el loop)."""
        self._set_status(
            f"⚠️ Error al lanzar recuperación {fb_name}: {error}",
            DARK_COLORS["yellow"])

    def _cb_repeat_until_check(self, idx, name, iteration, ru_max, found, condition_met):
        """Called after each repeat-until iteration when checking the icon."""
        status = "encontrado" if found else "no encontrado"
        met = "✓ CONDICIÓN CUMPLIDA" if condition_met else f"buscando... ({iteration}/{ru_max})"
        self._set_status(
            f"🔄 {name}: icono {status} | {met}",
            DARK_COLORS["green"] if condition_met else DARK_COLORS["blue"])

    def _cb_repeat_until_done(self, idx, name, iteration):
        """Called when the repeat-until condition is met."""
        self._ru_done = True
        self._set_status(
            f"✅ {name}: condición cumplida en {iteration} intento(s)",
            DARK_COLORS["green"])

    def _cb_repeat_until_max(self, idx, name, ru_max):
        """Called when repeat-until reaches max iterations without condition met."""
        self._set_status(
            f"⚠️ {name}: máximo de {ru_max} intentos alcanzado sin cumplir condición",
            DARK_COLORS["yellow"])

    def _cb_error(self, msg):
        self._dark_dialog("Error", msg, "error")
        self.is_running = False
        self._set_status(f"Error: {msg}", DARK_COLORS["red"])
        if self.mini_bar is not None:
            self.mini_bar.reset()

    def _on_close(self):
        settings = self._gather_settings()
        save_config({"playlist": self.playlist, "settings": settings}, self._current_profile)
        self.hotkey.stop()
        if self.mini_bar is not None:
            self.mini_bar.close()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = OrchestratorApp(root)
    root.mainloop()
