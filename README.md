# EasyGPT — Local Desktop & Development MCP Bridge

> Give an AI assistant a carefully controlled way to see, operate, and work with your Windows desktop, local files, development projects, and VS Code terminals.

EasyGPT is a Windows-focused **Model Context Protocol (MCP) bridge** that exposes local computer and developer-workflow capabilities to an MCP-compatible AI client. It combines desktop automation, Windows UI Automation, local project indexing, file operations, command execution, Blender scripting, VS Code terminal integration, and a persistent task ledger behind one bridge.

The goal is simple: move beyond an assistant that can only explain what to do. With the right permissions and a connected MCP client, an assistant can inspect the environment, plan work, perform supported actions, verify results, and keep a record of progress.

---

## Contents

- [What EasyGPT can do](#what-easygpt-can-do)
- [Capability overview](#capability-overview)
- [How it works](#how-it-works)
- [Desktop automation](#desktop-automation)
- [Local files and project intelligence](#local-files-and-project-intelligence)
- [Developer tools](#developer-tools)
- [VS Code terminal bridge](#vs-code-terminal-bridge)
- [Autonomous task infrastructure](#autonomous-task-infrastructure)
- [Security and safety](#security-and-safety)
- [Project structure](#project-structure)
- [Setup](#setup)
- [Configuration notes](#configuration-notes)
- [Known boundaries](#known-boundaries)
- [Roadmap ideas](#roadmap-ideas)

---

## What EasyGPT can do

EasyGPT gives an MCP-compatible assistant access to a set of tools running on the user's Windows machine.

### 🖥️ See and operate the desktop

- Capture screenshots across the Windows virtual desktop, including multiple monitors.
- Inspect visible Windows UI Automation (UIA) controls and their accessible names, types, IDs, enabled state, and screen coordinates.
- Combine screenshot context with UI metadata in a visual snapshot.
- Find and interact with applications through semantic UI controls.
- Move and click the mouse, scroll, press keys, and send keyboard shortcuts.
- Type text only after checking the expected foreground window and focused UI control.
- Run short, ordered desktop workflows with checks for the target window and focus between steps.

### 📁 Work with local files

- Browse directories in the configured Desktop workspace.
- Read UTF-8 text files.
- Create, overwrite, or append to text files.
- Keep file operations inside the configured workspace boundary.
- Index source code from local projects for fast, repeatable search.

### 🧠 Understand codebases

- Index source files into a local SQLite database.
- Incrementally update files that have changed.
- Skip common dependency, build, cache, and version-control folders.
- Search indexed code and return matching file paths, line numbers, and snippets.
- Generate a compact project tree and file-extension summary.

### 🛠️ Run development work

- Execute short shell commands in a chosen workspace folder.
- Start longer-running development commands in the background.
- Run Blender Python scripts in background-free, headless Blender mode.
- Use the companion VS Code extension to list, create, focus, and run commands in integrated terminals.

### 🤖 Track multi-step work

- Create persistent task records with a title and goal.
- Save ordered plans containing up to 30 steps.
- Track task and step status, current progress, and summaries.
- Record audit events as work progresses.
- Request and record explicit user approvals.
- Assess shell commands for potentially risky operations before execution.

---

## Capability overview

| Area | Capabilities |
|---|---|
| Desktop visibility | Multi-monitor screenshots, visual snapshots, UIA inspection |
| Desktop input | Mouse, keyboard, shortcuts, guarded text entry |
| UI interaction | Find controls, inspect windows, click accessible controls |
| Filesystem | List, read, write, append within the Desktop workspace |
| Project intelligence | Incremental indexing, code search, project maps |
| Command execution | Short commands and background processes |
| 3D workflows | Execute Blender Python scripts |
| VS Code | List/create/focus terminals, send text, execute commands and capture output |
| Task management | Persistent tasks, ordered plans, progress and audit events |
| Approval workflow | Record pending approvals and decisions |
| Authentication | Supabase JWT verification for the MCP server |

---

## How it works

At a high level, EasyGPT has three cooperating pieces:

1. **Python MCP server** — exposes local computer, filesystem, project, and task-management tools.
2. **VS Code extension** — exposes a small authenticated localhost API for integrated terminal operations.
3. **MCP-compatible AI client** — discovers the tools and decides when to call them as part of a user-requested workflow.

The Python server uses a Supabase-backed OAuth resource-server configuration and verifies access tokens against the project's published JSON Web Key Set (JWKS). The VS Code extension is a separate local companion service; it is not the MCP server itself.

```text
┌──────────────────────────────┐
│     MCP-compatible client    │
│       AI assistant           │
└──────────────┬───────────────┘
               │ MCP + OAuth
               ▼
┌──────────────────────────────┐
│       Python MCP server      │
│                              │
│  Desktop automation          │
│  Windows UI Automation       │
│  Workspace file operations   │
│  Project index + code search │
│  Command / Blender tools     │
│  Persistent task ledger      │
└──────────────┬───────────────┘
               │ localhost HTTP
               ▼
┌──────────────────────────────┐
│      VS Code extension       │
│                              │
│  Terminal discovery          │
│  Terminal creation/focus     │
│  Text submission             │
│  Command execution/output    │
└──────────────────────────────┘
```

The Python server stores project indexes and task records locally in the Windows user data area. Project source files are not uploaded by the indexing functions; the index is stored in local SQLite databases.

---

## Desktop automation

EasyGPT's desktop tools are designed for interacting with real Windows applications rather than only manipulating files or issuing shell commands.

### Screenshots and visual context

- `desktop_screenshot` — captures the Windows virtual desktop across all monitors.
- `desktop_visual_snapshot` — returns a screenshot together with UI Automation metadata for the foreground window.
- `desktop_inspect_ui` — inspects visible controls in the foreground window, optionally filtering by window title.

UI Automation metadata can expose useful details such as control names, control types, automation IDs, enabled state, and screen rectangles. This gives an assistant more reliable targets than guessing coordinates from a screenshot alone.

### Mouse and keyboard

- `desktop_mouse` — move, click, double-click, right-click, and scroll.
- `desktop_press_key` — press supported navigation, editing, and function keys.
- `desktop_hotkey` — send supported keyboard shortcuts.
- `desktop_type_text` — type into a verified foreground window and focused UI control.

### Semantic UI interaction

- `desktop_list_windows` — list top-level application windows, including minimized windows.
- `desktop_inspect_window` — inspect controls in a specifically named window without first making it foreground.
- `desktop_find_target` — rank visible controls against a natural-language description.
- `desktop_click_ui_element` — click a uniquely matched accessible control, with optional control-type or automation-ID narrowing.

### Ordered desktop workflows

`desktop_execute_workflow` runs a short sequence of supported actions against one identified window. It can:

- Activate and verify the target window.
- Send supported shortcuts.
- Wait for a particular focused control.
- Verify that the VS Code terminal panel is present.
- Verify actual keyboard focus on the VS Code terminal input.
- Type only after focus and foreground-window checks.
- Press supported keys.

The workflow is intentionally limited to a small set of actions; destructive actions are not supported by this workflow runner.

### Desktop control safety

Desktop input has an explicit pause/resume state:

- `desktop_control_status` reports whether control is ready or stopped.
- `desktop_resume_control` explicitly resumes control and displays the control overlay.
- Pressing **Esc** or using the overlay's Stop control pauses automation.
- PyAutoGUI's corner failsafe is also enabled.

The overlay is shown when desktop input is used. Screenshots and UI inspection are read-only operations.

---

## Local files and project intelligence

The main MCP server uses the user's **Desktop folder as its filesystem workspace**. Paths supplied to these tools are relative to that workspace.

| Tool | What it does |
|---|---|
| `list_files` | Lists files and folders in a workspace directory |
| `read_file` | Reads a UTF-8 text file |
| `write_file` | Writes or appends UTF-8 text |
| `index_project` | Indexes supported source files in a project |
| `search_code` | Searches indexed source and returns line matches |
| `project_map` | Returns an indexed file tree and file-type summary |

### Incremental local indexing

The indexer recognises common source and project formats, including Python, JavaScript, TypeScript, Dart, Java, Kotlin, Swift, C/C++, C#, Go, Rust, PHP, Ruby, HTML, CSS, JSON, YAML, SQL, Markdown, XML, and more.

It skips common generated and dependency directories such as:

- `.git`, `node_modules`, `.next`, `dist`, and `build`
- `.venv`, `venv`, and `__pycache__`
- `.dart_tool`, `.gradle`, `Pods`, and `DerivedData`

Files larger than 1 MB are skipped. The indexer checks file size and modification time to avoid re-reading unchanged files, and stores a SHA-256 digest with indexed content.

Each project receives its own SQLite index under:

```text
%LOCALAPPDATA%\ChatGPTProjectIndex\
```

The database is local to the machine. Run `index_project` again after code changes to refresh the index before searching.

---

## Developer tools

### Command execution

- `run_command` runs a command in the workspace or a selected project subfolder.
- It captures standard output and standard error, returns the exit code, and has a 25-second timeout.
- `run_background_command` starts a process separately and returns its process ID. This is useful for development servers, watchers, and other long-running commands.

Commands run with the permissions of the Windows account running the bridge. Treat them as real local machine operations.

### Blender automation

`run_blender_script` runs a Python script located inside the Desktop workspace using Blender's background mode:

```powershell
blender -b -P "path\to\script.py"
```

It captures the process output and reports Blender's exit code. Blender must be installed and available on PATH for this tool to work.

---

## VS Code terminal bridge

The companion extension lives in `easygpt-vscode/`.

It provides a localhost-only HTTP API so an assistant can work with VS Code's integrated terminals through VS Code's extension API and Shell Integration.

### Available endpoints

All endpoints require the generated bearer token.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/v1/health` | Check extension bridge health |
| GET | `/v1/terminals` | List open integrated terminals |
| POST | `/v1/terminals` | Create and show a terminal |
| POST | `/v1/terminals/:id/focus` | Focus a specific terminal |
| POST | `/v1/terminals/:id/send` | Send text to a terminal |
| POST | `/v1/terminals/:id/execute` | Execute a command and capture output/exit code |

### Example requests

List terminals:

```http
GET /v1/terminals
Authorization: Bearer <token>
```

Create a terminal:

```http
POST /v1/terminals
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "EasyGPT Terminal",
  "cwd": "C:\\Users\\YourUser\\Desktop\\my-project"
}
```

Execute a command:

```http
POST /v1/terminals/<terminal-id>/execute
Authorization: Bearer <token>
Content-Type: application/json

{
  "command": "npm run build",
  "timeoutMs": 30000
}
```

The execute endpoint uses VS Code Shell Integration and returns captured output and an exit code. Output capture is capped to the most recent 64 KB; the requested timeout is bounded by the extension.

### Connection descriptor

When activated, the extension writes a short-lived descriptor to:

```text
%TEMP%\easygpt-vscode-bridge.json
```

It contains the localhost host/port, a randomly generated bearer token, the extension process ID, and version. The descriptor is removed when the extension deactivates.

### Extension safety

- The server binds to `127.0.0.1`, not a public network interface.
- Requests from non-localhost clients are rejected.
- Every request must provide the generated bearer token.
- Terminal IDs are generated by the extension.
- Sending text does not press Enter unless `addNewLine: true` is explicitly provided.
- No terminal is killed or closed by the extension.

The extension requires VS Code 1.90 or newer according to its package manifest.

---

## Autonomous task infrastructure

The task ledger provides durable state for multi-step work. It does not itself decide what to do or execute a plan; the AI client orchestrates tool calls and updates the ledger as it works.

### Task lifecycle

Tasks can use these statuses:

`planned` · `running` · `waiting_approval` · `completed` · `failed` · `cancelled`

Each task can store:

- A title and goal.
- Current step and progress summary.
- Creation and update timestamps.
- Recent audit events.
- Approval requests and their decisions.
- An ordered plan of up to 30 steps.

### Plan and step tracking

Plans contain ordered steps with a title and optional description. Each step can be tracked as:

`pending` · `running` · `completed` · `failed` · `blocked`

The task tools can retrieve the next pending step, update a step result, and automatically update the parent task's progress state.

### Approval records

The agent can record an approval request with the action and reason, then wait for the user to explicitly approve or reject it in chat. The approval tool records the decision; the assistant must still enforce that decision before performing the action.

### Command risk assessment

`agent_assess_command` classifies a shell command as low, medium, or high risk and indicates whether the command pattern requires user approval.

This check is **advisory, not an execution sandbox or security boundary**. The AI client must enforce approval before running a command, and commands can have side effects.

### Task persistence

Task data is stored in:

```text
%LOCALAPPDATA%\ChatGPTProjectIndex\easygpt_agent.sqlite3
```

---

## Security and safety

EasyGPT can interact with a real computer. It should be treated as privileged local automation software.

### Protections implemented

- **Supabase JWT verification:** The MCP server validates token signatures against the configured Supabase JWKS and checks issuer, audience, expiry, authenticated role, subject, and client ID.
- **Workspace path validation:** Main MCP filesystem and project tools resolve paths against the Desktop workspace and reject paths that escape it, including through symlinks.
- **Guarded typing:** Desktop text entry checks both the expected foreground window title and focused UI Automation element.
- **Workflow focus checks:** Desktop workflows re-check the target window and focus before input actions.
- **Emergency stop:** Esc and the overlay Stop control pause desktop automation.
- **Local VS Code API:** The extension binds only to loopback and requires a randomly generated bearer token.
- **Approval ledger:** Approval requests and decisions can be recorded and audited.

### Important operational cautions

- Shell commands run with the bridge process's Windows user permissions.
- Desktop clicks and keystrokes affect the real foreground environment.
- A screenshot can contain private information visible on the monitor.
- A risk assessment is advisory; it does not make a command safe.
- The VS Code execute endpoint runs the command supplied to it in the selected terminal.
- Keep OAuth configuration, tunnel URLs, tokens, and local descriptors out of public commits.
- Review any command or automation before allowing it to change or delete important data.

### Network exposure

The Python MCP server is configured with a Supabase OAuth issuer and a public MCP resource URL. If exposing the local MCP server through a tunnel, protect the tunnel and OAuth configuration carefully. A Cloudflare Quick Tunnel URL is temporary and may change when the tunnel is restarted.

The VS Code companion API is separate and remains localhost-only.

---

## Project structure

```text
gpt-file-bridge/
├── gemini_mcp_bridge.py       # Main Python MCP server
├── agent_core.py              # Persistent task, plan, event, and approval tools
├── file_bridge.py             # Separate/legacy FastAPI file API (see note below)
├── enable_desktop_control.py  # Historical setup helper
├── diagnose_terminal_focus.py # Windows UIA focus diagnostic helper
├── fix_focus_validation.py    # Historical patch helper
└── easygpt-vscode/
    ├── extension.js           # VS Code terminal bridge
    ├── package.json           # Extension manifest
    ├── README.md              # Extension-specific documentation
    └── .vscode/
        └── launch.json        # Extension development launch config
```

### Note about `file_bridge.py`

`file_bridge.py` is a separate FastAPI application with its own workspace and authentication code. It is not the same filesystem implementation as the main MCP server, and it is not the VS Code bridge. Review and harden its configuration before using it; do not assume it inherits the main MCP server's OAuth or path protections.

The `enable_desktop_control.py` and `fix_focus_validation.py` scripts are historical development helpers. The current desktop-control implementation is already present in `gemini_mcp_bridge.py`; these scripts are not required for normal startup.

---

## Setup

This repository is currently a source project rather than a turnkey installer. The steps below outline the environment that the code expects; exact dependency pins and a reproducible lockfile should be added before distributing it to other machines.

### 1. Windows prerequisites

- Windows 10/11.
- Python 3.11+ recommended (the bridge uses modern Python typing syntax).
- Node.js and npm for the VS Code extension.
- Visual Studio Code 1.90+.
- Blender on PATH if you intend to use Blender scripting.
- A configured Supabase project for OAuth token verification.
- A secure way to expose the MCP endpoint if your MCP client connects remotely.

### 2. Python dependencies

The main bridge imports the MCP Python SDK, PyJWT, Pydantic, PyAutoGUI, Pillow, pywin32, and pywinauto.

Install the corresponding packages in a dedicated virtual environment. Example:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install mcp PyJWT cryptography pydantic pyautogui pillow pywin32 pywinauto
```

The standalone `file_bridge.py` additionally imports FastAPI and Pydantic; running that separate API also requires an ASGI server such as Uvicorn.

> These are package names inferred from the current source imports, not a tested pinned requirements set. Create and maintain a `requirements.txt` or lockfile for reproducible installs.

### 3. Configure OAuth and the MCP resource URL

Review the Supabase issuer, JWKS URL, expected audience, and MCP resource URL configuration near the top of `gemini_mcp_bridge.py`.

Use your own Supabase project values and the actual stable public MCP URL for your deployment. Do not commit private keys, access tokens, or other secrets.

### 4. Start the Python MCP server

Start the server using the MCP SDK's supported entry point for the installed SDK version and configure your MCP client to connect to that server using the appropriate transport and OAuth settings.

The exact launch command and transport configuration should match the installed MCP SDK version and your deployment method; they are not currently documented as a checked-in launcher or installer.

### 5. Install the VS Code extension for development

Open the extension folder in VS Code:

```powershell
cd easygpt-vscode
code .
```

Then:

1. Run `npm install` if a package lock or dependencies are added in a future version.
2. Press **F5** to launch an Extension Development Host.
3. In that host window, open a workspace and create a fresh integrated terminal.
4. Wait for Shell Integration to become available.
5. The extension starts its localhost service on port `47831` and writes its connection descriptor to the system temp directory.

The extension currently uses Node.js built-in modules and the VS Code API; its manifest does not list external npm dependencies.

---

## Configuration notes

Before sharing or deploying this project, centralise configuration rather than editing source constants manually.

Recommended environment/configuration values:

| Setting | Purpose |
|---|---|
| Supabase project URL | OAuth issuer and JWKS location |
| Expected JWT audience | Audience claim expected in access tokens |
| MCP resource URL | Public resource identifier used by the MCP auth configuration |
| Workspace root | Local folder the MCP filesystem tools can access |
| Index directory | Local location for SQLite project indexes and task data |
| Tunnel/public URL | External route to the local MCP server, if used |

The current source contains deployment-specific Supabase and tunnel configuration. Replace these values with your own before publishing the repository.

---

## Known boundaries

EasyGPT is powerful, but it is not an unrestricted remote-control platform.

- The main file tools operate on text files in the Desktop workspace; they are not a general-purpose binary file editor.
- Project search works against files that have been indexed and may need re-indexing after edits.
- The indexer skips unsupported extensions, ignored directories, and files larger than 1 MB.
- Short shell commands have a 25-second timeout; use the background command tool for long-running processes.
- Blender execution has a 60-second timeout.
- Desktop workflows support a deliberately limited set of actions and a maximum of 20 steps per call.
- UI Automation depends on the accessibility information exposed by each Windows application. Some custom-drawn interfaces may expose little or no useful metadata.
- VS Code command output capture depends on Shell Integration being ready.
- The VS Code extension's terminal API is local to the machine where the extension is running.
- Task records provide durable tracking; they do not independently schedule or execute tasks.

---

## Roadmap ideas

Potential next steps for making EasyGPT easier to install, safer to operate, and more capable:

- [ ] Add a pinned `requirements.txt` and automated environment setup.
- [ ] Move all deployment-specific settings into environment variables.
- [ ] Add a first-run configuration wizard and health diagnostics.
- [ ] Add structured tool schemas and consistent JSON responses.
- [ ] Add tests for path validation, indexing, task state transitions, and approval flows.
- [ ] Add a command execution policy that is enforced server-side, not only advisory.
- [ ] Add explicit allowlists for workspace roots and executable commands.
- [ ] Add file metadata and safe binary transfer tools with size limits.
- [ ] Add structured logging with secret redaction.
- [ ] Add packaging and a one-command Windows service/launcher.
- [ ] Add a documented MCP client configuration example.
- [ ] Add automated integration tests for the VS Code extension.

---

## Philosophy

EasyGPT is built around a simple idea:

**An AI assistant should be able to do useful work in the tools people already use — while keeping the user informed and in control.**

It brings together local context, desktop interaction, development tools, and task state so that an assistant can move from *"here's how you do it"* to *"I can help carry it out, verify what happened, and show you the result."*

