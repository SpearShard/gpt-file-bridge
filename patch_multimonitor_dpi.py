from pathlib import Path
import shutil

p = Path("gemini_mcp_bridge.py")
src = p.read_text(encoding="utf-8")
backup = p.with_name("gemini_mcp_bridge.py.before-multimonitor.bak")
if not backup.exists():
    shutil.copy2(p, backup)

# Establish Per-Monitor-V2 DPI awareness before pyautogui / GUI libraries load.
dpi_block = r'''
# Use physical virtual-desktop coordinates consistently across mixed-DPI monitors.
# This must run before pyautogui, tkinter, or other GUI libraries are imported.
try:
    import ctypes as _dpi_ctypes
    _dpi_user32 = _dpi_ctypes.WinDLL("user32", use_last_error=True)
    _set_dpi_context = getattr(_dpi_user32, "SetProcessDpiAwarenessContext", None)
    if _set_dpi_context is not None:
        _set_dpi_context(_dpi_ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    else:
        _dpi_ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

'''
if "SetProcessDpiAwarenessContext" not in src:
    src = src.replace("import time\n", "import time\n" + dpi_block, 1)

# Replace screenshot with a capture of the complete virtual desktop.
start = src.index("@mcp.tool()\ndef desktop_screenshot(")
end = src.index("@mcp.tool()\ndef desktop_mouse(", start)
screenshot = r'''@mcp.tool()
def desktop_screenshot() -> ImageContent:
    """Returns a screenshot spanning every monitor in the Windows virtual desktop."""
    from PIL import ImageGrab
    shot = ImageGrab.grab(all_screens=True)
    buffer = io.BytesIO()
    shot.save(buffer, format="PNG")
    return ImageContent(type="image", data=base64.b64encode(buffer.getvalue()).decode("ascii"), mimeType="image/png")


'''
src = src[:start] + screenshot + src[end:]

# Replace mouse with virtual-desktop aware Win32 input (supports negative origins too).
start = src.index("@mcp.tool()\ndef desktop_mouse(")
end = src.index("@mcp.tool()\ndef desktop_type_text(", start)
mouse = r'''@mcp.tool()
def desktop_mouse(action: str, x: int = 0, y: int = 0, button: str = "left", clicks: int = 1, amount: int = 0) -> str:
    """Controls the mouse in physical virtual-desktop coordinates across all monitors."""
    import win32api
    import win32con
    import win32gui

    allowed = {"move", "click", "double_click", "right_click", "scroll"}
    if action not in allowed:
        return "Invalid action. Use move, click, double_click, right_click, or scroll."
    if button not in {"left", "right", "middle"}:
        return "Invalid mouse button."

    left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
    top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
    width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
    height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
    if not (left <= x < left + width and top <= y < top + height):
        return f"Coordinates must be within virtual desktop bounds x={left}..{left+width-1}, y={top}..{top+height-1}."

    if _control_allowed():
        _ensure_overlay_started()
    if not _control_allowed():
        return "CONTROL STOPPED: no mouse action was performed. Explicitly resume desktop control to continue."

    win32api.SetCursorPos((int(x), int(y)))
    flags = {
        "left": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
        "right": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP),
        "middle": (win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP),
    }
    if action == "move":
        pass
    elif action in {"click", "double_click", "right_click"}:
        chosen = "right" if action == "right_click" else button
        down, up = flags[chosen]
        count = 2 if action == "double_click" else max(1, min(int(clicks), 3))
        for _ in range(count):
            win32api.mouse_event(down, 0, 0, 0, 0)
            win32api.mouse_event(up, 0, 0, 0, 0)
            if count > 1:
                time.sleep(0.06)
    elif action == "scroll":
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, int(max(-20, min(amount, 20)) * 120), 0)

    _sync_input_baseline()
    return f"Mouse {action} completed at virtual-desktop coordinate ({x}, {y}); bounds=({left},{top},{width},{height})."


'''
src = src[:start] + mouse + src[end:]

# Replace inspector screen metrics and stop clamping coordinates to the primary monitor.
src = src.replace(
    "        screen_w, screen_h = pyautogui.size()\n        desktop = Desktop(backend=\"uia\")",
    "        import win32api, win32con\n        screen_left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)\n        screen_top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)\n        screen_w = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)\n        screen_h = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)\n        desktop = Desktop(backend=\"uia\")",
    1
)
src = src.replace(
    "        # Only inspect the foreground application: other visible windows may be\n        # covered by it and therefore cannot be verified against the screenshot.\n        foreground_handle = int(win32gui.GetForegroundWindow())\n",
    "        # Inspect the foreground window; its native Win32 bounds are in physical screen coordinates.\n        foreground_handle = int(win32gui.GetForegroundWindow())\n",
    1
)
src = src.replace(
    "                left, top, right, bottom = win32gui.GetWindowRect(handle)",
    "                left, top, right, bottom = win32gui.GetWindowRect(handle)",
    1
)
src = src.replace(
    "                if right <= 0 or bottom <= 0 or left >= screen_w or top >= screen_h:\n                    continue\n                if left < -screen_w or top < -screen_h:\n                    continue",
    "                if right <= screen_left or bottom <= screen_top or left >= screen_left + screen_w or top >= screen_top + screen_h:\n                    continue",
    1
)
# UIA child rectangles are physical after Per-Monitor-V2 awareness; report their raw centers.
src = src.replace(
    "                        if cr <= 0 or cb <= 0 or cl >= screen_w or ct >= screen_h:\n                            continue\n                        if cl < -screen_w or ct < -screen_h:\n                            continue\n                        cx = max(0, min(screen_w - 1, (cl + cr) // 2))\n                        cy = max(0, min(screen_h - 1, (ct + cb) // 2))",
    "                        if cr <= screen_left or cb <= screen_top or cl >= screen_left + screen_w or ct >= screen_top + screen_h:\n                            continue\n                        cx = (cl + cr) // 2\n                        cy = (ct + cb) // 2",
    1
)

# Find-target uses the same virtual bounds and does not clamp coordinates to monitor 1.
src = src.replace(
    "        screen_w, screen_h = pyautogui.size()\n        foreground = int(win32gui.GetForegroundWindow())",
    "        import win32api, win32con\n        screen_left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)\n        screen_top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)\n        screen_w = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)\n        screen_h = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)\n        foreground = int(win32gui.GetForegroundWindow())",
    1
)
src = src.replace(
    "                        if right <= left or bottom <= top or right <= 0 or bottom <= 0:\n                            continue\n                        if left >= screen_w or top >= screen_h or left < -screen_w or top < -screen_h:\n                            continue",
    "                        if right <= left or bottom <= top:\n                            continue\n                        if right <= screen_left or bottom <= screen_top or left >= screen_left + screen_w or top >= screen_top + screen_h:\n                            continue",
    1
)
src = src.replace(
    '"center": (max(0, min(screen_w - 1, (left + right) // 2)),\n                                       max(0, min(screen_h - 1, (top + bottom) // 2))),',
    '"center": ((left + right) // 2, (top + bottom) // 2),',
    1
)

p.write_text(src, encoding="utf-8")
print(f"Patched {p}; backup: {backup.name}")
