from pathlib import Path

p = Path("gemini_mcp_bridge.py")
s = p.read_text(encoding="utf-8")

s = s.replace("from mcp.types import ImageContent", "from mcp.types import ImageContent, TextContent", 1)

anchor = """@mcp.tool()
def desktop_click_ui_element(window_title: str, name: str, control_type: str = "", automation_id: str = "") -> str:
"""
if anchor not in s:
    raise SystemExit("Could not find insertion point; no changes made.")

new_tool = '''@mcp.tool()
def desktop_visual_snapshot(max_elements: int = 100) -> list:
    """Returns one desktop screenshot together with UI Automation metadata for the foreground window. Read-only; performs no input."""
    try:
        # Capture the pixels first, then collect accessibility metadata without
        # changing focus or interacting with the desktop.
        shot = pyautogui.screenshot()
        buffer = io.BytesIO()
        shot.save(buffer, format="PNG")
        screenshot_content = ImageContent(
            type="image",
            data=base64.b64encode(buffer.getvalue()).decode("ascii"),
            mimeType="image/png",
        )
        ui_text = desktop_inspect_ui(window_title="", max_elements=max_elements)
        metadata = TextContent(
            type="text",
            text=(
                f"DESKTOP VISUAL SNAPSHOT\\n"
                f"Screen size: {shot.width}x{shot.height}\\n"
                f"UI metadata is limited to the foreground window.\\n"
                f"No mouse or keyboard input was sent.\\n\\n"
                f"{ui_text}"
            ),
        )
        return [metadata, screenshot_content]
    except Exception as exc:
        return [TextContent(type="text", text=f"Visual snapshot failed: {type(exc).__name__}: {exc}")]

'''
s = s.replace(anchor, new_tool + anchor, 1)
p.write_text(s, encoding="utf-8")
print("Added desktop_visual_snapshot to the existing MCP server.")
