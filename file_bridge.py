import os
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel, Field

app = FastAPI(title="Local File Bridge")

# The folder on your computer ChatGPT is allowed to manage:
BASE_DIR = os.path.expanduser("~/AI_Workspace")

# Your secret API key password (change this if you like):
AUTH_SECRET = "super-secret-passkey-123"

os.makedirs(BASE_DIR, exist_ok=True)

class WritePayload(BaseModel):
    path: str = Field(..., description="Relative path, e.g. 'notes.md' or 'src/index.js'")
    content: str = Field(..., description="Text content to write")
    mode: str = Field("overwrite", description="'overwrite' or 'append'")

def auth_check(authorization: str = Header(None)):
    if not authorization or authorization != f"Bearer {AUTH_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

def get_safe_path(rel_path: str) -> str:
    # Prevents ../ path traversal exploits outside your workspace folder
    target = os.path.abspath(os.path.join(BASE_DIR, rel_path.lstrip("/")))
    if not target.startswith(os.path.abspath(BASE_DIR)):
        raise HTTPException(status_code=403, detail="Path traversal forbidden")
    return target

@app.get("/files", dependencies=[Depends(auth_check)])
def list_files(subfolder: str = ""):
    target = get_safe_path(subfolder)
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="Directory not found")

    items = []
    for entry in os.listdir(target):
        p = os.path.join(target, entry)
        items.append({
            "name": entry,
            "type": "folder" if os.path.isdir(p) else "file",
            "size_bytes": os.path.getsize(p) if os.path.isfile(p) else None
        })
    return {"directory": subfolder or "/", "items": items}

@app.get("/read", dependencies=[Depends(auth_check)])
def read_file(path: str):
    target = get_safe_path(path)
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="File not found")
    with open(target, "r", encoding="utf-8", errors="replace") as f:
        return {"path": path, "content": f.read()}

@app.post("/write", dependencies=[Depends(auth_check)])
def write_file(payload: WritePayload):
    target = get_safe_path(payload.path)
    os.makedirs(os.path.dirname(target), exist_ok=True)

    file_mode = "a" if payload.mode == "append" else "w"
    with open(target, file_mode, encoding="utf-8") as f:
        f.write(payload.content)

    return {"status": "ok", "path": payload.path, "mode": payload.mode}