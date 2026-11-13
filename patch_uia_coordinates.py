from pathlib import Path
import re

p = Path("gemini_mcp_bridge.py")
s = p.read_text(encoding="utf-8")
start = s.index("@mcp.tool()\ndef desktop_inspect_ui(")
end = s.index("@mcp.tool()\ndef desktop_click_ui_element(", start)

replacement = r'''@mcp.tool()
def desktop_inspect_ui(window_title: str = "", max_elements: int = 100) -> str:
    """Inspects visible, non-minimized Windows UI Automation controls with validated physical-screen coordinates."""
    try:
        import win32gui
        import pyautogui
        from pywinauto import Desktop

        screen_w, screen_h = pyautogui.size()
        desktop = Desktop(backend="uia")
        needle = (window_title or "").casefold()
        output = []
        seen_handles = set()

        for win in desktop.windows():
            try:
                handle = int(win.handle)
                if handle in seen_handles:
                    continue
                seen_handles.add(handle)

                title = win.window_text() or ""
                if needle and needle not in title.casefold():
                    continue
                if not win32gui.IsWindowVisible(handle) or win32gui.IsIconic(handle):
                    continue

                left, top, right, bottom = win32gui.GetWindowRect(handle)
                # Reject minimized/off-screen/invalid window rectangles.
                if right <= left or bottom <= top:
                    continue
                if right <= 0 or bottom <= 0 or left >= screen_w or top >= screen_h:
                    continue
                if left < -screen_w or top < -screen_h:
                    continue

                output.append(
                    f"WINDOW: {title!r} | handle={handle} "
                    f"| rect={{'left': {left}, 'top': {top}, 'right': {right}, 'bottom': {bottom}}}"
                )
                count = 0
                for ctrl in win.descendants():
                    if count >= max(1, min(int(max_elements), 200)):
                        output.append("  ... element limit reached")
                        break
                    try:
                        info = ctrl.element_info
                        name = (info.name or "").strip()
                        ctype = info.control_type or "Unknown"
                        aid = info.automation_id or ""
                        if not name and not aid:
                            continue
                        if not ctrl.is_visible():
                            continue
                        try:
                            enabled = ctrl.is_enabled()
                        except Exception:
                            enabled = False
                        r = ctrl.rectangle()
                        cl, ct, cr, cb = r.left, r.top, r.right, r.bottom
                        # Keep only plausible screen-space bounds. Some providers
                        # expose sentinel coordinates for minimized/off-screen children.
                        if cr <= cl or cb <= ct:
                            continue
                        if cr <= 0 or cb <= 0 or cl >= screen_w or ct >= screen_h:
                            continue
                        if cl < -screen_w or ct < -screen_h:
                            continue
                        cx = max(0, min(screen_w - 1, (cl + cr) // 2))
                        cy = max(0, min(screen_h - 1, (ct + cb) // 2))
                        output.append(
                            f"  {ctype} | name={name!r} | automation_id={aid!r} "
                            f"| enabled={enabled} | rect=({cl},{ct},{cr},{cb}) "
                            f"| center=({cx},{cy})"
                        )
                        count += 1
                    except Exception:
                        continue
            except Exception:
                continue

        return "\n".join(output) if output else "No visible, non-minimized matching windows/elements found."
    except Exception as exc:
        return f"UI inspection failed: {type(exc).__name__}: {exc}"

'''
s = s[:start] + replacement + s[end:]
p.write_text(s, encoding="utf-8")
print("Replaced UI inspector with visible-window filtering and validated screen coordinates.")
