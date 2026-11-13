from pathlib import Path
import shutil

p = Path("gemini_mcp_bridge.py")
src = p.read_text(encoding="utf-8")
marker = "\n\n# ---------------------------------------------------------------------------\n# Windows UI Automation: inspect accessible controls by semantic properties."
if marker not in src:
    raise SystemExit("Insertion marker not found; no changes made.")
if "def desktop_execute_workflow(" in src:
    raise SystemExit("desktop_execute_workflow already exists; refusing duplicate patch.")

backup = p.with_name("gemini_mcp_bridge.py.before-desktop-workflow.bak")
if not backup.exists():
    shutil.copy2(p, backup)

block = r'''

@mcp.tool()
def desktop_execute_workflow(
    window_title: str,
    steps: list[dict[str, Any]],
    activate_window: bool = True,
    timeout_seconds: float = 12.0,
) -> str:
    """Runs a short, ordered desktop workflow in one MCP call.

    Supported steps:
      {"action":"hotkey","keys":["ctrl","shift","grave"]}
      {"action":"wait_focus","contains":"Terminal","timeout":10}
      {"action":"type_text","text":"...","expected_focus":"Terminal"}
      {"action":"press_key","key":"enter"}
    The target window is activated once. Typing is always guarded by a fresh
    foreground-window and focused-UIA-element check. Destructive actions are
    intentionally not supported by this workflow tool.
    """
    import time as _time
    import win32gui
    import win32con
    from pywinauto.uia_defines import IUIA

    if not window_title.strip():
        return "REFUSED: window_title is required."
    if not isinstance(steps, list) or not steps or len(steps) > 20:
        return "REFUSED: provide 1-20 workflow steps."
    if not _control_allowed():
        return "CONTROL STOPPED: resume desktop control before running a workflow."

    def foreground_title():
        hwnd = int(win32gui.GetForegroundWindow())
        return hwnd, (win32gui.GetWindowText(hwnd) or "").strip()

    def focus_description():
        el = IUIA().get_focused_element()
        parts = [
            (el.CurrentName or "").strip(),
            (el.CurrentAutomationId or "").strip(),
            (el.CurrentClassName or "").strip(),
            str(el.CurrentLocalizedControlType or "").strip(),
        ]
        return " | ".join(x for x in parts if x)

    try:
        desktop = _uia_desktop()
        matches = []
        needle = window_title.casefold()
        for win in desktop.windows():
            try:
                title = (win.window_text() or "").strip()
                if needle in title.casefold() and win.is_visible() and not win.is_minimized():
                    matches.append((win, title, int(win.handle)))
            except Exception:
                continue
        if len(matches) != 1:
            return (
                f"REFUSED: expected exactly one visible window containing {window_title!r}; "
                f"found {len(matches)}."
            )
        win, title, hwnd = matches[0]

        if activate_window:
            if not _control_allowed():
                return "CONTROL STOPPED before window activation."
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
            except Exception as exc:
                return f"FAILED activating {title!r}: {type(exc).__name__}: {exc}"

            deadline = _time.monotonic() + min(max(float(timeout_seconds), 1.0), 30.0)
            while _time.monotonic() < deadline:
                if not _control_allowed():
                    return "CONTROL STOPPED while waiting for target window."
                current_hwnd, current_title = foreground_title()
                if current_hwnd == hwnd and needle in current_title.casefold():
                    break
                _time.sleep(0.08)
            else:
                return f"REFUSED: could not bring {title!r} to the foreground."

        _ensure_overlay_started()
        results = []
        for index, step in enumerate(steps, start=1):
            if not _control_allowed():
                return "\n".join(results + [f"STOPPED before step {index}; no further actions sent."])
            if not isinstance(step, dict):
                return "\n".join(results + [f"FAILED step {index}: step must be an object."])

            action = str(step.get("action", "")).strip().lower()
            current_hwnd, current_title = foreground_title()
            if current_hwnd != hwnd or needle not in current_title.casefold():
                return "\n".join(results + [
                    f"REFUSED at step {index}: foreground changed to {current_title!r}."
                ])

            if action == "hotkey":
                keys = step.get("keys")
                if not isinstance(keys, list) or not 2 <= len(keys) <= 4:
                    return "\n".join(results + [f"FAILED step {index}: hotkey needs 2-4 keys."])
                normalized = [str(k).lower().strip() for k in keys]
                allowed = {"ctrl", "alt", "shift", "win", "enter", "esc", "tab", "s", "c", "v", "x", "a", "z", "y", "w", "t", "r", "f", "d", "e", "n", "o", "p", "space", "grave"}
                if any(k not in allowed for k in normalized):
                    return "\n".join(results + [f"FAILED step {index}: unsupported hotkey."])
                pyautogui.hotkey(*normalized)
                _sync_input_baseline()
                results.append(f"Step {index}: sent {'+'.join(normalized)}.")

            elif action == "wait_focus":
                expected = str(step.get("contains", "")).strip()
                if not expected:
                    return "\n".join(results + [f"FAILED step {index}: wait_focus needs contains."])
                wait_for = min(max(float(step.get("timeout", 8.0)), 0.2), 20.0)
                deadline = _time.monotonic() + wait_for
                last_focus = ""
                while _time.monotonic() < deadline:
                    if not _control_allowed():
                        return "\n".join(results + [f"STOPPED while waiting at step {index}."])
                    current_hwnd, current_title = foreground_title()
                    if current_hwnd != hwnd or needle not in current_title.casefold():
                        return "\n".join(results + [f"REFUSED at step {index}: foreground changed."])
                    try:
                        last_focus = focus_description()
                        if expected.casefold() in last_focus.casefold():
                            break
                    except Exception:
                        pass
                    _time.sleep(0.08)
                else:
                    return "\n".join(results + [
                        f"FAILED step {index}: focus did not contain {expected!r}; last focus={last_focus!r}."
                    ])
                results.append(f"Step {index}: verified focus {last_focus!r}.")

            elif action == "type_text":
                text_value = str(step.get("text", ""))
                expected = str(step.get("expected_focus", "")).strip()
                if not expected or not text_value or len(text_value) > 10000:
                    return "\n".join(results + [
                        f"REFUSED step {index}: type_text needs non-empty text and expected_focus (max 10,000 chars)."
                    ])
                focus = focus_description()
                current_hwnd, current_title = foreground_title()
                if current_hwnd != hwnd or needle not in current_title.casefold():
                    return "\n".join(results + [f"REFUSED at step {index}: foreground changed before typing."])
                if expected.casefold() not in focus.casefold():
                    return "\n".join(results + [
                        f"REFUSED step {index}: focused element {focus!r} did not match {expected!r}."
                    ])
                if not _control_allowed():
                    return "\n".join(results + [f"STOPPED before typing at step {index}."])
                pyautogui.write(text_value, interval=0)
                _sync_input_baseline()
                results.append(f"Step {index}: typed {len(text_value)} characters into verified focus.")

            elif action == "press_key":
                key = str(step.get("key", "")).lower().strip()
                allowed = {"enter", "esc", "tab", "space", "backspace", "delete", "home", "end", "pageup", "pagedown", "up", "down", "left", "right", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"}
                if key not in allowed:
                    return "\n".join(results + [f"FAILED step {index}: key {key!r} is not allowed."])
                if not _control_allowed():
                    return "\n".join(results + [f"STOPPED before keypress at step {index}."])
                pyautogui.press(key)
                _sync_input_baseline()
                results.append(f"Step {index}: pressed {key}.")

            else:
                return "\n".join(results + [
                    f"FAILED step {index}: unsupported action {action!r}. "
                    "Supported: hotkey, wait_focus, type_text, press_key."
                ])

        return f"WORKFLOW COMPLETED in {len(steps)} steps for {title!r}.\n" + "\n".join(results)
    except Exception as exc:
        return f"WORKFLOW FAILED: {type(exc).__name__}: {exc}"
'''
src = src.replace(marker, block + marker, 1)
p.write_text(src, encoding="utf-8")
print("Added desktop_execute_workflow.")
print(f"Backup created: {backup.name}")
