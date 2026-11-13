import win32gui
from pywinauto import Desktop
from pywinauto.uia_defines import IUIA

title_part = ".metadata - sp-inventory-app - Visual Studio Code"
hwnd = None
for w in Desktop(backend="uia").windows():
    if title_part.casefold() in (w.window_text() or "").casefold():
        hwnd = int(w.handle)
        break
print("foreground:", win32gui.GetWindowText(win32gui.GetForegroundWindow()))
print("target hwnd:", hwnd)
focused = IUIA().get_focused_element()
def props(el):
    out = {}
    for key in ("CurrentName","CurrentAutomationId","CurrentClassName","CurrentLocalizedControlType","CurrentHasKeyboardFocus","CurrentIsKeyboardFocusable","CurrentControlType"):
        try: out[key] = getattr(el, key)
        except Exception as e: out[key] = type(e).__name__
    return out
print("FOCUSED:", props(focused))
if hwnd:
    root = Desktop(backend="uia").window(handle=hwnd)
    for c in root.descendants():
        try:
            name = c.window_text() or ""
            if "terminal" in name.casefold() and c.is_visible():
                el = c.element_info.element
                print("MATCH:", c.element_info.control_type, repr(name[:220]), props(el))
        except Exception:
            pass
