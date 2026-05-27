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


def format_time(seconds):
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


class OrchestratorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TinyTask Orchestrator")
        self.root.geometry("900x650")
        self.root.minsize(750, 500)

        # Estado
        config = load_config()
        self.playlist = config["playlist"]
        self.settings = config["settings"]
        self.executor_thread = None
        self.stop_event = threading.Event()
        self.launch_event = threading.Event()
        self.is_running = False

        # Hotkey global configurable (toggles: start all / stop)
        self.saved_hotkey = self.settings.get("hotkey", "f10")
        self.hotkey = HotkeyListener()
        self.hotkey.start(self.saved_hotkey, self._hotkey_toggle)

        # Construir UI
        self._build_ui()
        self._refresh_list()
        self._update_time_labels()

        # Guardar al cerrar
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        # ===== Frame Configuración del Loop =====
        loop_frame = ttk.LabelFrame(self.root, text=" Configuración del Loop ", padding=10)
        loop_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        ttk.Label(loop_frame, text="Modo:").pack(side=tk.LEFT, padx=(0, 5))
        self.loop_mode_var = tk.StringVar(value=self.settings.get("loop_mode", "once"))
        mode_combo = ttk.Combobox(
            loop_frame,
            textvariable=self.loop_mode_var,
            values=["once", "fixed", "infinite"],
            width=12,
            state="readonly",
        )
        mode_combo.pack(side=tk.LEFT, padx=5)
        mode_combo.bind("<<ComboboxSelected>>", self._on_loop_mode_change)

        ttk.Label(loop_frame, text="Cantidad:").pack(side=tk.LEFT, padx=(15, 5))
        self.loop_count_var = tk.StringVar(value=str(self.settings.get("loop_count", 1)))
        self.loop_count_entry = ttk.Entry(loop_frame, textvariable=self.loop_count_var, width=8, validate="key")
        self.loop_count_entry.config(validatecommand=(self.root.register(self._validate_int_positive), "%P"))
        self.loop_count_entry.pack(side=tk.LEFT, padx=5)

        ttk.Label(loop_frame, text="Espera entre loops (s):").pack(side=tk.LEFT, padx=(15, 5))
        self.loop_delay_var = tk.StringVar(value=str(self.settings.get("loop_delay", 0)))
        self.loop_delay_entry = ttk.Entry(loop_frame, textvariable=self.loop_delay_var, width=8, validate="key")
        self.loop_delay_entry.config(validatecommand=(self.root.register(self._validate_int_non_negative), "%P"))
        self.loop_delay_entry.pack(side=tk.LEFT, padx=5)

        # Tiempo estimado total
        self.total_time_label = ttk.Label(loop_frame, text="Tiempo estimado total: 0s")
        self.total_time_label.pack(side=tk.RIGHT, padx=10)

        self._on_loop_mode_change(None)

        # ===== Frame lista =====
        list_frame = ttk.Frame(self.root, padding=10)
        list_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("orden", "hab", "nombre", "reps", "duracion", "pausa", "tiempo")
        self.tree = ttk.Treeview(
            list_frame, columns=columns, show="headings", selectmode="browse"
        )
        self.tree.heading("orden", text="#")
        self.tree.heading("hab", text="✓")
        self.tree.heading("nombre", text="Script")
        self.tree.heading("reps", text="Repeticiones")
        self.tree.heading("duracion", text="Duración (s)")
        self.tree.heading("pausa", text="Pausa (s)")
        self.tree.heading("tiempo", text="Tiempo total")

        self.tree.column("orden", width=35, anchor="center")
        self.tree.column("hab", width=30, anchor="center")
        self.tree.column("nombre", width=250, anchor="w")
        self.tree.column("reps", width=90, anchor="center")
        self.tree.column("duracion", width=90, anchor="center")
        self.tree.column("pausa", width=90, anchor="center")
        self.tree.column("tiempo", width=100, anchor="center")

        # Click on checkbox column toggles enabled/disabled
        self.tree.bind("<ButtonRelease-1>", self._on_tree_click)
        # Double-click on editable columns for inline editing
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self._inline_entry = None  # Track the inline editing Entry widget

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # ===== Frame botones =====
        btn_frame = ttk.Frame(self.root, padding=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="➕ Agregar", command=self._add_script).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="✏️ Editar", command=self._edit_script).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="🗑️ Quitar", command=self._remove_script).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="⬆️ Subir", command=self._move_up).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="⬇️ Bajar", command=self._move_down).pack(
            side=tk.LEFT, padx=5
        )

        # ===== Frame ejecución =====
        exec_frame = ttk.LabelFrame(self.root, text=" Ejecución ", padding=10)
        exec_frame.pack(fill=tk.X, padx=10, pady=5)

        # Status visual con colores
        self.status_label = tk.Label(
            exec_frame,
            text=" LISTO ",
            font=("Segoe UI", 10, "bold"),
            fg="white",
            bg="#2ecc71",
            padx=10,
            pady=4,
        )
        self.status_label.pack(anchor=tk.W, pady=(0, 5))

        # Progress bar + percentage label
        progress_frame = ttk.Frame(exec_frame)
        progress_frame.pack(fill=tk.X, pady=5)

        self.progress = ttk.Progressbar(
            progress_frame, orient=tk.HORIZONTAL, mode="determinate"
        )
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.progress_pct_label = ttk.Label(progress_frame, text="0%", width=6)
        self.progress_pct_label.pack(side=tk.LEFT, padx=(5, 0))

        ttk.Button(exec_frame, text="▶️ Iniciar todo", command=self._start).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(exec_frame, text="▶️ Ejecutar seleccionado", command=self._run_selected).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(exec_frame, text="⏹️ Detener", command=self._stop).pack(
            side=tk.LEFT, padx=5
        )

        # Hotkey configurable
        ttk.Label(exec_frame, text="Tecla rápida:").pack(side=tk.LEFT, padx=(20, 5))
        self.hotkey_var = tk.StringVar(value=self.saved_hotkey.upper())
        hotkey_combo = ttk.Combobox(
            exec_frame,
            textvariable=self.hotkey_var,
            values=["F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"],
            width=5,
            state="readonly",
        )
        hotkey_combo.pack(side=tk.LEFT, padx=5)
        hotkey_combo.bind("<<ComboboxSelected>>", self._on_hotkey_change)

        # Countdown timer
        self.countdown_label = ttk.Label(exec_frame, text="⏱️ --:--", font=("Segoe UI", 10, "bold"))
        self.countdown_label.pack(side=tk.RIGHT, padx=10)

    def _on_hotkey_change(self, event):
        new_key = self.hotkey_var.get().lower()
        self.hotkey.restart(new_key, self._hotkey_toggle)
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
            self.total_time_label.config(text="Tiempo estimado total: ∞ (infinito)")
        else:
            self.total_time_label.config(
                text=f"Tiempo estimado total: {format_time(total)}"
            )

    def _on_loop_mode_change(self, event):
        mode = self.loop_mode_var.get()
        if mode == "infinite":
            self.loop_count_entry.config(state="disabled")
        else:
            self.loop_count_entry.config(state="normal")
        self._update_time_labels()

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

    def _add_script(self):
        path = filedialog.askopenfilename(
            title="Seleccionar script TinyTask",
            filetypes=[("Ejecutables", "*.exe"), ("Todos", "*.*")],
        )
        if not path:
            return

        # Validate the .exe exists
        if not os.path.isfile(path):
            messagebox.showerror("Error", f"El archivo no existe:\n{path}")
            return

        win = tk.Toplevel(self.root)
        win.title("Configurar script")
        win.geometry("300x300")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.lift()

        ttk.Label(win, text="Repeticiones:").pack(pady=(10, 0))
        reps_var = tk.IntVar(value=1)
        ttk.Spinbox(win, from_=1, to=999, textvariable=reps_var, width=10).pack()

        ttk.Label(win, text="Duración estimada (s):").pack(pady=(10, 0))
        dur_var = tk.IntVar(value=10)
        ttk.Spinbox(win, from_=1, to=9999, textvariable=dur_var, width=10).pack()

        ttk.Label(win, text="Pausa entre reps (s):").pack(pady=(10, 0))
        pause_var = tk.IntVar(value=0)
        ttk.Spinbox(win, from_=0, to=9999, textvariable=pause_var, width=10).pack()

        ttk.Label(win, text="Tiempo estimado:").pack(pady=(10, 0))
        time_preview = ttk.Label(win, text="10s")
        time_preview.pack()

        def update_preview(*args):
            total = (dur_var.get() + pause_var.get()) * reps_var.get() - pause_var.get()
            total = max(total, 0)
            time_preview.config(text=format_time(total))

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

        ttk.Button(win, text="Guardar", command=save).pack(pady=15)

    def _edit_script(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Seleccionar", "Seleccioná un script de la lista para editarlo.")
            return
        idx = self.tree.index(sel[0])
        item = self.playlist[idx]

        win = tk.Toplevel(self.root)
        win.title("Editar script")
        win.geometry("300x300")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.lift()

        ttk.Label(win, text=f"Script: {os.path.basename(item['path'])}").pack(pady=(10, 0))

        ttk.Label(win, text="Repeticiones:").pack(pady=(10, 0))
        reps_var = tk.IntVar(value=item["repetitions"])
        ttk.Spinbox(win, from_=1, to=999, textvariable=reps_var, width=10).pack()

        ttk.Label(win, text="Duración estimada (s):").pack(pady=(10, 0))
        dur_var = tk.IntVar(value=item["duration"])
        ttk.Spinbox(win, from_=1, to=9999, textvariable=dur_var, width=10).pack()

        ttk.Label(win, text="Pausa entre reps (s):").pack(pady=(10, 0))
        pause_var = tk.IntVar(value=item["pause"])
        ttk.Spinbox(win, from_=0, to=9999, textvariable=pause_var, width=10).pack()

        ttk.Label(win, text="Tiempo estimado:").pack(pady=(10, 0))
        time_preview = ttk.Label(win, text=format_time(self._calc_item_time(item)))
        time_preview.pack()

        def update_preview(*args):
            total = (dur_var.get() + pause_var.get()) * reps_var.get() - pause_var.get()
            total = max(total, 0)
            time_preview.config(text=format_time(total))

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

        ttk.Button(win, text="Guardar cambios", command=save).pack(pady=15)

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

    def _gather_settings(self):
        return {
            "loop_mode": self.loop_mode_var.get(),
            "loop_count": self._parse_int(self.loop_count_var, 1),
            "loop_delay": self._parse_int(self.loop_delay_var, 0),
            "hotkey": self.hotkey_var.get().lower(),
        }

    def _start(self):
        if not self.playlist:
            messagebox.showwarning("Vacío", "No hay scripts en la lista.")
            return
        # Only run enabled items
        active = [item for item in self.playlist if item.get("enabled", True)]
        if not active:
            messagebox.showwarning("Sin habilitados", "No hay scripts habilitados. Activá alguno con el checkbox ✅.")
            return
        self._execute(active, self._gather_settings())

    def _run_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Seleccionar", "Seleccioná un script de la lista para ejecutarlo solo.")
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
        self._set_status("DETENIENDO...", "#f39c12")

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
            messagebox.showerror(
                "Error al lanzar",
                f"No se pudo ejecutar:\n{path}\n\nError: {e}"
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
        else:
            # Infinite mode: show elapsed time
            self.countdown_label.config(text=f"⏱️ {format_time(int(elapsed))}")
            # Progress cycles per-loop based on reps completed is handled by executor callbacks
        self.root.after(500, self._poll_timer)

    def _cb_start_run(self, total_global, total_per_loop, max_loops):
        self._exec_start_time = time.time()
        if max_loops is None:
            self._exec_total_time = None
            self._update_progress(0, total_per_loop)
            self._set_status(f"EJECUTANDO | Loop ∞ | Reps/loop: {total_per_loop}", "#3498db")
        else:
            # Use total time already computed in _execute for the actual playlist
            self._update_progress(0, self._exec_total_time or 1)
            self._set_status(f"EJECUTANDO | Loop 1/{max_loops} | Total reps: {total_global}", "#3498db")
        self._poll_timer()

    def _cb_start_loop(self, current, max_loops, total_global):
        if max_loops is None:
            total_per_loop = self.progress["maximum"]
            self._update_progress(0, total_per_loop)
        if max_loops is None:
            self._set_status(f"EJECUTANDO | Loop {current} (∞)", "#3498db")
        else:
            self._set_status(f"EJECUTANDO | Loop {current}/{max_loops}", "#3498db")

    def _cb_start_item(self, idx, name, reps):
        pass

    def _cb_repeat(self, global_rep, total_global, total_per_loop, name, current, total_item, loop, max_loops):
        # Infinite mode: track per-loop progress by rep count (bar is reset each loop)
        if max_loops is None:
            loop_progress = ((global_rep - 1) % total_per_loop) + 1
            self._update_progress(loop_progress, total_per_loop)

        loop_str = f"L{loop}" if max_loops is None else f"L{loop}/{max_loops}"
        if total_global is None:
            total_str = f"∞"
        else:
            total_str = f"{global_rep}/{total_global}"
        self._set_status(
            f"EJECUTANDO | {loop_str} | {name}: {current}/{total_item} | Total: {total_str}",
            "#3498db",
        )

    def _cb_loop_delay(self, current, delay, total_global):
        self._set_status(f"ESPERANDO | Loop {current} → pausa {delay}s", "#9b59b6")

    def _cb_finish(self, msg, done, total_global, total_per_loop, loops, max_loops):
        self.is_running = False
        loop_str = f"{loops} loops" if max_loops is None else f"{loops}/{max_loops} loops"
        total_str = f"{done}/{total_global}" if total_global is not None else f"{done} (∞)"
        if msg == "Detenido":
            self._set_status(f"DETENIDO | {loop_str} | {total_str} reps", "#e74c3c")
        elif msg == "Completado":
            self._set_status(f"COMPLETADO | {loop_str} | {total_str} reps", "#27ae60")
            messagebox.showinfo("Finalizado", f"Ejecución completada.\n{loop_str}\n{total_str} reps realizadas.")
        else:
            self._set_status(f"{msg} | {loop_str} | {total_str} reps", "#7f8c8d")
        self._update_progress(self._exec_total_time or done, self._exec_total_time or total_per_loop or 1)

    def _set_status(self, text, color):
        """Update the status label with text and background color."""
        self.status_label.config(text=f" {text} ", bg=color)

    def _cb_error(self, msg):
        messagebox.showerror("Error", msg)
        self.is_running = False
        self._set_status(f"Error: {msg}", "#c0392b")

    def _on_close(self):
        settings = self._gather_settings()
        save_config(self.playlist, settings)
        self.hotkey.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = OrchestratorApp(root)
    root.mainloop()
