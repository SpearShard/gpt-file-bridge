from pathlib import Path
p = Path("gemini_mcp_bridge.py")
s = p.read_text(encoding="utf-8")
s = s.replace("_hook_handle = None\n", "_hook_handle = None\n_hook_callback = None\n", 1)
s = s.replace(
    "    global _hook_handle, _hook_thread_id\n",
    "    global _hook_handle, _hook_thread_id, _hook_callback\n",
    1
)
s = s.replace(
    "    @_hook_proc_type\n    def low_level_keyboard_proc(n_code, w_param, l_param):",
    "    @_hook_proc_type\n    def low_level_keyboard_proc(n_code, w_param, l_param):",
    1
)
s = s.replace(
    "    _user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _hook_proc_type, wintypes.HINSTANCE, wintypes.DWORD]\n    _user32.SetWindowsHookExW.restype = wintypes.HHOOK\n    _hook_handle = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, low_level_keyboard_proc, None, 0)",
    "    _hook_callback = low_level_keyboard_proc  # keep callback alive for the lifetime of the hook\n    _user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _hook_proc_type, ctypes.c_void_p, wintypes.DWORD]\n    _user32.SetWindowsHookExW.restype = ctypes.c_void_p\n    _hook_handle = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, _hook_callback, None, 0)",
    1
)
# Ensure mouse clicks visibly travel to their target before clicking.
s = s.replace(
    '    elif action == "click":\n        pyautogui.click(x=x, y=y, button=button, clicks=max(1, min(clicks, 3)))\n    elif action == "double_click":\n        pyautogui.doubleClick(x=x, y=y, button=button)\n    elif action == "right_click":\n        pyautogui.rightClick(x=x, y=y)',
    '    elif action == "click":\n        pyautogui.moveTo(x, y, duration=0.25)\n        pyautogui.click(button=button, clicks=max(1, min(clicks, 3)))\n    elif action == "double_click":\n        pyautogui.moveTo(x, y, duration=0.25)\n        pyautogui.doubleClick(button=button)\n    elif action == "right_click":\n        pyautogui.moveTo(x, y, duration=0.25)\n        pyautogui.rightClick()',
    1
)
p.write_text(s, encoding="utf-8")
print("Hardened ESC hook lifetime and made click movement visible.")
