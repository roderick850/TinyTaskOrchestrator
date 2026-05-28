import subprocess
import threading
import time
import os


class Executor(threading.Thread):
    def __init__(self, playlist, settings, callbacks, stop_event, launch_event):
        super().__init__(daemon=True)
        self.playlist = playlist
        self.settings = settings
        self.callbacks = callbacks
        self.stop_event = stop_event
        self.launch_event = launch_event

    def run(self):
        if len(self.playlist) == 0:
            self._safe_callback("on_finish", "Lista vacía", 0, 0, 0, 0)
            return

        loop_mode = self.settings.get("loop_mode", "once")
        loop_count = self.settings.get("loop_count", 1)
        loop_delay = self.settings.get("loop_delay", 0)

        if loop_mode == "infinite":
            max_loops = None
        elif loop_mode == "fixed":
            max_loops = loop_count
        else:
            max_loops = 1

        total_reps_per_loop = sum(item["repetitions"] for item in self.playlist)

        # Total global real (None si es infinito)
        if max_loops is None:
            total_global_reps = None
        else:
            total_global_reps = total_reps_per_loop * max_loops

        current_loop = 0
        completed_reps_total = 0

        # Allow Windows to fully clean up the previous process context
        time.sleep(1)

        self._safe_callback("on_start_run", total_global_reps, total_reps_per_loop, max_loops)

        while True:
            if self.stop_event.is_set():
                break

            if max_loops is not None and current_loop >= max_loops:
                break

            current_loop += 1

            self._safe_callback("on_start_loop", current_loop, max_loops, total_global_reps)

            for idx, item in enumerate(self.playlist):
                if self.stop_event.is_set():
                    break

                name = os.path.basename(item["path"])
                reps = item["repetitions"]
                duration = item["duration"]
                pause = item["pause"]

                self._safe_callback("on_start_item", idx, name, reps)

                for r in range(reps):
                    if self.stop_event.is_set():
                        break

                    self._safe_callback(
                        "on_repeat",
                        completed_reps_total + 1,
                        total_global_reps,
                        total_reps_per_loop,
                        name,
                        r + 1,
                        reps,
                        current_loop,
                        max_loops,
                    )

                    try:
                        self.launch_event.clear()
                        self._safe_callback("on_launch", item["path"])
                        if not self.launch_event.wait(timeout=10):
                            raise TimeoutError("El hilo principal no pudo lanzar el .exe")

                        # Buffer: give Windows time to create and show the process window
                        time.sleep(2.0)
                    except Exception as e:
                        self._safe_callback("on_error", str(e))
                        break

                    # Count the repetition NOW (after successful launch, before duration)
                    completed_reps_total += 1

                    # Wait the configured duration (checking stop frequently)
                    interval = 0.1
                    slept = 0.0
                    while slept < duration and not self.stop_event.is_set():
                        time.sleep(interval)
                        slept += interval

                    if self.stop_event.is_set():
                        break

                    # Pause between repetitions (except after the last one)
                    if r < reps - 1 and pause > 0:
                        slept = 0.0
                        while slept < pause and not self.stop_event.is_set():
                            time.sleep(interval)
                            slept += interval

                    if self.stop_event.is_set():
                        break

            # Delay between full loops (except after the last loop)
            if (max_loops is None or current_loop < max_loops) and loop_delay > 0:
                self._safe_callback("on_loop_delay", current_loop, loop_delay, total_global_reps)
                slept = 0.0
                while slept < loop_delay and not self.stop_event.is_set():
                    time.sleep(0.1)
                    slept += 0.1

            if self.stop_event.is_set():
                break

        self._safe_callback(
            "on_finish",
            "Detenido" if self.stop_event.is_set() else "Completado",
            completed_reps_total,
            total_global_reps,
            total_reps_per_loop,
            current_loop,
            max_loops,
        )

    def _safe_callback(self, name, *args):
        cb = self.callbacks.get(name)
        if cb:
            try:
                cb(*args)
            except Exception as e:
                print(f"Callback error {name}: {e}")
