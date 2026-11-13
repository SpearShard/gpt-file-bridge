from pathlib import Path

p = Path("gemini_mcp_bridge.py")
src = p.read_text(encoding="utf-8")
if "def desktop_screenshot(" in src:
    print("Desktop-control tools already present; no changes made.")
    raise SystemExit(0)

src = src.replace(
    "import subprocess\n",
    "import subprocess\nimport base64\nimport io\nimport time\n"
)
src = src.replace(
    "from typing import Any\n",
    "from typing import Any\n\nfrom mcp.types import ImageContent\n"
)

block = r'''

# ---------------------------------------------------------------------------
# Desktop mouse, keyboard and screenshot controls
# ---------------------------------------------------------------------------

import pyautogui

# Emergency stop: move the physical mouse to any screen corner.
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.15

@mcp.tool()
def desktop_screenshot() -> ImageContent:
    """Returns a screenshot of the current Windows desktop. May include private information."""
    shot = pyautogui.screenshot()
    buffer = io.BytesIO()
    shot.save(buffer, format="PNG")
    return ImageContent(type="image", data=base64.b64encode(buffer.getvalue()).decode("ascii"), mimeType="image/png")


@mcp.tool()
def desktop_mouse(action: str, x: int = 0, y: int = 0, button: str = "left", clicks: int = 1, amount: int = 0) -> str:
    """Controls the mouse: action is move, click, double_click, right_click, or scroll. Coordinates are screen pixels; scroll amount is positive up, negative down."""
    allowed = {"move", "click", "double_click", "right_click", "scroll"}
    if action not in allowed:
        return "Invalid action. Use move, click, double_click, right_click, or scroll."
    width, height = pyautogui.size()
    if action != "scroll" and not (0 <= x < width and 0 <= y < height):
        return f"Coordinates must be within screen bounds 0..{width-1}, 0..{height-1}."
    if button not in {"left", "right", "middle"}:
        return "Invalid mouse button."
    if action == "move":
        pyautogui.moveTo(x, y, duration=0.2)
    elif action == "click":
        pyautogui.click(x=x, y=y, button=button, clicks=max(1, min(clicks, 3)))
    elif action == "double_click":
        pyautogui.doubleClick(x=x, y=y, button=button)
    elif action == "right_click":
        pyautogui.rightClick(x=x, y=y)
    elif action == "scroll":
        pyautogui.moveTo(x, y, duration=0.1)
        pyautogui.scroll(max(-20, min(amount, 20)))
    return f"Mouse {action} completed at ({x}, {y})."


@mcp.tool()
def desktop_type_text(text: str, interval: float = 0.02) -> str:
    """Types text into the currently focused application. Does not submit or send it automatically."""
    if len(text) > 10000:
        return "Text too long; limit is 10,000 characters."
    pyautogui.write(text, interval=max(0, min(interval, 0.1)))
    return f"Typed {len(text)} characters."


@mcp.tool()
def desktop_press_key(key: str) -> str:
    """Presses one keyboard key, e.g. enter, esc, tab, space, up, f5."""
    allowed = {
        "enter", "esc", "tab", "space", "backspace", "delete", "home", "end",
        "pageup", "pagedown", "up", "down", "left", "right", "f1", "f2", "f3",
        "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"
    }
    key = key.lower().strip()
    if key not in allowed:
        return "Key not allowed. Use a standard navigation key, enter/esc/tab, or F1-F12."
    pyautogui.press(key)
    return f"Pressed {key}."


@mcp.tool()
def desktop_hotkey(keys: list[str]) -> str:
    """Sends a keyboard shortcut such as ['ctrl','s'], ['alt','tab'], or ['ctrl','shift','esc']."""
    allowed = {"ctrl", "alt", "shift", "win", "enter", "esc", "tab", "s", "c", "v", "x", "a", "z", "y", "w", "t", "r", "f", "d", "e", "n", "o", "p", "space"}
    normalized = [k.lower().strip() for k in keys]
    if not 2 <= len(normalized) <= 4 or any(k not in allowed for k in normalized):
        return "Shortcut rejected. Use 2-4 allowed keys, e.g. ['ctrl','s']."
    pyautogui.hotkey(*normalized)
    return "Pressed " + "+".join(normalized) + "."

'''
marker = '\nif __name__ == "__main__":'
if marker not in src:
    raise SystemExit("Could not find server startup marker; no changes made.")
src = src.replace(marker, block + marker)
p.write_text(src, encoding="utf-8")
print("Added desktop_screenshot, desktop_mouse, desktop_type_text, desktop_press_key and desktop_hotkey.")
