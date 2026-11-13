from pathlib import Path

p = Path("gemini_mcp_bridge.py")
src = p.read_text(encoding="utf-8")

start = src.index("@mcp.tool()\ndef desktop_type_text(")
end = src.index("@mcp.tool()\ndef desktop_press_key(", start)

replacement = r'''@mcp.tool()
def desktop_type_text(expected_window_title: str, expected_focus_name: str, text: str, interval: float = 0.02) -> str:
    """Types only after verifying the foreground window title and focused UI control name. Never types if either check is missing or mismatched."""
    if not expected_window_title.strip() or not expected_focus_name.strip():
        return "REFUSED: provide both expected_window_title and expected_focus_name so the target can be verified."
    if len(text) > 10000:
        return "Text too long; limit is 10,000 characters."
    if not _control_allowed():
        return "CONTROL STOPPED: no text was typed. Explicitly resume desktop control to continue."

    try:
        import win32gui
        from pywinauto import Desktop

        foreground = int(win32gui.GetForegroundWindow())
        actual_title = (win32gui.GetWindowText(foreground) or "").strip()
        if expected_window_title.casefold() not in actual_title.casefold():
            return f"REFUSED: foreground window is {actual_title!r}, not the expected {expected_window_title!r}."

        desktop = Desktop(backend="uia")
        focused = desktop.get_focus()
        info = focused.element_info
        focus_name = (info.name or "").strip()
        focus_type = (info.control_type or "").strip()
        expected = expected_focus_name.casefold()
        if expected not in focus_name.casefold() and expected not in focus_type.casefold():
            return (
                f"REFUSED: focused control is {focus_type} {focus_name!r}; "
                f"expected focus containing {expected_focus_name!r}."
            )
    except Exception as exc:
        return f"REFUSED: could not verify the focused control ({type(exc).__name__}: {exc})."

    if not _control_allowed():
        return "CONTROL STOPPED before typing."
    _ensure_overlay_started()
    typed = 0
    delay = max(0, min(interval, 0.1))
    for character in text:
        if not _control_allowed():
            return f"CONTROL STOPPED after {typed}/{len(text)} characters. Remaining text was not typed."
        pyautogui.write(character, interval=0)
        typed += 1
        _sync_input_baseline()
        if delay:
            time.sleep(delay)
    return f"Typed {typed} characters into verified {focus_type} {focus_name!r} in {actual_title!r}."


'''
src = src[:start] + replacement + src[end:]

# Include virtual-desktop origin and dimensions alongside the screenshot in visual snapshots.
old = '''        ui_text = desktop_inspect_ui(window_title="", max_elements=max_elements)
        metadata = TextContent(
            type="text",
            text=(
                f"DESKTOP VISUAL SNAPSHOT\\n"
                f"Screen size: {shot.width}x{shot.height}\\n"
                f"UI metadata is limited to the foreground window.\\n"'''
new = '''        ui_text = desktop_inspect_ui(window_title="", max_elements=max_elements)
        import win32api, win32con
        virtual_bounds = (
            win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN),
            win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN),
            win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN),
            win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN),
        )
        metadata = TextContent(
            type="text",
            text=(
                f"DESKTOP VISUAL SNAPSHOT\\\\n"
                f"Screenshot size: {shot.width}x{shot.height}\\\\n"
                f"Virtual desktop bounds (left, top, width, height): {virtual_bounds}\\\\n"
                f"Screenshot origin corresponds to the virtual desktop's top-left.\\\\n"
                f"UI metadata is limited to the foreground window.\\\\n"'''
if old not in src:
    raise SystemExit("Could not find visual snapshot metadata block; no changes made.")
src = src.replace(old, new, 1)

p.write_text(src, encoding="utf-8")
print("Added verified typing and virtual-desktop origin metadata.")
