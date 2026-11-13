from pathlib import Path

path = Path("gemini_mcp_bridge.py")
source = path.read_text(encoding="utf-8")

old = '''    _ensure_overlay_started()
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
    return f"Typed {typed} characters into verified focus {focus_description!r} in {actual_title!r}."
'''

new = '''    _ensure_overlay_started()
    # Recheck immediately before sending the text as one batch. The previous
    # character-by-character loop paid PyAutoGUI's global PAUSE on every
    # character and added an artificial sleep per character.
    if not _control_allowed():
        return "CONTROL STOPPED before typing."
    pyautogui.write(text, interval=0)
    _sync_input_baseline()
    return f"Typed {len(text)} characters in one batch into verified focus {focus_description!r} in {actual_title!r}."
'''

if old not in source:
    raise SystemExit("PATCH ABORTED: expected typing loop not found; no changes made.")

backup = path.with_name("gemini_mcp_bridge.py.before-batched-typing.bak")
if not backup.exists():
    backup.write_text(source, encoding="utf-8")

path.write_text(source.replace(old, new, 1), encoding="utf-8")
print("Patched desktop_type_text to batch text input.")
print(f"Backup: {backup.name}")
