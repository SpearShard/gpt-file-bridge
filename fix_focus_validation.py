from pathlib import Path
p = Path("gemini_mcp_bridge.py")
s = p.read_text(encoding="utf-8")
old = """        desktop = Desktop(backend="uia")
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
"""
new = """        from pywinauto.uia_defines import IUIA
        focused = IUIA().get_focused_element()
        focus_name = (focused.CurrentName or "").strip()
        focus_type = str(focused.CurrentLocalizedControlType or "").strip()
        focus_automation_id = (focused.CurrentAutomationId or "").strip()
        focus_class = (focused.CurrentClassName or "").strip()
        expected = expected_focus_name.casefold()
        focus_description = " | ".join(x for x in (focus_name, focus_automation_id, focus_class, focus_type) if x)
        if expected not in focus_description.casefold():
            return (
                f"REFUSED: focused UIA element is {focus_description!r}; "
                f"expected focus containing {expected_focus_name!r}."
            )
"""
if old not in s:
    raise SystemExit("Old focus validation block not found; no changes made.")
s = s.replace(old, new, 1)
s = s.replace(
    'return f"Typed {typed} characters into verified {focus_type} {focus_name!r} in {actual_title!r}."',
    'return f"Typed {typed} characters into verified focus {focus_description!r} in {actual_title!r}."',
    1
)
p.write_text(s, encoding="utf-8")
print("Updated focus verification to use Windows UI Automation focused-element API.")
