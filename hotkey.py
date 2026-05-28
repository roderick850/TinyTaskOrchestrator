"""
Global hotkey listener using GetAsyncKeyState polling.

No window, no registration, no admin privileges required.
Polls key state every 50ms — negligible CPU impact.
"""

import ctypes
import threading

user32 = ctypes.windll.user32

_VK_MAP = {f"f{i}": 0x6F + i for i in range(1, 13)}  # f1=0x70 ... f12=0x7B


class HotkeyListener:
    """Listens for a single global hotkey via polling and invokes a callback.

    Usage::

        listener = HotkeyListener()
        listener.start("f10", my_callback)
        ...
        listener.stop()
    """

    def __init__(self):
        self._thread = None
        self._stop_evt = threading.Event()
        self._callback = None
        self._vk = None

    def start(self, hotkey: str, callback):
        """Start listening for *hotkey* (e.g. ``"f10"``). *callback* is called
        (no arguments) when the key is pressed.  Edge-triggered: only fires
        once per press, not continuously while held.

        Raises ``ValueError`` if the hotkey name is invalid."""
        vk = _VK_MAP.get(hotkey.lower())
        if vk is None:
            raise ValueError(f"Tecla no valida: {hotkey}")
        self._vk = vk
        self._callback = callback
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._poll, daemon=True, name="hotkey-poll"
        )
        self._thread.start()

    def stop(self):
        """Stop listening."""
        self._stop_evt.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        self._thread = None

    def _poll(self):
        """Poll GetAsyncKeyState every 50ms.  Edge-triggered: fires callback
        only on the transition from released → pressed."""
        was_down = False
        while not self._stop_evt.is_set():
            is_down = (user32.GetAsyncKeyState(self._vk) & 0x8000) != 0
            if is_down and not was_down:
                try:
                    self._callback()
                except Exception:
                    pass
            was_down = is_down
            self._stop_evt.wait(0.05)  # 50ms = 20 polls/sec
