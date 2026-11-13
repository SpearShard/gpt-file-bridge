from pathlib import Path
p = Path("gemini_mcp_bridge.py")
s = p.read_text(encoding="utf-8")
needle = '''    def focus_description():
        el = IUIA().get_focused_element()
        parts = [
            (el.CurrentName or "").strip(),
            (el.CurrentAutomationId or "").strip(),
            (el.CurrentClassName or "").strip(),
            str(el.CurrentLocalizedControlType or "").strip(),
        ]
        return " | ".join(x for x in parts if x)

'''
addition = '''    def terminal_input_has_focus():
        # Chromium/Electron may expose the terminal input separately from the
        # global focused-element result. Check the actual UIA focus property.
        target = _uia_desktop().window(handle=hwnd)
        for control in target.descendants():
            try:
                if not control.is_visible():
                    continue
                if str(control.element_info.control_type or "").casefold() != "edit":
                    continue
                name = (control.window_text() or "").strip().casefold()
                if name == "terminal input":
                    return bool(control.element_info.element.CurrentHasKeyboardFocus)
            except Exception:
                continue
        return False

'''
if needle not in s:
    raise SystemExit("focus_description insertion point not found")
if "def terminal_input_has_focus():" not in s:
    s = s.replace(needle, needle + addition, 1)
needle2 = '''            elif action == "wait_focus":
'''
action = '''            elif action == "wait_terminal_focus":
                wait_for = min(max(float(step.get("timeout", 8.0)), 0.2), 20.0)
                deadline = _time.monotonic() + wait_for
                has_focus = False
                while _time.monotonic() < deadline:
                    if not _control_allowed():
                        return "\\n".join(results + [f"STOPPED while waiting at step {index}."])
                    current_hwnd, current_title = foreground_title()
                    if current_hwnd != hwnd or needle not in current_title.casefold():
                        return "\\n".join(results + [f"REFUSED at step {index}: foreground changed."])
                    try:
                        has_focus = terminal_input_has_focus()
                    except Exception:
                        has_focus = False
                    if has_focus:
                        break
                    _time.sleep(0.1)
                if not has_focus:
                    return "\\n".join(results + [
                        f"FAILED step {index}: VS Code terminal input did not report keyboard focus."
                    ])
                results.append(f"Step {index}: verified keyboard focus on VS Code Terminal input.")

'''
if needle2 not in s:
    raise SystemExit("wait_focus insertion point not found")
if "elif action == \"wait_terminal_focus\":" not in s:
    s = s.replace(needle2, action + needle2, 1)
old = '''                if expected.casefold() not in focus.casefold():
                    return "\\n".join(results + [
                        f"REFUSED step {index}: focused element {focus!r} did not match {expected!r}."
                    ])
'''
new = '''                terminal_expected = expected.casefold() in ("terminal", "terminal input")
                focus_matches = terminal_input_has_focus() if terminal_expected else expected.casefold() in focus.casefold()
                if not focus_matches:
                    return "\\n".join(results + [
                        f"REFUSED step {index}: terminal keyboard focus was not verified; focused element={focus!r}."
                    ])
'''
if old not in s:
    raise SystemExit("type_text focus guard not found")
if "terminal_expected = expected.casefold()" not in s:
    s = s.replace(old, new, 1)
s = s.replace("Supported steps: hotkey, wait_terminal_panel, wait_focus, type_text, press_key.",
              "Supported steps: hotkey, wait_terminal_panel, wait_terminal_focus, wait_focus, type_text, press_key.", 1)
s = s.replace("Supported: hotkey, wait_terminal_panel, wait_focus, type_text, press_key.",
              "Supported: hotkey, wait_terminal_panel, wait_terminal_focus, wait_focus, type_text, press_key.", 1)
backup = p.with_name("gemini_mcp_bridge.py.before-terminal-focus-check.bak")
backup.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
p.write_text(s, encoding="utf-8")
print("Added terminal focus detection; backup:", backup.name)
