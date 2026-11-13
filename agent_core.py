"""Phase 1 task ledger and safety primitives for EasyGPT.

This module is registered by the existing MCP server; it does not start a
second server or expose a separate network listener.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def register_agent_tools(mcp: Any, base_dir: str, index_dir: str, safe_path: Any) -> None:
    """Register task-management tools on the already-running MCP server."""
    db_path = Path(index_dir) / "easygpt_agent.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect() -> sqlite3.Connection:
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                goal TEXT NOT NULL,
                status TEXT NOT NULL,
                current_step TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(task_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(task_id)
            )
        """)
        conn.commit()
        return conn

    def task_exists(conn: sqlite3.Connection, task_id: str) -> bool:
        return conn.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone() is not None

    @mcp.tool()
    def agent_create_task(title: str, goal: str) -> str:
        """Create a persistent EasyGPT task record and return its task ID."""
        title = (title or "").strip()[:160]
        goal = (goal or "").strip()[:4000]
        if not title or not goal:
            return "A non-empty title and goal are required."
        task_id = uuid.uuid4().hex[:12]
        now = _now()
        with connect() as conn:
            conn.execute(
                "INSERT INTO tasks(task_id,title,goal,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (task_id, title, goal, "planned", now, now),
            )
            conn.execute(
                "INSERT INTO task_events(task_id,event_type,message,created_at) VALUES(?,?,?,?)",
                (task_id, "created", "Task created.", now),
            )
        return f"Created task {task_id}: {title} [planned]"

    @mcp.tool()
    def agent_update_task(task_id: str, status: str = "", current_step: str = "", summary: str = "") -> str:
        """Update task status/progress. Status: planned, running, waiting_approval, completed, failed, cancelled."""
        allowed = {"planned", "running", "waiting_approval", "completed", "failed", "cancelled"}
        if status and status not in allowed:
            return "Invalid status. Use: " + ", ".join(sorted(allowed))
        with connect() as conn:
            if not task_exists(conn, task_id):
                return f"Task not found: {task_id}"
            row = conn.execute("SELECT status,current_step,summary FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            new_status = status or row["status"]
            new_step = current_step[:500] if current_step else row["current_step"]
            new_summary = summary[:4000] if summary else row["summary"]
            now = _now()
            conn.execute(
                "UPDATE tasks SET status=?,current_step=?,summary=?,updated_at=? WHERE task_id=?",
                (new_status, new_step, new_summary, now, task_id),
            )
            changed = []
            if status and status != row["status"]:
                changed.append(f"status: {row['status']} -> {status}")
            if current_step:
                changed.append(f"step: {current_step[:500]}")
            if summary:
                changed.append(f"summary: {summary[:1000]}")
            if changed:
                conn.execute(
                    "INSERT INTO task_events(task_id,event_type,message,created_at) VALUES(?,?,?,?)",
                    (task_id, "progress", " | ".join(changed), now),
                )
        return f"Updated task {task_id}: {new_status}"

    @mcp.tool()
    def agent_log_task_event(task_id: str, event_type: str, message: str) -> str:
        """Append an audit event to a task without changing its status."""
        event_type = (event_type or "note").strip()[:60]
        message = (message or "").strip()[:4000]
        if not message:
            return "Event message cannot be empty."
        with connect() as conn:
            if not task_exists(conn, task_id):
                return f"Task not found: {task_id}"
            conn.execute(
                "INSERT INTO task_events(task_id,event_type,message,created_at) VALUES(?,?,?,?)",
                (task_id, event_type, message, _now()),
            )
            conn.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (_now(), task_id))
        return f"Logged {event_type} event for task {task_id}."

    @mcp.tool()
    def agent_get_task(task_id: str) -> str:
        """Get a task, its latest progress and its recent audit events."""
        with connect() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task:
                return f"Task not found: {task_id}"
            events = conn.execute(
                "SELECT event_type,message,created_at FROM task_events WHERE task_id=? ORDER BY event_id DESC LIMIT 30",
                (task_id,),
            ).fetchall()
            approvals = conn.execute(
                "SELECT approval_id,action,reason,status,created_at FROM approvals WHERE task_id=? ORDER BY created_at DESC LIMIT 10",
                (task_id,),
            ).fetchall()
        result = dict(task)
        result["events"] = [dict(x) for x in events]
        result["approvals"] = [dict(x) for x in approvals]
        return json.dumps(result, indent=2, ensure_ascii=False)

    @mcp.tool()
    def agent_list_tasks(limit: int = 10) -> str:
        """List recent EasyGPT tasks, newest first."""
        limit = max(1, min(int(limit), 50))
        with connect() as conn:
            rows = conn.execute(
                "SELECT task_id,title,status,current_step,updated_at FROM tasks ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return json.dumps([dict(x) for x in rows], indent=2, ensure_ascii=False)

    @mcp.tool()
    def agent_request_approval(task_id: str, action: str, reason: str) -> str:
        """Record a pending approval. EasyGPT must obtain explicit user consent in chat before resolving it."""
        action = (action or "").strip()[:500]
        reason = (reason or "").strip()[:1500]
        if not action or not reason:
            return "Both action and reason are required."
        approval_id = uuid.uuid4().hex[:12]
        now = _now()
        with connect() as conn:
            if not task_exists(conn, task_id):
                return f"Task not found: {task_id}"
            conn.execute(
                "INSERT INTO approvals(approval_id,task_id,action,reason,status,created_at) VALUES(?,?,?,?,?,?)",
                (approval_id, task_id, action, reason, "pending", now),
            )
            conn.execute(
                "INSERT INTO task_events(task_id,event_type,message,created_at) VALUES(?,?,?,?)",
                (task_id, "approval_requested", f"{approval_id}: {action} — {reason}", now),
            )
            conn.execute(
                "UPDATE tasks SET status='waiting_approval',updated_at=? WHERE task_id=?",
                (now, task_id),
            )
        return f"Approval {approval_id} is pending for task {task_id}. Do not perform the action until the user explicitly approves it."

    @mcp.tool()
    def agent_resolve_approval(task_id: str, approval_id: str, decision: str) -> str:
        """Record an approval decision after the user explicitly responds in chat. Decision: approved or rejected."""
        if decision not in {"approved", "rejected"}:
            return "Decision must be approved or rejected."
        now = _now()
        with connect() as conn:
            row = conn.execute(
                "SELECT status FROM approvals WHERE approval_id=? AND task_id=?",
                (approval_id, task_id),
            ).fetchone()
            if not row:
                return "Approval request not found."
            if row["status"] != "pending":
                return f"Approval already resolved as {row['status']}."
            conn.execute(
                "UPDATE approvals SET status=?,resolved_at=? WHERE approval_id=?",
                (decision, now, approval_id),
            )
            conn.execute(
                "INSERT INTO task_events(task_id,event_type,message,created_at) VALUES(?,?,?,?)",
                (task_id, "approval_" + decision, f"Approval {approval_id} {decision}.", now),
            )
            conn.execute(
                "UPDATE tasks SET status=?,updated_at=? WHERE task_id=?",
                ("running" if decision == "approved" else "planned", now, task_id),
            )
        return f"Approval {approval_id}: {decision}."

    @mcp.tool()
    def agent_assess_command(command: str) -> str:
        """Classify a shell command before execution; this is advisory and does not execute it."""
        raw = (command or "").strip()
        if not raw:
            return "Command is empty."
        lower = raw.lower()
        high_patterns = [
            r"\b(format|diskpart|shutdown|restart-computer|stop-computer)\b",
            r"\b(remove-item|rmdir|rd|del|erase)\b",
            r"\b(drop\s+database|drop\s+table|truncate\s+table)\b",
            r"\b(git\s+push\s+.*--force|git\s+reset\s+--hard)\b",
            r"\b(reg\s+delete|bcdedit|cipher\s+/w)\b",
        ]
        medium_patterns = [
            r"\b(npm\s+(i|install|uninstall)|pip\s+install|flutter\s+pub\s+add)\b",
            r"\b(git\s+(commit|push|merge|rebase|reset|clean))\b",
            r"\b(winget\s+install|choco\s+install)\b",
            r"\b(\w+\s+--force)\b",
        ]
        if any(re.search(p, lower) for p in high_patterns):
            level, approval = "high", True
        elif any(re.search(p, lower) for p in medium_patterns):
            level, approval = "medium", True
        else:
            level, approval = "low", False
        return json.dumps({
            "risk": level,
            "requires_user_approval": approval,
            "command": raw[:1000],
            "note": "This assessment is advisory. The chat agent must enforce approval before execution; shell commands can have side effects."
        }, indent=2)


    # Phase 2: durable ordered plans and step state.
    def ensure_step_schema() -> None:
        with connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_steps (
                    step_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    step_order INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    result TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(task_id, step_order),
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                )
            """)
    ensure_step_schema()

    @mcp.tool()
    def agent_set_plan(task_id: str, plan_json: str) -> str:
        """Save an ordered task plan. plan_json must be a JSON array of {title, description?} objects; maximum 30 steps."""
        try:
            plan = json.loads(plan_json)
        except Exception:
            return "Invalid plan_json: expected a JSON array."
        if not isinstance(plan, list) or not plan or len(plan) > 30:
            return "Plan must contain between 1 and 30 steps."
        normalized = []
        for item in plan:
            if not isinstance(item, dict) or not str(item.get("title", "")).strip():
                return "Every step must be an object with a non-empty title."
            normalized.append((
                str(item["title"]).strip()[:200],
                str(item.get("description", "")).strip()[:1500],
            ))
        now = _now()
        with connect() as conn:
            if not task_exists(conn, task_id):
                return f"Task not found: {task_id}"
            started = conn.execute(
                "SELECT COUNT(*) FROM task_steps WHERE task_id=? AND status!='pending'",
                (task_id,),
            ).fetchone()[0]
            if started:
                return "Plan cannot be replaced after a step has started. Add or update steps individually instead."
            conn.execute("DELETE FROM task_steps WHERE task_id=?", (task_id,))
            conn.executemany(
                """INSERT INTO task_steps(task_id,step_order,title,description,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                [(task_id, i + 1, title, desc, "pending", now, now)
                 for i, (title, desc) in enumerate(normalized)],
            )
            conn.execute(
                "INSERT INTO task_events(task_id,event_type,message,created_at) VALUES(?,?,?,?)",
                (task_id, "plan_created", f"Saved a plan with {len(normalized)} steps.", now),
            )
            conn.execute(
                "UPDATE tasks SET status='planned',current_step=?,updated_at=? WHERE task_id=?",
                (normalized[0][0], now, task_id),
            )
        return f"Saved {len(normalized)} ordered steps for task {task_id}."

    @mcp.tool()
    def agent_get_steps(task_id: str) -> str:
        """Return all ordered steps for a task with their status and results."""
        with connect() as conn:
            if not task_exists(conn, task_id):
                return f"Task not found: {task_id}"
            rows = conn.execute(
                """SELECT step_id,step_order,title,description,status,result,created_at,updated_at
                   FROM task_steps WHERE task_id=? ORDER BY step_order""",
                (task_id,),
            ).fetchall()
        return json.dumps([dict(row) for row in rows], indent=2, ensure_ascii=False)

    @mcp.tool()
    def agent_update_step(task_id: str, step_id: int, status: str, result: str = "") -> str:
        """Update a plan step. Status must be pending, running, completed, failed, or blocked."""
        allowed = {"pending", "running", "completed", "failed", "blocked"}
        if status not in allowed:
            return "Invalid status. Use pending, running, completed, failed, or blocked."
        now = _now()
        with connect() as conn:
            row = conn.execute(
                "SELECT title FROM task_steps WHERE task_id=? AND step_id=?",
                (task_id, int(step_id)),
            ).fetchone()
            if not row:
                return "Step not found for this task."
            conn.execute(
                "UPDATE task_steps SET status=?,result=?,updated_at=? WHERE task_id=? AND step_id=?",
                (status, (result or "")[:4000], now, task_id, int(step_id)),
            )
            conn.execute(
                "INSERT INTO task_events(task_id,event_type,message,created_at) VALUES(?,?,?,?)",
                (task_id, "step_" + status, f"Step {step_id} ({row['title']}): {status}. {(result or '')[:800]}", now),
            )
            if status == "running":
                conn.execute(
                    "UPDATE tasks SET status='running',current_step=?,updated_at=? WHERE task_id=?",
                    (row["title"], now, task_id),
                )
            elif status in {"failed", "blocked"}:
                conn.execute(
                    "UPDATE tasks SET status=CASE WHEN ?='failed' THEN 'failed' ELSE 'waiting_approval' END,updated_at=? WHERE task_id=?",
                    (status, now, task_id),
                )
            elif status == "completed":
                next_row = conn.execute(
                    "SELECT title FROM task_steps WHERE task_id=? AND status='pending' ORDER BY step_order LIMIT 1",
                    (task_id,),
                ).fetchone()
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM task_steps WHERE task_id=? AND status!='completed'",
                    (task_id,),
                ).fetchone()[0]
                if remaining == 0:
                    conn.execute(
                        "UPDATE tasks SET status='completed',current_step='All plan steps completed',updated_at=? WHERE task_id=?",
                        (now, task_id),
                    )
                elif next_row:
                    conn.execute(
                        "UPDATE tasks SET status='running',current_step=?,updated_at=? WHERE task_id=?",
                        (next_row["title"], now, task_id),
                    )
        return f"Updated step {step_id}: {status}."

    @mcp.tool()
    def agent_next_step(task_id: str) -> str:
        """Return the next pending step without executing it."""
        with connect() as conn:
            if not task_exists(conn, task_id):
                return f"Task not found: {task_id}"
            row = conn.execute(
                """SELECT step_id,step_order,title,description FROM task_steps
                   WHERE task_id=? AND status='pending' ORDER BY step_order LIMIT 1""",
                (task_id,),
            ).fetchone()
            blocked = conn.execute(
                "SELECT step_id,title,status FROM task_steps WHERE task_id=? AND status IN ('failed','blocked') ORDER BY step_order LIMIT 1",
                (task_id,),
            ).fetchone()
        if blocked:
            return json.dumps({"state": "blocked", "step": dict(blocked)}, indent=2)
        if not row:
            return json.dumps({"state": "complete", "message": "No pending steps remain."}, indent=2)
        return json.dumps({"state": "ready", "next_step": dict(row)}, indent=2)
