from pathlib import Path

p = Path("gemini_mcp_bridge.py")
s = p.read_text(encoding="utf-8")

anchor = '''@mcp.tool()
def desktop_click_ui_element(window_title: str, name: str, control_type: str = "", automation_id: str = "") -> str:
'''
if anchor not in s:
    raise SystemExit("Could not find insertion point; no changes made.")

tool = '''@mcp.tool()
def desktop_find_target(query: str, max_results: int = 8) -> str:
    """Ranks visible controls in the foreground window against a natural-language target. Read-only; confidence is semantic-label similarity, not visual certainty."""
    import difflib
    import re
    import win32gui
    import pyautogui

    query = (query or "").strip()
    if not query:
        return "Enter a target description, e.g. 'Reload button' or 'Address and search bar'."

    try:
        screen_w, screen_h = pyautogui.size()
        foreground = int(win32gui.GetForegroundWindow())
        desktop = _uia_desktop()
        candidates = []

        def normalize(value):
            return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()

        q_norm = normalize(query)
        q_tokens = set(q_norm.split())
        for win in desktop.windows():
            try:
                handle = int(win.handle)
                if handle != foreground or not win32gui.IsWindowVisible(handle) or win32gui.IsIconic(handle):
                    continue
                title = win.window_text() or ""
                for ctrl in win.descendants():
                    try:
                        info = ctrl.element_info
                        name = (info.name or "").strip()
                        aid = (info.automation_id or "").strip()
                        ctype = (info.control_type or "Unknown").strip()
                        if not name and not aid:
                            continue
                        if not ctrl.is_visible():
                            continue
                        try:
                            enabled = bool(ctrl.is_enabled())
                        except Exception:
                            enabled = False
                        r = ctrl.rectangle()
                        left, top, right, bottom = r.left, r.top, r.right, r.bottom
                        if right <= left or bottom <= top or right <= 0 or bottom <= 0:
                            continue
                        if left >= screen_w or top >= screen_h or left < -screen_w or top < -screen_h:
                            continue

                        label = normalize(name)
                        if not label:
                            continue
                        ratio = difflib.SequenceMatcher(None, q_norm, label).ratio()
                        label_tokens = set(label.split())
                        overlap = len(q_tokens & label_tokens) / max(1, len(q_tokens))
                        if q_norm == label:
                            score = 1.0
                        elif q_norm in label or label in q_norm:
                            score = max(ratio, 0.90)
                        else:
                            score = 0.72 * ratio + 0.28 * overlap

                        candidates.append({
                            "score": round(score, 3),
                            "name": name,
                            "control_type": ctype,
                            "automation_id": aid,
                            "enabled": enabled,
                            "window": title,
                            "rect": (left, top, right, bottom),
                            "center": (max(0, min(screen_w - 1, (left + right) // 2)),
                                       max(0, min(screen_h - 1, (top + bottom) // 2))),
                        })
                    except Exception:
                        continue
            except Exception:
                continue

        candidates.sort(key=lambda item: item["score"], reverse=True)
        limit = max(1, min(int(max_results), 20))
        top = candidates[:limit]
        if not top:
            return "No visible named controls found in the foreground window."
        best = top[0]["score"]
        second = top[1]["score"] if len(top) > 1 else 0.0
        if best >= 0.92 and best - second >= 0.12:
            verdict = "HIGH semantic match; still verify the screenshot before clicking."
        elif best >= 0.78:
            verdict = "POSSIBLE match; ambiguous or not strong enough for automatic clicking."
        else:
            verdict = "LOW match; do not click."
        lines = [
            f"Target query: {query!r}",
            f"Assessment: {verdict}",
            f"Best score: {best:.3f} | margin over runner-up: {best-second:.3f}",
            "Scores measure accessible-label similarity only, not visual recognition."
        ]
        for index, item in enumerate(top, 1):
            lines.append(
                f"{index}. score={item['score']:.3f} | {item['control_type']} "
                f"| name={item['name']!r} | automation_id={item['automation_id']!r} "
                f"| enabled={item['enabled']} | center={item['center']} "
                f"| rect={item['rect']} | window={item['window']!r}"
            )
        return "\\n".join(lines)
    except Exception as exc:
        return f"Target matching failed: {type(exc).__name__}: {exc}"

'''
s = s.replace(anchor, tool + anchor, 1)
p.write_text(s, encoding="utf-8")
print("Added read-only semantic target matcher with confidence ranking.")
