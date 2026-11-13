import hashlib
import os
import sqlite3
import subprocess
import base64
import io
import time

# Use physical virtual-desktop coordinates consistently across mixed-DPI monitors.
# This must run before pyautogui, tkinter, or other GUI libraries are imported.
try:
    import ctypes as _dpi_ctypes
    _dpi_user32 = _dpi_ctypes.WinDLL("user32", use_last_error=True)
    _set_dpi_context = getattr(_dpi_user32, "SetProcessDpiAwarenessContext", None)
    if _set_dpi_context is not None:
        _set_dpi_context(_dpi_ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    else:
        _dpi_ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

from collections import Counter
from pathlib import Path

from typing import Any

from mcp.types import ImageContent, TextContent

import jwt
from jwt import PyJWKClient
from pydantic import AnyHttpUrl

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer


# ---------------------------------------------------------------------------
# MadeEasy OAuth resource-server configuration
# ---------------------------------------------------------------------------

SUPABASE_PROJECT_URL = "https://mowshbvvrrbgtapvqoar.supabase.co"
SUPABASE_ISSUER = f"{SUPABASE_PROJECT_URL}/auth/v1"
SUPABASE_JWKS_URL = f"{SUPABASE_ISSUER}/.well-known/jwks.json"

# Current Cloudflare Quick Tunnel URL supplied by Jay.
# Quick Tunnel URLs change when cloudflared is restarted.
PUBLIC_MCP_URL = (
    "https://tell-consequence-investigator-postcard.trycloudflare.com/mcp"
)

# Supabase OAuth access tokens use aud="authenticated" by default.
EXPECTED_AUDIENCE = "authenticated"


class SupabaseTokenVerifier(TokenVerifier):
    """Verify Supabase ES256 access tokens against the project's public JWKS."""

    def __init__(self) -> None:
        self._jwks = PyJWKClient(
            SUPABASE_JWKS_URL,
            cache_jwk_set=True,
            lifespan=300,
            timeout=10,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return an MCP AccessToken only when the Supabase JWT is valid."""
        import asyncio

        try:
            claims = await asyncio.to_thread(self._verify_sync, token)

            subject = claims.get("sub")
            client_id = claims.get("client_id")
            expires_at = claims.get("exp")

            if (
                not isinstance(subject, str)
                or not subject
                or not isinstance(client_id, str)
                or not client_id
                or not isinstance(expires_at, (int, float))
                or claims.get("role") != "authenticated"
            ):
                return None

            raw_scopes = claims.get("scope", "")
            if isinstance(raw_scopes, str):
                scopes = raw_scopes.split()
            elif isinstance(raw_scopes, list):
                scopes = [str(scope) for scope in raw_scopes]
            else:
                scopes = []

            return AccessToken(
                token=token,
                client_id=client_id,
                scopes=scopes,
                expires_at=int(expires_at),
                resource=None,
                subject=subject,
                claims=claims,
            )
        except Exception:
            # Never log or return the token itself.
            return None

    def _verify_sync(self, token: str) -> dict[str, Any]:
        signing_key = self._jwks.get_signing_key_from_jwt(token)

        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            issuer=SUPABASE_ISSUER,
            audience=EXPECTED_AUDIENCE,
            leeway=30,
            options={
                "require": ["iss", "aud", "exp", "iat", "sub"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )

        if claims.get("role") != "authenticated":
            raise jwt.InvalidTokenError("Token is not for an authenticated user")

        if not claims.get("client_id"):
            raise jwt.InvalidTokenError("Token is missing its OAuth client_id")

        return claims


mcp = MCPServer(
    "MadeEasy",
    token_verifier=SupabaseTokenVerifier(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(SUPABASE_ISSUER),
        resource_server_url=AnyHttpUrl(PUBLIC_MCP_URL),
        required_scopes=[],
        # Supabase's default token audience is "authenticated", not the MCP URL.
        # The verifier validates that audience explicitly above.
        validate_token_resource=False,
    ),
)


BASE_DIR = os.path.realpath(os.path.expanduser("~/Desktop"))
os.makedirs(BASE_DIR, exist_ok=True)

INDEX_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ChatGPTProjectIndex")
os.makedirs(INDEX_DIR, exist_ok=True)

IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".dart_tool", ".idea", ".vscode",
    "node_modules", ".next", "dist", "build", "coverage",
    ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    "Pods", "DerivedData", "vendor", "target", ".gradle"
}
CODE_EXTENSIONS = {
    ".dart", ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".html", ".css", ".scss", ".sass", ".json", ".yaml", ".yml",
    ".md", ".mdx", ".sql", ".java", ".kt", ".swift", ".go", ".rs",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".php", ".rb", ".vue",
    ".svelte", ".sh", ".toml", ".xml", ".example"
}
MAX_FILE_BYTES = 1_000_000


def safe_path(rel_path: str) -> str:
    """Resolve a Desktop-relative path and reject traversal, including symlinks."""
    raw = str(rel_path or "").lstrip("/\\")
    target = os.path.realpath(os.path.join(BASE_DIR, raw))
    try:
        if os.path.commonpath([BASE_DIR, target]) != BASE_DIR:
            raise ValueError("Access denied: path outside Desktop workspace")
    except ValueError:
        raise ValueError("Access denied: path outside Desktop workspace")
    return target


def project_db_path(project_path: str) -> str:
    digest = hashlib.sha256(os.path.normcase(project_path).encode("utf-8")).hexdigest()[:24]
    return os.path.join(INDEX_DIR, digest + ".sqlite3")


def connect_index(project_path: str):
    conn = sqlite3.connect(project_db_path(project_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            extension TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            content TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_extension ON files(extension)")
    return conn


@mcp.tool()
def list_files(subfolder: str = "") -> str:
    """Lists directory contents using fast native filesystem reads."""
    target_dir = safe_path(subfolder)
    if not os.path.exists(target_dir):
        return f"Dir '{subfolder}' not found."
    if not os.path.isdir(target_dir):
        return f"'{subfolder}' is not a directory."
    entries = []
    for item in os.listdir(target_dir)[:60]:
        full_path = os.path.join(target_dir, item)
        kind = "DIR" if os.path.isdir(full_path) else "FILE"
        entries.append(f"[{kind}] {item}")
    return "\n".join(entries) if entries else "Empty directory."


@mcp.tool()
def read_file(path: str, max_chars: int = 8000) -> str:
    """Reads a UTF-8 text file under the Desktop workspace."""
    target = safe_path(path)
    if not os.path.isfile(target):
        return f"File '{path}' not found."
    with open(target, "r", encoding="utf-8", errors="replace") as f:
        return f.read(max(1, min(max_chars, 50000)))


@mcp.tool()
def write_file(path: str, content: str, mode: str = "overwrite") -> str:
    """Writes a text file under the Desktop workspace. mode is overwrite or append."""
    if mode not in {"overwrite", "append"}:
        raise ValueError("mode must be 'overwrite' or 'append'")
    target = safe_path(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "a" if mode == "append" else "w", encoding="utf-8") as f:
        f.write(content)
    return f"Saved {path}."


@mcp.tool()
def index_project(project_path: str) -> str:
    """Indexes source files in a Desktop-relative project. Skips dependencies/build outputs and updates only changed files."""
    root = safe_path(project_path)
    if not os.path.isdir(root):
        return f"Project folder not found: {project_path}"

    conn = connect_index(root)
    seen = set()
    scanned = added = updated = unchanged = skipped = 0

    for current, dirs, filenames in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and (not d.startswith(".") or d == ".github")]
        for filename in filenames:
            full_path = os.path.join(current, filename)
            extension = Path(filename).suffix.lower()
            if extension not in CODE_EXTENSIONS:
                skipped += 1
                continue
            try:
                stat = os.stat(full_path)
                if stat.st_size > MAX_FILE_BYTES:
                    skipped += 1
                    continue
                relative = os.path.relpath(full_path, root).replace(os.sep, "/")
                seen.add(relative)
                row = conn.execute(
                    "SELECT size_bytes, mtime_ns FROM files WHERE path = ?", (relative,)
                ).fetchone()
                scanned += 1
                if row and row[0] == stat.st_size and row[1] == stat.st_mtime_ns:
                    unchanged += 1
                    continue
                with open(full_path, "rb") as source:
                    raw = source.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES:
                    skipped += 1
                    continue
                text = raw.decode("utf-8", errors="replace")
                digest = hashlib.sha256(raw).hexdigest()
                conn.execute("""
                    INSERT INTO files(path, extension, size_bytes, mtime_ns, sha256, content)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                      extension=excluded.extension,
                      size_bytes=excluded.size_bytes,
                      mtime_ns=excluded.mtime_ns,
                      sha256=excluded.sha256,
                      content=excluded.content
                """, (relative, extension, stat.st_size, stat.st_mtime_ns, digest, text))
                if row:
                    updated += 1
                else:
                    added += 1
            except (OSError, UnicodeError):
                skipped += 1

    existing = {row[0] for row in conn.execute("SELECT path FROM files")}
    removed = len(existing - seen)
    if removed:
        conn.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in existing - seen])
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    conn.close()
    return (
        f"Indexed project: {project_path}\n"
        f"Files checked: {scanned} | added: {added} | updated: {updated} | "
        f"unchanged: {unchanged} | removed: {removed} | skipped: {skipped}\n"
        f"Total indexed files: {total}\n"
        "Index stored locally in your Windows user data folder."
    )


@mcp.tool()
def search_code(project_path: str, query: str, limit: int = 20) -> str:
    """Searches indexed project source and returns matching file paths, line numbers and short snippets."""
    root = safe_path(project_path)
    if not os.path.isdir(root):
        return f"Project folder not found: {project_path}"
    db_path = project_db_path(root)
    if not os.path.isfile(db_path):
        return "Project is not indexed yet. Run index_project(project_path) first."
    needle = (query or "").strip()
    if not needle:
        return "Enter a non-empty search query."
    limit = max(1, min(int(limit), 50))
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT path, content FROM files WHERE instr(lower(content), lower(?)) > 0 LIMIT 500",
        (needle,)
    ).fetchall()
    conn.close()

    matches = []
    for path, content in rows:
        for line_number, line in enumerate(content.splitlines(), start=1):
            if needle.casefold() in line.casefold():
                matches.append(f"{path}:{line_number}: {line.strip()[:240]}")
                if len(matches) >= limit:
                    break
        if len(matches) >= limit:
            break
    if not matches:
        return f"No matches for '{needle}' in indexed project '{project_path}'."
    return f"Found {len(matches)} matching lines (showing up to {limit}):\n" + "\n".join(matches)


@mcp.tool()
def project_map(project_path: str, max_entries: int = 160) -> str:
    """Returns a compact project tree and file-type summary from the local index."""
    root = safe_path(project_path)
    if not os.path.isdir(root):
        return f"Project folder not found: {project_path}"
    db_path = project_db_path(root)
    if not os.path.isfile(db_path):
        return "Project is not indexed yet. Run index_project(project_path) first."
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT path, extension FROM files ORDER BY path").fetchall()
    conn.close()
    counts = Counter(ext or "[no extension]" for _, ext in rows)
    max_entries = max(20, min(int(max_entries), 300))
    tree = [path for path, _ in rows[:max_entries]]
    summary = ", ".join(f"{ext}: {count}" for ext, count in counts.most_common(12))
    suffix = f"\n... {len(rows) - max_entries} more indexed files" if len(rows) > max_entries else ""
    return (
        f"Project: {project_path}\nIndexed source files: {len(rows)}\n"
        f"File types: {summary or 'none'}\n\n"
        + "\n".join(tree) + suffix
    )


@mcp.tool()
def run_command(command: str, subfolder: str = "") -> str:
    """Runs a short development command inside the Desktop workspace; use a project subfolder."""
    work_dir = safe_path(subfolder) if subfolder else BASE_DIR
    try:
        proc = subprocess.run(
            command, cwd=work_dir, shell=True, capture_output=True,
            text=True, timeout=25
        )
        combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        trimmed = combined[-4000:] if len(combined) > 4000 else combined
        return f"[Exit {proc.returncode}]\n{trimmed}" if trimmed else f"[Exit {proc.returncode}] OK."
    except subprocess.TimeoutExpired:
        return "Timed out after 25s. Use run_background_command for apps/builds."
    except Exception as e:
        return f"Failed: {str(e)}"


@mcp.tool()
def run_background_command(command: str, subfolder: str = "") -> str:
    """Starts a development command in the background inside the Desktop workspace."""
    work_dir = safe_path(subfolder) if subfolder else BASE_DIR
    try:
        DETACHED_PROCESS = 0x00000008
        proc = subprocess.Popen(
            command, cwd=work_dir, shell=True,
            creationflags=DETACHED_PROCESS, close_fds=True
        )
        return f"Started in background (PID: {proc.pid})."
    except Exception as e:
        return f"Spawn failed: {str(e)}"


@mcp.tool()
def run_blender_script(script_name: str) -> str:
    """Executes a Blender Python script stored inside the Desktop workspace."""
    script_path = safe_path(script_name)
    if not os.path.isfile(script_path):
        return f"Script not found: {script_name}"
    work_dir = os.path.dirname(script_path)
    cmd = f'blender -b -P "{script_path}"'
    try:
        res = subprocess.run(cmd, cwd=work_dir, shell=True, capture_output=True, text=True, timeout=60)
        combined = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
        trimmed = combined[-2000:] if len(combined) > 2000 else combined
        return f"[Blender Exit {res.returncode}]\n{trimmed}" if trimmed else "Blender script completed."
    except Exception as e:
        return f"Blender error: {str(e)}"



# ---------------------------------------------------------------------------
# Desktop mouse, keyboard and screenshot controls
# ---------------------------------------------------------------------------

import pyautogui

# Emergency stop: move the physical mouse to any screen corner.

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.15

# On-demand desktop-control overlay + global ESC emergency stop (Windows).
# The overlay is hidden until a desktop input action is requested. ESC or Stop
# pauses automation and hides the overlay so the user immediately regains control.
import ctypes
from ctypes import wintypes
import threading
import tkinter as tk

_control_lock = threading.RLock()
_control_paused = False
_overlay_root = None
_overlay_status = None
_overlay_button = None
_overlay_thread = None
_hook_thread = None
_hook_handle = None
_hook_callback = None
_overlay_ready = threading.Event()

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
VK_ESCAPE = 0x1B
HC_ACTION = 0
LRESULT = ctypes.c_ssize_t
_hook_proc_type = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

# Explicit Windows API signatures are essential on 64-bit Python.
# Without these, ctypes may treat pointer-sized LPARAM/HHOOK values as 32-bit ints.
_user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
]
_user32.CallNextHookEx.restype = LRESULT
_user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, _hook_proc_type, ctypes.c_void_p, wintypes.DWORD
]
_user32.SetWindowsHookExW.restype = ctypes.c_void_p

def _control_allowed() -> bool:
    with _control_lock:
        return not _control_paused

def _sync_input_baseline() -> None:
    return None

def _refresh_overlay() -> None:
    if _overlay_status is None or _overlay_button is None:
        return
    active = _control_allowed()
    _overlay_status.config(
        text="DESKTOP CONTROL  •  ACTIVE" if active else "DESKTOP CONTROL  •  STOPPED",
        fg="#16a34a" if active else "#f87171",
    )
    _overlay_button.config(
        text="⎋  PRESS ESC TO STOP" if active else "CONTROL STOPPED",
        state="normal" if active else "disabled",
        bg="#dc2626" if active else "#374151",
        activebackground="#b91c1c",
    )

def _hide_overlay() -> None:
    root = _overlay_root
    if root is not None:
        try:
            root.after(0, root.withdraw)
        except Exception:
            pass

def _set_control_paused(paused: bool) -> None:
    global _control_paused
    with _control_lock:
        _control_paused = paused
    if paused:
        _hide_overlay()
    elif _overlay_root is not None:
        try:
            _overlay_root.after(0, lambda: (_refresh_overlay(), _overlay_root.deiconify()))
        except Exception:
            pass

def _stop_from_overlay() -> None:
    _set_control_paused(True)

def _overlay_main() -> None:
    global _overlay_root, _overlay_status, _overlay_button
    root = tk.Tk()
    _overlay_root = root
    root.title("Desktop Control")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#111827")
    width, height = 340, 76
    x = (root.winfo_screenwidth() - width) // 2
    root.geometry(f"{width}x{height}+{x}+12")

    # Non-activating topmost window: clicking Stop must not steal Chrome focus.
    root.update_idletasks()
    hwnd = wintypes.HWND(root.winfo_id())
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 1, 2, 16, 64
    HWND_TOPMOST = wintypes.HWND(-1)
    get_long = getattr(_user32, "GetWindowLongPtrW", _user32.GetWindowLongW)
    set_long = getattr(_user32, "SetWindowLongPtrW", _user32.SetWindowLongW)
    get_long.argtypes = [wintypes.HWND, ctypes.c_int]
    get_long.restype = ctypes.c_ssize_t
    set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_long.restype = ctypes.c_ssize_t
    set_long(hwnd, GWL_EXSTYLE, get_long(hwnd, GWL_EXSTYLE) | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)

    frame = tk.Frame(root, bg="#111827", padx=10, pady=7)
    frame.pack(fill="both", expand=True)
    _overlay_status = tk.Label(frame, text="DESKTOP CONTROL  •  ACTIVE",
        bg="#111827", fg="#16a34a", font=("Segoe UI", 9, "bold"))
    _overlay_status.pack()
    _overlay_button = tk.Button(frame, text="⎋  PRESS ESC TO STOP",
        command=_stop_from_overlay, relief="flat", bg="#dc2626", fg="white",
        activebackground="#b91c1c", activeforeground="white",
        font=("Segoe UI", 10, "bold"), padx=14, pady=5, cursor="hand2")
    _overlay_button.pack(fill="x", pady=(4, 0))
    root.withdraw()  # hidden until the first desktop input operation
    _overlay_ready.set()
    root.mainloop()

def _keyboard_hook_main() -> None:
    global _hook_handle, _hook_callback

    @_hook_proc_type
    def callback(n_code, w_param, l_param):
        try:
            if n_code == HC_ACTION and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                # KBDLLHOOKSTRUCT.vkCode is a DWORD on both 32-bit and 64-bit Windows.
                vk = ctypes.cast(
                    l_param, ctypes.POINTER(ctypes.c_uint32)
                ).contents.value
                if vk == VK_ESCAPE and _control_allowed():
                    _stop_desktop_control()
                    return 1  # Consume ESC only; ordinary keys pass through.
        except BaseException:
            # A Python exception must never escape a native Windows callback.
            pass

        return _user32.CallNextHookEx(
            ctypes.c_void_p(_hook_handle) if _hook_handle else None,
            n_code,
            w_param,
            l_param,
        )

    _hook_callback = callback  # Keep the ctypes callback alive while installed.
    handle = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, _hook_callback, None, 0)
    if not handle:
        return
    _hook_handle = handle

    msg = wintypes.MSG()
    while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        _user32.TranslateMessage(ctypes.byref(msg))
        _user32.DispatchMessageW(ctypes.byref(msg))

def _ensure_overlay_started() -> None:
    global _overlay_thread, _hook_thread
    if _overlay_thread is None or not _overlay_thread.is_alive():
        _overlay_ready.clear()
        _overlay_thread = threading.Thread(target=_overlay_main, name="desktop-control-overlay", daemon=True)
        _overlay_thread.start()
        _overlay_ready.wait(timeout=3)
    if _hook_thread is None or not _hook_thread.is_alive():
        _hook_thread = threading.Thread(target=_keyboard_hook_main, name="desktop-escape-hook", daemon=True)
        _hook_thread.start()
    if _overlay_root is not None:
        try:
            _overlay_root.after(0, lambda: (_refresh_overlay(), _overlay_root.deiconify()))
        except Exception:
            pass

@mcp.tool()
def desktop_control_status() -> str:
    """Reports whether desktop automation is active or stopped."""
    return "STOPPED: ESC/Stop paused control; overlay is hidden." if not _control_allowed() else "READY: control is enabled; overlay appears when a desktop action starts."

@mcp.tool()
def desktop_resume_control() -> str:
    """Explicitly resumes desktop automation and shows the control overlay."""
    _set_control_paused(False)
    _ensure_overlay_started()
    return "Desktop control resumed. Overlay is visible; ESC or Stop hides it and pauses control."

@mcp.tool()
def desktop_screenshot() -> ImageContent:
    """Returns a screenshot spanning every monitor in the Windows virtual desktop."""
    from PIL import ImageGrab
    shot = ImageGrab.grab(all_screens=True)
    buffer = io.BytesIO()
    shot.save(buffer, format="PNG")
    return ImageContent(type="image", data=base64.b64encode(buffer.getvalue()).decode("ascii"), mimeType="image/png")


@mcp.tool()
def desktop_mouse(action: str, x: int = 0, y: int = 0, button: str = "left", clicks: int = 1, amount: int = 0) -> str:
    """Controls the mouse in physical virtual-desktop coordinates across all monitors."""
    import win32api
    import win32con
    import win32gui

    allowed = {"move", "click", "double_click", "right_click", "scroll"}
    if action not in allowed:
        return "Invalid action. Use move, click, double_click, right_click, or scroll."
    if button not in {"left", "right", "middle"}:
        return "Invalid mouse button."

    left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
    top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
    width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
    height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
    if not (left <= x < left + width and top <= y < top + height):
        return f"Coordinates must be within virtual desktop bounds x={left}..{left+width-1}, y={top}..{top+height-1}."

    if _control_allowed():
        _ensure_overlay_started()
    if not _control_allowed():
        return "CONTROL STOPPED: no mouse action was performed. Explicitly resume desktop control to continue."

    win32api.SetCursorPos((int(x), int(y)))
    flags = {
        "left": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
        "right": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP),
        "middle": (win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP),
    }
    if action == "move":
        pass
    elif action in {"click", "double_click", "right_click"}:
        chosen = "right" if action == "right_click" else button
        down, up = flags[chosen]
        count = 2 if action == "double_click" else max(1, min(int(clicks), 3))
        for _ in range(count):
            win32api.mouse_event(down, 0, 0, 0, 0)
            win32api.mouse_event(up, 0, 0, 0, 0)
            if count > 1:
                time.sleep(0.06)
    elif action == "scroll":
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, int(max(-20, min(amount, 20)) * 120), 0)

    _sync_input_baseline()
    return f"Mouse {action} completed at virtual-desktop coordinate ({x}, {y}); bounds=({left},{top},{width},{height})."


@mcp.tool()
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

        from pywinauto.uia_defines import IUIA
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
    except Exception as exc:
        return f"REFUSED: could not verify the focused control ({type(exc).__name__}: {exc})."

    if not _control_allowed():
        return "CONTROL STOPPED before typing."
    _ensure_overlay_started()
    # Recheck immediately before sending the text as one batch. The previous
    # character-by-character loop paid PyAutoGUI's global PAUSE on every
    # character and added an artificial sleep per character.
    if not _control_allowed():
        return "CONTROL STOPPED before typing."
    pyautogui.write(text, interval=0)
    _sync_input_baseline()
    return f"Typed {len(text)} characters in one batch into verified focus {focus_description!r} in {actual_title!r}."


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
    if _control_allowed():
        _ensure_overlay_started()
    if not _control_allowed():
        return "CONTROL STOPPED: no key was pressed. Explicitly resume desktop control to continue."
    pyautogui.press(key)
    _sync_input_baseline()
    return f"Pressed {key}."


@mcp.tool()
def desktop_hotkey(keys: list[str]) -> str:
    """Sends a keyboard shortcut such as ['ctrl','s'], ['alt','tab'], or ['ctrl','shift','esc']."""
    allowed = {"ctrl", "alt", "shift", "win", "enter", "esc", "tab", "s", "c", "v", "x", "a", "z", "y", "w", "t", "r", "f", "d", "e", "n", "o", "p", "space"}
    normalized = [k.lower().strip() for k in keys]
    if not 2 <= len(normalized) <= 4 or any(k not in allowed for k in normalized):
        return "Shortcut rejected. Use 2-4 allowed keys, e.g. ['ctrl','s']."
    if _control_allowed():
        _ensure_overlay_started()
    if not _control_allowed():
        return "CONTROL STOPPED: no shortcut was sent. Explicitly resume desktop control to continue."
    pyautogui.hotkey(*normalized)
    _sync_input_baseline()
    return "Pressed " + "+".join(normalized) + "."



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

    def terminal_input_has_focus():
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

            elif action == "wait_terminal_panel":
                # Panel presence is separate from keyboard focus.
                wait_for = min(max(float(step.get("timeout", 8.0)), 0.2), 20.0)
                deadline = _time.monotonic() + wait_for
                found_tab = False
                found_input = False
                while _time.monotonic() < deadline:
                    if not _control_allowed():
                        return "\n".join(results + [f"STOPPED while waiting at step {index}."])
                    current_hwnd, current_title = foreground_title()
                    if current_hwnd != hwnd or needle not in current_title.casefold():
                        return "\n".join(results + [f"REFUSED at step {index}: foreground changed."])
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
                    return "\n".join(results + [
                        f"FAILED step {index}: terminal panel not verified (tab={found_tab}, input={found_input})."
                    ])
                results.append(f"Step {index}: verified integrated terminal panel and terminal Edit control; keyboard focus NOT assumed.")

            elif action == "wait_terminal_focus":
                wait_for = min(max(float(step.get("timeout", 8.0)), 0.2), 20.0)
                deadline = _time.monotonic() + wait_for
                has_focus = False
                while _time.monotonic() < deadline:
                    if not _control_allowed():
                        return "\n".join(results + [f"STOPPED while waiting at step {index}."])
                    current_hwnd, current_title = foreground_title()
                    if current_hwnd != hwnd or needle not in current_title.casefold():
                        return "\n".join(results + [f"REFUSED at step {index}: foreground changed."])
                    try:
                        has_focus = terminal_input_has_focus()
                    except Exception:
                        has_focus = False
                    if has_focus:
                        break
                    _time.sleep(0.1)
                if not has_focus:
                    return "\n".join(results + [
                        f"FAILED step {index}: VS Code terminal input did not report keyboard focus."
                    ])
                results.append(f"Step {index}: verified keyboard focus on VS Code Terminal input.")

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
                terminal_expected = expected.casefold() in ("terminal", "terminal input")
                focus_matches = terminal_input_has_focus() if terminal_expected else expected.casefold() in focus.casefold()
                if not focus_matches:
                    return "\n".join(results + [
                        f"REFUSED step {index}: terminal keyboard focus was not verified; focused element={focus!r}."
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
                    "Supported: hotkey, wait_terminal_panel, wait_terminal_focus, wait_focus, type_text, press_key."
                ])

        return f"WORKFLOW COMPLETED in {len(steps)} steps for {title!r}.\n" + "\n".join(results)
    except Exception as exc:
        return f"WORKFLOW FAILED: {type(exc).__name__}: {exc}"


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
    """Inspects visible, non-minimized Windows UI Automation controls with validated physical-screen coordinates."""
    try:
        import win32gui
        import pyautogui
        from pywinauto import Desktop

        import win32api, win32con
        screen_left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
        screen_top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
        screen_w = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
        screen_h = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
        desktop = Desktop(backend="uia")
        needle = (window_title or "").casefold()
        output = []
        seen_handles = set()
        # Inspect the foreground window; its native Win32 bounds are in physical screen coordinates.
        foreground_handle = int(win32gui.GetForegroundWindow())

        for win in desktop.windows():
            try:
                handle = int(win.handle)
                if handle in seen_handles or handle != foreground_handle:
                    continue
                seen_handles.add(handle)

                title = win.window_text() or ""
                if needle and needle not in title.casefold():
                    continue
                if not win32gui.IsWindowVisible(handle) or win32gui.IsIconic(handle):
                    continue

                left, top, right, bottom = win32gui.GetWindowRect(handle)
                # Reject minimized/off-screen/invalid window rectangles.
                if right <= left or bottom <= top:
                    continue
                if right <= screen_left or bottom <= screen_top or left >= screen_left + screen_w or top >= screen_top + screen_h:
                    continue

                output.append(
                    f"WINDOW: {title!r} | handle={handle} "
                    f"| rect={{'left': {left}, 'top': {top}, 'right': {right}, 'bottom': {bottom}}}"
                )
                count = 0
                for ctrl in win.descendants():
                    if count >= max(1, min(int(max_elements), 200)):
                        output.append("  ... element limit reached")
                        break
                    try:
                        info = ctrl.element_info
                        name = (info.name or "").strip()
                        ctype = info.control_type or "Unknown"
                        aid = info.automation_id or ""
                        if not name and not aid:
                            continue
                        if not ctrl.is_visible():
                            continue
                        try:
                            enabled = ctrl.is_enabled()
                        except Exception:
                            enabled = False
                        r = ctrl.rectangle()
                        cl, ct, cr, cb = r.left, r.top, r.right, r.bottom
                        # Keep only plausible screen-space bounds. Some providers
                        # expose sentinel coordinates for minimized/off-screen children.
                        if cr <= cl or cb <= ct:
                            continue
                        if cr <= screen_left or cb <= screen_top or cl >= screen_left + screen_w or ct >= screen_top + screen_h:
                            continue
                        cx = (cl + cr) // 2
                        cy = (ct + cb) // 2
                        output.append(
                            f"  {ctype} | name={name!r} | automation_id={aid!r} "
                            f"| enabled={enabled} | rect=({cl},{ct},{cr},{cb}) "
                            f"| center=({cx},{cy})"
                        )
                        count += 1
                    except Exception:
                        continue
            except Exception:
                continue

        return "\n".join(output) if output else "No visible, non-minimized matching windows/elements found."
    except Exception as exc:
        return f"UI inspection failed: {type(exc).__name__}: {exc}"

@mcp.tool()
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
                f"DESKTOP VISUAL SNAPSHOT\\n"
                f"Screenshot size: {shot.width}x{shot.height}\\n"
                f"Virtual desktop bounds (left, top, width, height): {virtual_bounds}\\n"
                f"Screenshot origin corresponds to the virtual desktop's top-left.\\n"
                f"UI metadata is limited to the foreground window.\\n"
                f"No mouse or keyboard input was sent.\n\n"
                f"{ui_text}"
            ),
        )
        return [metadata, screenshot_content]
    except Exception as exc:
        return [TextContent(type="text", text=f"Visual snapshot failed: {type(exc).__name__}: {exc}")]

@mcp.tool()
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
        import win32api, win32con
        screen_left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
        screen_top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
        screen_w = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
        screen_h = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
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
                        if right <= left or bottom <= top:
                            continue
                        if right <= screen_left or bottom <= screen_top or left >= screen_left + screen_w or top >= screen_top + screen_h:
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
                            "center": ((left + right) // 2, (top + bottom) // 2),
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
        return "\n".join(lines)
    except Exception as exc:
        return f"Target matching failed: {type(exc).__name__}: {exc}"

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



@mcp.tool()
def desktop_list_windows(include_minimized: bool = True, max_windows: int = 80) -> str:
    """Lists top-level Windows applications across the interactive Windows desktop, including windows not in the foreground. Read-only."""
    try:
        import win32gui
        import win32process
        import win32api
        import win32con
        from pywinauto import Desktop

        rows = []
        foreground = int(win32gui.GetForegroundWindow())
        desktop = Desktop(backend="uia")
        seen = set()
        for win in desktop.windows():
            try:
                hwnd = int(win.handle)
                if not hwnd or hwnd in seen or not win32gui.IsWindow(hwnd):
                    continue
                seen.add(hwnd)
                title = (win.window_text() or "").strip()
                if not title:
                    continue
                minimized = bool(win32gui.IsIconic(hwnd))
                visible = bool(win32gui.IsWindowVisible(hwnd))
                if not include_minimized and minimized:
                    continue
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    process = win32api.GetModuleFileNameEx(
                        win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid), 0
                    )
                    process = process.rsplit("\\\\", 1)[-1]
                except Exception:
                    process = "unknown"
                try:
                    rect = win32gui.GetWindowRect(hwnd)
                except Exception:
                    rect = None
                flags = []
                if hwnd == foreground:
                    flags.append("FOREGROUND")
                if minimized:
                    flags.append("MINIMIZED")
                if not visible:
                    flags.append("HIDDEN")
                rows.append((hwnd == foreground, f"{title!r} | process={process} | hwnd={hwnd} | {'/'.join(flags) or 'background'} | rect={rect}"))
            except Exception:
                continue
        rows.sort(key=lambda x: x[0], reverse=True)
        lines = [r[1] for r in rows[:max(1, min(int(max_windows), 200))]]
        return "WINDOW ENUMERATION (read-only)\n" + ("\n".join(lines) if lines else "No titled application windows found.")
    except Exception as exc:
        return f"Window enumeration failed: {type(exc).__name__}: {exc}"


@mcp.tool()
def desktop_inspect_window(window_title: str, max_elements: int = 120) -> str:
    """Inspects UI Automation controls in a specifically named window, even when it is not foreground. Read-only; does not switch desktops or send input."""
    try:
        import win32gui
        from pywinauto import Desktop

        needle = (window_title or "").strip().casefold()
        if not needle:
            return "Provide part of the target window title, e.g. 'sp-inventory-app' or 'Visual Studio Code'."
        desktop = Desktop(backend="uia")
        matches = []
        for win in desktop.windows():
            try:
                title = (win.window_text() or "").strip()
                if needle in title.casefold():
                    matches.append((win, title, int(win.handle)))
            except Exception:
                continue
        if not matches:
            return f"No window title containing {window_title!r} was found. Run desktop_list_windows to see available windows."
        if len(matches) > 1:
            return "Multiple windows matched; narrow the title:\n" + "\n".join(
                f"{title!r} | hwnd={hwnd}" for _, title, hwnd in matches[:20]
            )

        win, title, hwnd = matches[0]
        foreground = int(win32gui.GetForegroundWindow())
        minimized = bool(win32gui.IsIconic(hwnd))
        visible = bool(win32gui.IsWindowVisible(hwnd))
        rect = win32gui.GetWindowRect(hwnd)
        lines = [
            f"WINDOW: {title!r} | hwnd={hwnd}",
            f"foreground={hwnd == foreground} | visible={visible} | minimized={minimized} | rect={rect}",
            "Inspection is via UI Automation metadata; no focus change, screenshot, mouse, or keyboard input was performed."
        ]
        count = 0
        for ctrl in win.descendants():
            if count >= max(1, min(int(max_elements), 250)):
                lines.append("... element limit reached")
                break
            try:
                info = ctrl.element_info
                name = (info.name or "").strip()
                aid = (info.automation_id or "").strip()
                ctype = (info.control_type or "Unknown").strip()
                if not name and not aid:
                    continue
                try:
                    enabled = bool(ctrl.is_enabled())
                except Exception:
                    enabled = False
                try:
                    ctrl_visible = bool(ctrl.is_visible())
                except Exception:
                    ctrl_visible = False
                r = ctrl.rectangle()
                bounds = (r.left, r.top, r.right, r.bottom)
                lines.append(
                    f"  {ctype} | name={name!r} | automation_id={aid!r} "
                    f"| enabled={enabled} | visible={ctrl_visible} | rect={bounds}"
                )
                count += 1
            except Exception:
                continue
        return "\n".join(lines)
    except Exception as exc:
        return f"Window inspection failed: {type(exc).__name__}: {exc}"


# Register Phase 1 agent tools on this existing MCP server.
from agent_core import register_agent_tools
register_agent_tools(mcp, BASE_DIR, INDEX_DIR, safe_path)


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8000,
        streamable_http_path="/mcp",
        json_response=True,
    )
