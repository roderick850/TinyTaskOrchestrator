import keyboard
import threading
import time


class HotkeyListener:
    def __init__(self):
        self._stop_callback = None
        self._thread = None
        self._running = threading.Event()
        self._hotkey = "f10"

    def _listen(self):
        keyboard.add_hotkey(self._hotkey, self._on_hotkey)
        self._running.set()
        while self._running.is_set():
            time.sleep(0.5)
        keyboard.unhook_all()

    def start(self, hotkey, stop_callback):
        self._hotkey = hotkey
        self._stop_callback = stop_callback
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def _on_hotkey(self):
        if self._stop_callback:
            try:
                self._stop_callback()
            except Exception:
                pass

    def stop(self):
        self._running.clear()

    def restart(self, hotkey, stop_callback):
        self.stop()
        if self._thread is not None:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                # Force cleanup: unhook all keys so the old thread can exit
                keyboard.unhook_all()
                self._thread.join(timeout=2)
        self._running = threading.Event()
        self.start(hotkey, stop_callback)
