from pathlib import Path
p = Path("gemini_mcp_bridge.py")
s = p.read_text(encoding="utf-8")
needle = '            elif action == "wait_focus":\n'
insert = '''            elif action == "wait_terminal_panel":
                # Panel presence is separate from keyboard focus.
                wait_for = min(max(float(step.get("timeout", 8.0)), 0.2), 20.0)
                deadline = _time.monotonic() + wait_for
                found_tab = False
                found_input = False
                while _time.monotonic() < deadline:
                    if not _control_allowed():
                        return "\\n".join(results + [f"STOPPED while waiting at step {index}."])
                    current_hwnd, current_title = foreground_title()
                    if current_hwnd != hwnd or needle not in current_title.casefold():
                        return "\\n".join(results + [f"REFUSED at step {index}: foreground changed."])
                    try:
                        target = _uia_desktop().window(handle=hwnd)
                        for control in target.descendants():
                            try:
                                if not control.is_visible():
                                    continue
                                name = (control.window_text() or "").casefold()
                                kind = str(control.element_info.control_type or "").casefold()
                                if "terminal" in name and kind in ("tabitem", "tab"):
                                    found_tab = True
                                if "terminal" in name and kind == "edit":
                                    found_input = True
                            except Exception:
                                continue
                        if found_tab and found_input:
                            break
                    except Exception:
                        pass
                    _time.sleep(0.15)
                if not (found_tab and found_input):
                    return "\\n".join(results + [
                        f"FAILED step {index}: terminal panel not verified (tab={found_tab}, input={found_input})."
                    ])
                results.append(f"Step {index}: verified integrated terminal panel and terminal Edit control; keyboard focus NOT assumed.")

'''
if needle not in s:
    raise SystemExit("Insertion point not found")
backup = p.with_name("gemini_mcp_bridge.py.before-terminal-panel-detection.bak")
backup.write_text(s, encoding="utf-8")
s = s.replace(needle, insert + needle, 1)
s = s.replace("Supported steps: hotkey, wait_focus, type_text, press_key.",
              "Supported steps: hotkey, wait_terminal_panel, wait_focus, type_text, press_key.", 1)
s = s.replace("Supported: hotkey, wait_focus, type_text, press_key.",
              "Supported: hotkey, wait_terminal_panel, wait_focus, type_text, press_key.", 1)
p.write_text(s, encoding="utf-8")
print("Patched; backup:", backup.name)
