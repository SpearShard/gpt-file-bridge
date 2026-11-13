from pathlib import Path

p = Path("gemini_mcp_bridge.py")
s = p.read_text(encoding="utf-8")
marker = "# Register Phase 1 agent tools on this existing MCP server."
assert marker in s
assert "def desktop_inspect_ui(" not in s

block = r'''
# ---------------------------------------------------------------------------
# Windows UI Automation: inspect accessible controls by semantic properties.
# This complements screenshots; it does not infer labels from pixels.
# ---------------------------------------------------------------------------

def _uia_desktop():
    try:
        from pywinauto import Desktop
        return Desktop(backend="uia")
    except Exception as exc:
        raise RuntimeError(f"Windows UI Automation unavailable: {exc}")

def _uia_rect(control):
    try:
        r = control.rectangle()
        return {"left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom}
    except Exception:
        return None

@mcp.tool()
def desktop_inspect_ui(window_title: str = "", max_elements: int = 100) -> str:
    """Inspects visible Windows UI Automation controls. Optionally filter by a window title substring."""
    try:
        desktop = _uia_desktop()
        windows = desktop.windows()
        output = []
        needle = (window_title or "").casefold()
        for win in windows:
            try:
                title = win.window_text() or ""
                if needle and needle not in title.casefold():
                    continue
                output.append(f"WINDOW: {title!r} | handle={win.handle}")
                controls = win.descendants()
                count = 0
                for ctrl in controls:
                    if count >= max(1, min(int(max_elements), 200)):
                        output.append("  ... element limit reached")
                        break
                    try:
                        info = ctrl.element_info
                        name = (info.name or "").strip()
                        ctype = info.control_type or "Unknown"
                        aid = info.automation_id or ""
                        rect = _uia_rect(ctrl)
                        if not name and not aid:
                            continue
                        try:
                            visible = ctrl.is_visible()
                        except Exception:
                            visible = False
                        try:
                            enabled = ctrl.is_enabled()
                        except Exception:
                            enabled = False
                        if not visible:
                            continue
                        output.append(
                            f"  {ctype} | name={name!r} | automation_id={aid!r} "
                            f"| enabled={enabled} | rect={rect}"
                        )
                        count += 1
                    except Exception:
                        continue
            except Exception:
                continue
        return "\n".join(output) if output else "No matching visible UI Automation elements found."
    except Exception as exc:
        return f"UI inspection failed: {type(exc).__name__}: {exc}"

@mcp.tool()
def desktop_click_ui_element(window_title: str, name: str, control_type: str = "", automation_id: str = "") -> str:
    """Clicks one uniquely matched UI Automation element by its accessible name, optionally narrowed by type or automation ID. Refuses ambiguous matches."""
    if not _control_allowed():
        return "CONTROL STOPPED: UI element was not clicked. Explicitly resume desktop control first."
    if not window_title.strip() or not (name.strip() or automation_id.strip()):
        return "Provide a window_title and either name or automation_id."
    try:
        desktop = _uia_desktop()
        wins = desktop.windows(title_re=".*" + __import__("re").escape(window_title) + ".*")
        matches = []
        for win in wins:
            for ctrl in win.descendants():
                try:
                    info = ctrl.element_info
                    if name and (info.name or "").strip().casefold() != name.strip().casefold():
                        continue
                    if automation_id and (info.automation_id or "") != automation_id:
                        continue
                    if control_type and (info.control_type or "").casefold() != control_type.casefold():
                        continue
                    if not ctrl.is_visible() or not ctrl.is_enabled():
                        continue
                    matches.append((win, ctrl))
                except Exception:
                    continue
        if len(matches) != 1:
            if not matches:
                return "No enabled, visible element matched. Run desktop_inspect_ui to inspect available controls."
            return f"Refusing to click: {len(matches)} elements matched. Narrow by control_type or automation_id."
        _ensure_overlay_started()
        if not _control_allowed():
            return "CONTROL STOPPED before click."
        win, ctrl = matches[0]
        try:
            ctrl.click_input()
        except Exception as exc:
            return f"UI element matched but click failed: {type(exc).__name__}: {exc}"
        return f"Clicked {ctrl.element_info.control_type} {ctrl.element_info.name!r} in window {win.window_text()!r}."
    except Exception as exc:
        return f"UI click failed: {type(exc).__name__}: {exc}"

'''
s = s.replace(marker, block + marker, 1)
p.write_text(s, encoding="utf-8")
print("Added semantic Windows UI inspection and uniquely matched element clicking.")
