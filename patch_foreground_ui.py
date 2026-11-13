from pathlib import Path

p = Path("gemini_mcp_bridge.py")
s = p.read_text(encoding="utf-8")
old = """        output = []
        seen_handles = set()

        for win in desktop.windows():
"""
new = """        output = []
        seen_handles = set()
        # Only inspect the foreground application: other visible windows may be
        # covered by it and therefore cannot be verified against the screenshot.
        foreground_handle = int(win32gui.GetForegroundWindow())

        for win in desktop.windows():
"""
if old not in s:
    raise SystemExit("Patch anchor 1 not found; no changes made.")
s = s.replace(old, new, 1)
old2 = """                if handle in seen_handles:
                    continue
                seen_handles.add(handle)

                title = win.window_text() or ""
"""
new2 = """                if handle in seen_handles or handle != foreground_handle:
                    continue
                seen_handles.add(handle)

                title = win.window_text() or ""
"""
if old2 not in s:
    raise SystemExit("Patch anchor 2 not found; no changes made.")
s = s.replace(old2, new2, 1)
p.write_text(s, encoding="utf-8")
print("Updated desktop_inspect_ui to report only the foreground window.")
