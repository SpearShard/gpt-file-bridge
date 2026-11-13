const vscode = require("vscode");
const http = require("http");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const os = require("os");

const PORT = 47831;
const terminals = new Map();
let server;
let descriptorPath;

function idFor(terminal) {
  for (const [id, entry] of terminals) if (entry === terminal) return id;
  const id = crypto.randomUUID();
  terminals.set(id, terminal);
  return id;
}
function json(res, status, data) {
  const body = JSON.stringify(data);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body), "cache-control": "no-store" });
  res.end(body);
}
function readBody(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", chunk => { raw += chunk; if (raw.length > 16384) reject(new Error("Request too large")); });
    req.on("end", () => { try { resolve(raw ? JSON.parse(raw) : {}); } catch { reject(new Error("Invalid JSON")); } });
    req.on("error", reject);
  });
}
function listTerminals() {
  return vscode.window.terminals.map(t => ({
    id: idFor(t), name: t.name, processId: null,
    state: t.exitStatus ? "exited" : "available",
    isActive: vscode.window.activeTerminal === t,
    shellIntegration: Boolean(t.shellIntegration)
  }));
}
async function executeAndCapture(terminal, command, timeoutMs = 30000) {
  if (typeof command !== "string" || !command.trim() || command.length > 10000) {
    throw new Error("command must be 1-10000 non-whitespace characters");
  }
  const shell = terminal.shellIntegration;
  if (!shell || typeof shell.executeCommand !== "function") {
    const error = new Error("Shell integration is not ready for this terminal. Open a fresh terminal and wait for its prompt, then retry.");
    error.statusCode = 409;
    throw error;
  }
  const execution = shell.executeCommand(command);
  const capture = (async () => {
    let text = "";
    for await (const chunk of execution.read()) {
      text += chunk;
      if (text.length > 65536) {
        text = text.slice(-65536);
      }
    }
    const exitCode = await execution.exitCode;
    return { output: text, exitCode };
  })();
  let timeout;
  try {
    return await Promise.race([
      capture,
      new Promise((_, reject) => {
        timeout = setTimeout(() => {
          const error = new Error("Command is still running; output capture timed out.");
          error.statusCode = 408;
          reject(error);
        }, Math.min(Math.max(timeoutMs, 1000), 120000));
      })
    ]);
  } finally {
    clearTimeout(timeout);
  }
}
function activate(context) {
  const output = vscode.window.createOutputChannel("EasyGPT Bridge");
  context.subscriptions.push(output);
  for (const t of vscode.window.terminals) idFor(t);
  context.subscriptions.push(vscode.window.onDidOpenTerminal(t => idFor(t)));
  context.subscriptions.push(vscode.window.onDidCloseTerminal(t => {
    for (const [id, value] of terminals) if (value === t) terminals.delete(id);
  }));

  const token = crypto.randomBytes(32).toString("hex");
  descriptorPath = path.join(os.tmpdir(), "easygpt-vscode-bridge.json");
  const descriptor = { host: "127.0.0.1", port: PORT, token, pid: process.pid, version: "0.2.0" };
  fs.writeFileSync(descriptorPath, JSON.stringify(descriptor), { mode: 0o600 });

  server = http.createServer(async (req, res) => {
    const remote = req.socket.remoteAddress;
    if (remote !== "127.0.0.1" && remote !== "::1" && remote !== "::ffff:127.0.0.1") return json(res, 403, { error: "localhost only" });
    if (req.headers.authorization !== `Bearer ${token}`) return json(res, 401, { error: "unauthorized" });
    try {
      const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
      if (req.method === "GET" && url.pathname === "/v1/health") return json(res, 200, { ok: true, version: "0.2.0" });
      if (req.method === "GET" && url.pathname === "/v1/terminals") return json(res, 200, { terminals: listTerminals() });
      if (req.method === "POST" && url.pathname === "/v1/terminals") {
        const body = await readBody(req);
        const options = { name: String(body.name || "EasyGPT Terminal") };
        if (body.cwd) options.cwd = String(body.cwd);
        const terminal = vscode.window.createTerminal(options);
        terminal.show(true);
        return json(res, 201, { terminal: { id: idFor(terminal), name: terminal.name } });
      }

      const executeMatch = url.pathname.match(/^\/v1\/terminals\/([a-f0-9-]+)\/execute$/i);
      if (req.method === "POST" && executeMatch) {
        const terminal = terminals.get(executeMatch[1]);
        if (!terminal || !vscode.window.terminals.includes(terminal)) return json(res, 404, { error: "terminal not found" });
        const body = await readBody(req);
        const result = await executeAndCapture(terminal, body.command, Number(body.timeoutMs) || 30000);
        return json(res, 200, { ok: true, terminal: terminal.name, command: body.command, ...result });
      }

      const match = url.pathname.match(/^\/v1\/terminals\/([a-f0-9-]+)\/(focus|send)$/i);
      if (req.method === "POST" && match) {
        const terminal = terminals.get(match[1]);
        if (!terminal || !vscode.window.terminals.includes(terminal)) return json(res, 404, { error: "terminal not found" });
        if (match[2] === "focus") { terminal.show(true); return json(res, 200, { ok: true, focused: terminal.name }); }
        const body = await readBody(req);
        if (typeof body.text !== "string" || !body.text.length || body.text.length > 10000) return json(res, 400, { error: "text must be 1-10000 characters" });
        terminal.sendText(body.text, body.addNewLine === true);
        return json(res, 200, { ok: true, sentTo: terminal.name, addNewLine: body.addNewLine === true });
      }
      return json(res, 404, { error: "not found" });
    } catch (error) {
      return json(res, error.statusCode || 400, { error: String(error.message || error) });
    }
  });
  server.on("error", error => output.appendLine(`HTTP server error: ${error.message}`));
  server.listen(PORT, "127.0.0.1", () => output.appendLine(`Listening on 127.0.0.1:${PORT}; descriptor: ${descriptorPath}`));

  context.subscriptions.push(vscode.commands.registerCommand("easygpt.listTerminals", () => {
    const rows = listTerminals();
    output.appendLine(JSON.stringify(rows, null, 2));
    vscode.window.showInformationMessage(`EasyGPT sees ${rows.length} terminal(s).`);
  }));
  context.subscriptions.push(vscode.commands.registerCommand("easygpt.createTerminal", () => {
    const t = vscode.window.createTerminal({ name: "EasyGPT Terminal" });
    t.show(true);
    vscode.window.showInformationMessage("Created an EasyGPT terminal.");
  }));
  context.subscriptions.push({ dispose: () => {
    if (server) server.close();
    try { fs.unlinkSync(descriptorPath); } catch {}
  }});
}
function deactivate() {
  if (server) server.close();
  try { if (descriptorPath) fs.unlinkSync(descriptorPath); } catch {}
}
module.exports = { activate, deactivate };
