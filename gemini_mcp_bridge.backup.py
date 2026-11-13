import hashlib
import os
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

from typing import Any

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
    "https://print-breach-experiencing-relatively.trycloudflare.com/mcp"
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


mcp = MCPServer("LocalFilesystem")

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


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8000,
        streamable_http_path="/mcp",
        json_response=True,
    )
