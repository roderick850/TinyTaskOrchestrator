"""
Global hotkey listener using the native Windows RegisterHotKey API.

Replaces the ``keyboard`` library. RegisterHotKey is the standard
Windows mechanism for system-wide hotkeys — it works regardless of focus
and does not depend on low-level keyboard hooks.
"""

import ctypes
from ctypes import wintypes
import threading

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_HOTKEY = 0x0312
WM_NULL = 0x0000
WM_DESTROY = 0x0002
MOD_NOREPEAT = 0x4000

_VK_FKEYS = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}

# Fallback order: try these VK codes if the requested one fails
_FALLBACK_VKS = [0x78, 0x77, 0x76, 0x75, 0x7B]  # F9, F8, F7, F6, F12


class HotkeyListener:
    """Listens for a single global hotkey and invokes a callback.

    The callback is always called from the **main** tkinter thread via
    ``root.after(0, ...)``, which is safe and avoids cross-thread GUI issues.
    """

    def __init__(self):
        self._hotkey_id = 1
        self._hwnd = None
        self._thread = None
        self._stop_event = threading.Event()
        self._ready = threading.Event()
        self._callback = None
        self._active_vk = None
        self._active_name = None

    # ── public API ──────────────────────────────────────────────────

    def start(self, hotkey: str, stop_callback):
        """Register *hotkey* (e.g. ``"f10"``) and call *stop_callback* when
        pressed. Returns the actual hotkey name that was registered,
        which may differ from the requested one if a fallback was used.
        Raises RuntimeError only if ALL hotkeys (including fallbacks) fail."""
        self._callback = stop_callback
        self._stop_event.clear()
        self._ready.clear()

        requested_vk = _VK_FKEYS.get(hotkey.lower(), 0x79)
        vks_to_try = [requested_vk] + [vk for vk in _FALLBACK_VKS if vk != requested_vk]
        last_error = None

        for vk in vks_to_try:
            self._thread = threading.Thread(
                target=self._message_loop,
                args=(vk,),
                daemon=True,
                name="hotkey-thread",
            )
            self._thread.start()

            if self._ready.wait(timeout=5):
                if self._active_vk is not None:
                    # Success!
                    return self._active_name
                else:
                    # Thread set ready but hotkey failed — try next
                    self.stop()
                    continue
            else:
                # Thread never became ready — kill it and try next
                self._stop_event.set()
                if self._hwnd:
                    user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
                if self._thread.is_alive():
                    self._thread.join(timeout=1)
                last_error = f"timeout for {hotkey}"

        raise RuntimeError(
            f"No se pudo registrar ninguna hotkey (F5-F12). "
            f"Puede que todas esten en uso por otros programas."
        )

    def stop(self):
        """Unregister the hotkey and tear down the listener thread."""
        self._stop_event.set()
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def restart(self, hotkey: str, stop_callback):
        """Convenience: stop + start with the new hotkey."""
        self.stop()
        return self.start(hotkey, stop_callback)

    # ── internal: hidden window + message pump ──────────────────────

    def _message_loop(self, vk: int):
        """Create a message-only window, register the hotkey, and run
        a message pump until ``stop()`` is called."""

        try:
            hinst = kernel32.GetModuleHandleW(None)

            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_long, wintypes.HWND, wintypes.UINT,
                wintypes.WPARAM, wintypes.LPARAM,
            )
            self._wndproc = WNDPROC(self._window_proc)

            wc = wintypes.WNDCLASSW()
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.lpszClassName = "TinyTaskHotkeyCls"
            atom = user32.RegisterClassW(ctypes.byref(wc))
            if not atom:
                self._ready.set()
                return

            HWND_MESSAGE = -3
            self._hwnd = user32.CreateWindowExW(
                0, atom, None, 0, 0, 0, 0, 0,
                wintypes.HWND(HWND_MESSAGE), None, hinst, None,
            )
            if not self._hwnd:
                user32.UnregisterClassW(atom, hinst)
                self._ready.set()
                return

            # RegisterHotKey: try without MOD_NOREPEAT first as fallback
            registered = user32.RegisterHotKey(
                self._hwnd, self._hotkey_id, MOD_NOREPEAT, vk
            )
            if not registered:
                # Try without MOD_NOREPEAT
                registered = user32.RegisterHotKey(
                    self._hwnd, self._hotkey_id, 0, vk
                )

            if not registered:
                user32.DestroyWindow(self._hwnd)
                user32.UnregisterClassW(atom, hinst)
                self._hwnd = None
                self._active_vk = None
                self._ready.set()
                return

            # Resolve VK code back to name string
            for name, code in _VK_FKEYS.items():
                if code == vk:
                    self._active_name = name
                    break
            else:
                self._active_name = f"f{vk - 0x70 + 1}"
            self._active_vk = vk
            self._ready.set()

            # message pump
            msg = wintypes.MSG()
            while not self._stop_event.is_set():
                if user32.PeekMessageW(ctypes.byref(msg), self._hwnd,
                                       0, 0, 1):
                    if msg.message == WM_DESTROY:
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                else:
                    self._stop_event.wait(0.05)

            # cleanup
            user32.UnregisterHotKey(self._hwnd, self._hotkey_id)
            user32.DestroyWindow(self._hwnd)
            user32.UnregisterClassW(atom, hinst)
            self._hwnd = None
        except Exception:
            # If anything in the thread crashes, signal ready so the
            # main thread doesn't hang, then re-raise in thread context.
            self._ready.set()
            raise

    def _window_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_HOTKEY and wparam == self._hotkey_id:
            if self._callback is not None:
                try:
                    self._callback()
                except Exception:
                    pass
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
