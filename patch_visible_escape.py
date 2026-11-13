from pathlib import Path

p = Path("gemini_mcp_bridge.py")
s = p.read_text(encoding="utf-8")
start = s.index("# Human takeover guard (Windows):")
end = s.index("@mcp.tool()\ndef desktop_screenshot()", start)

replacement = r'''# Visible desktop-control overlay + global ESC emergency stop (Windows).
# ESC is intercepted by a low-level keyboard hook while control is active.
import ctypes
from ctypes import wintypes
import threading
import tkinter as tk

_control_lock = threading.RLock()
_control_paused = False
_overlay_root = None
_overlay_status = None
_overlay_button = None
_hook_thread_id = None

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012
VK_ESCAPE = 0x1B
HC_ACTION = 0
LLKHF_UP = 0x80

LRESULT = ctypes.c_ssize_t
ULONG_PTR = wintypes.WPARAM
KBDLLHOOKSTRUCT_PTR = ctypes.POINTER(ctypes.c_ulong * 5)
_hook_proc_type = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_hook_handle = None

def _set_control_paused(paused: bool) -> None:
    global _control_paused
    with _control_lock:
        _control_paused = paused
    root = _overlay_root
    if root is not None:
        try:
            root.after(0, _refresh_overlay)
        except Exception:
            pass

def _control_allowed() -> bool:
    with _control_lock:
        return not _control_paused

def _sync_input_baseline() -> None:
    # Kept as a no-op for compatibility with existing desktop action tools.
    return None

def _refresh_overlay() -> None:
    if _overlay_status is None or _overlay_button is None:
        return
    active = _control_allowed()
    _overlay_status.config(
        text="DESKTOP CONTROL  •  ACTIVE" if active else "DESKTOP CONTROL  •  STOPPED",
        fg="#16a34a" if active else "#f87171",
    )
    _overlay_button.config(
        text="⎋  PRESS ESC TO STOP" if active else "CONTROL STOPPED",
        state="normal" if active else "disabled",
        bg="#dc2626" if active else "#374151",
        activebackground="#b91c1c",
    )

def _stop_from_overlay() -> None:
    _set_control_paused(True)

def _overlay_main() -> None:
    global _overlay_root, _overlay_status, _overlay_button
    root = tk.Tk()
    _overlay_root = root
    root.title("Desktop Control")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#111827")
    width, height = 340, 76
    screen_w = root.winfo_screenwidth()
    x = (screen_w - width) // 2
    root.geometry(f"{width}x{height}+{x}+12")

    frame = tk.Frame(root, bg="#111827", padx=10, pady=7)
    frame.pack(fill="both", expand=True)
    _overlay_status = tk.Label(
        frame, text="DESKTOP CONTROL  •  ACTIVE",
        bg="#111827", fg="#16a34a",
        font=("Segoe UI", 9, "bold")
    )
    _overlay_status.pack()
    _overlay_button = tk.Button(
        frame, text="⎋  PRESS ESC TO STOP",
        command=_stop_from_overlay, relief="flat",
        bg="#dc2626", fg="white", activebackground="#b91c1c",
        activeforeground="white", font=("Segoe UI", 10, "bold"),
        padx=14, pady=5, cursor="hand2"
    )
    _overlay_button.pack(fill="x", pady=(4, 0))
    _refresh_overlay()
    root.mainloop()

def _keyboard_hook_main() -> None:
    global _hook_handle, _hook_thread_id
    _hook_thread_id = _kernel32.GetCurrentThreadId()

    @_hook_proc_type
    def low_level_keyboard_proc(n_code, w_param, l_param):
        if n_code == HC_ACTION and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
            vk_code = ctypes.cast(l_param, ctypes.POINTER(ctypes.c_ulong)).contents.value
            if vk_code == VK_ESCAPE and _control_allowed():
                _set_control_paused(True)
                return 1  # swallow ESC so it cannot trigger the foreground app
        return _user32.CallNextHookEx(_hook_handle, n_code, w_param, l_param)

    _user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _hook_proc_type, wintypes.HINSTANCE, wintypes.DWORD]
    _user32.SetWindowsHookExW.restype = wintypes.HHOOK
    _hook_handle = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, low_level_keyboard_proc, None, 0)
    if not _hook_handle:
        return
    msg = wintypes.MSG()
    while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        _user32.TranslateMessage(ctypes.byref(msg))
        _user32.DispatchMessageW(ctypes.byref(msg))

def _start_desktop_safety_ui() -> None:
    threading.Thread(target=_overlay_main, name="desktop-control-overlay", daemon=True).start()
    threading.Thread(target=_keyboard_hook_main, name="desktop-escape-hook", daemon=True).start()

_start_desktop_safety_ui()

@mcp.tool()
def desktop_control_status() -> str:
    """Reports whether desktop automation is active or stopped."""
    return "STOPPED: ESC or the overlay emergency button stopped desktop control." if not _control_allowed() else "ACTIVE: desktop control is enabled. Press ESC at any time to stop."

@mcp.tool()
def desktop_resume_control() -> str:
    """Resumes desktop automation only when explicitly requested by the user."""
    _set_control_paused(False)
    return "Desktop control resumed. The always-on-top ESC emergency stop is active."

'''
s = s[:start] + replacement + s[end:]
s = s.replace(
    'return "CONTROL PAUSED: human mouse/keyboard activity detected. No action performed. Wait for the user to explicitly resume."',
    'return "CONTROL STOPPED: no mouse action was performed. Explicitly resume desktop control to continue."'
)
s = s.replace(
    'return f"CONTROL PAUSED: human input detected after {typed}/{len(text)} characters. Remaining text was not typed."',
    'return f"CONTROL STOPPED after {typed}/{len(text)} characters. Remaining text was not typed."'
)
s = s.replace(
    'return "CONTROL PAUSED: human input detected. No key was pressed. Wait for the user to explicitly resume."',
    'return "CONTROL STOPPED: no key was pressed. Explicitly resume desktop control to continue."'
)
s = s.replace(
    'return "CONTROL PAUSED: human input detected. No shortcut was sent. Wait for the user to explicitly resume."',
    'return "CONTROL STOPPED: no shortcut was sent. Explicitly resume desktop control to continue."'
)
p.write_text(s, encoding="utf-8")
print("Patched visible overlay and global ESC stop.")
