from pathlib import Path

p = Path("gemini_mcp_bridge.py")
s = p.read_text(encoding="utf-8")
marker = "# Register Phase 1 agent tools on this existing MCP server."
if "def desktop_list_windows(" not in s:
    addition = r'''

@mcp.tool()
def desktop_list_windows(include_minimized: bool = True, max_windows: int = 80) -> str:
    """Lists top-level Windows applications across the interactive Windows desktop, including windows not in the foreground. Read-only."""
    try:
        import win32gui
        import win32process
        import win32api
        import win32con
        from pywinauto import Desktop

        rows = []
        foreground = int(win32gui.GetForegroundWindow())
        desktop = Desktop(backend="uia")
        seen = set()
        for win in desktop.windows():
            try:
                hwnd = int(win.handle)
                if not hwnd or hwnd in seen or not win32gui.IsWindow(hwnd):
                    continue
                seen.add(hwnd)
                title = (win.window_text() or "").strip()
                if not title:
                    continue
                minimized = bool(win32gui.IsIconic(hwnd))
                visible = bool(win32gui.IsWindowVisible(hwnd))
                if not include_minimized and minimized:
                    continue
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    process = win32api.GetModuleFileNameEx(
                        win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid), 0
                    )
                    process = process.rsplit("\\\\", 1)[-1]
                except Exception:
                    process = "unknown"
                try:
                    rect = win32gui.GetWindowRect(hwnd)
                except Exception:
                    rect = None
                flags = []
                if hwnd == foreground:
                    flags.append("FOREGROUND")
                if minimized:
                    flags.append("MINIMIZED")
                if not visible:
                    flags.append("HIDDEN")
                rows.append((hwnd == foreground, f"{title!r} | process={process} | hwnd={hwnd} | {'/'.join(flags) or 'background'} | rect={rect}"))
            except Exception:
                continue
        rows.sort(key=lambda x: x[0], reverse=True)
        lines = [r[1] for r in rows[:max(1, min(int(max_windows), 200))]]
        return "WINDOW ENUMERATION (read-only)\n" + ("\n".join(lines) if lines else "No titled application windows found.")
    except Exception as exc:
        return f"Window enumeration failed: {type(exc).__name__}: {exc}"


@mcp.tool()
def desktop_inspect_window(window_title: str, max_elements: int = 120) -> str:
    """Inspects UI Automation controls in a specifically named window, even when it is not foreground. Read-only; does not switch desktops or send input."""
    try:
        import win32gui
        from pywinauto import Desktop

        needle = (window_title or "").strip().casefold()
        if not needle:
            return "Provide part of the target window title, e.g. 'sp-inventory-app' or 'Visual Studio Code'."
        desktop = Desktop(backend="uia")
        matches = []
        for win in desktop.windows():
            try:
                title = (win.window_text() or "").strip()
                if needle in title.casefold():
                    matches.append((win, title, int(win.handle)))
            except Exception:
                continue
        if not matches:
            return f"No window title containing {window_title!r} was found. Run desktop_list_windows to see available windows."
        if len(matches) > 1:
            return "Multiple windows matched; narrow the title:\n" + "\n".join(
                f"{title!r} | hwnd={hwnd}" for _, title, hwnd in matches[:20]
            )

        win, title, hwnd = matches[0]
        foreground = int(win32gui.GetForegroundWindow())
        minimized = bool(win32gui.IsIconic(hwnd))
        visible = bool(win32gui.IsWindowVisible(hwnd))
        rect = win32gui.GetWindowRect(hwnd)
        lines = [
            f"WINDOW: {title!r} | hwnd={hwnd}",
            f"foreground={hwnd == foreground} | visible={visible} | minimized={minimized} | rect={rect}",
            "Inspection is via UI Automation metadata; no focus change, screenshot, mouse, or keyboard input was performed."
        ]
        count = 0
        for ctrl in win.descendants():
            if count >= max(1, min(int(max_elements), 250)):
                lines.append("... element limit reached")
                break
            try:
                info = ctrl.element_info
                name = (info.name or "").strip()
                aid = (info.automation_id or "").strip()
                ctype = (info.control_type or "Unknown").strip()
                if not name and not aid:
                    continue
                try:
                    enabled = bool(ctrl.is_enabled())
                except Exception:
                    enabled = False
                try:
                    ctrl_visible = bool(ctrl.is_visible())
                except Exception:
                    ctrl_visible = False
                r = ctrl.rectangle()
                bounds = (r.left, r.top, r.right, r.bottom)
                lines.append(
                    f"  {ctype} | name={name!r} | automation_id={aid!r} "
                    f"| enabled={enabled} | visible={ctrl_visible} | rect={bounds}"
                )
                count += 1
            except Exception:
                continue
        return "\n".join(lines)
    except Exception as exc:
        return f"Window inspection failed: {type(exc).__name__}: {exc}"

'''
    if marker not in s:
        raise SystemExit("Insertion marker not found; no changes made.")
    s = s.replace(marker, addition + "\n" + marker, 1)
    p.write_text(s, encoding="utf-8")
    print("Added desktop_list_windows and desktop_inspect_window.")
else:
    print("Window enumeration tools already exist; no changes made.")
