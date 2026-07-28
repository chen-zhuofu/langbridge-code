import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import path from "node:path";
import { fileURLToPath } from "node:url";
import fs from "node:fs";
import { rgPath } from "@vscode/ripgrep";

import type { ClientMessage, EngineEvent } from "./protocol.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// tui/dist -> repo root
const REPO_ROOT = path.resolve(HERE, "..", "..");

function pythonExecutable(): string {
  const override = process.env.LANGBRIDGE_PYTHON;
  if (override) return override;
  const venv = process.platform === "win32"
    ? path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
    : path.join(REPO_ROOT, ".venv", "bin", "python");
  if (fs.existsSync(venv)) return venv;
  return "python3";
}

/** Spawns the Python engine and exchanges JSONL messages with it. */
export class Bridge extends EventEmitter {
  private child: ChildProcessWithoutNullStreams;
  private buffer = "";
  // Engine events that arrived before the UI attached its listener.
  private backlog: EngineEvent[] = [];

  override on(event: string, listener: (...args: any[]) => void): this {
    super.on(event, listener);
    if (event === "event" && this.backlog.length > 0) {
      const pending = this.backlog;
      this.backlog = [];
      for (const item of pending) this.emit("event", item);
    }
    return this;
  }

  constructor() {
    super();
    const bridgeModule = process.env.LANGBRIDGE_BRIDGE_MODULE || "langbridge_code.ui.bridge";
    this.child = spawn(pythonExecutable(), ["-m", bridgeModule], {
      cwd: process.cwd(),
      env: {
        ...process.env,
        // The Python engine always receives the platform-specific ripgrep
        // binary installed with the TUI. It still has a Python fallback for
        // headless launches where the TypeScript package is not present.
        LANGBRIDGE_RG_PATH: rgPath,
        PYTHONPATH: [path.join(REPO_ROOT, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.child.stdout.setEncoding("utf-8");
    this.child.stdout.on("data", (chunk: string) => this.onData(chunk));
    this.child.stderr.setEncoding("utf-8");
    this.child.stderr.on("data", (chunk: string) => {
      const text = chunk.trim();
      if (text) this.emit("stderr", text);
    });
    this.child.on("exit", (code) => this.emit("exit", code ?? 0));
  }

  private onData(chunk: string): void {
    this.buffer += chunk;
    let index = this.buffer.indexOf("\n");
    while (index >= 0) {
      const line = this.buffer.slice(0, index).trim();
      this.buffer = this.buffer.slice(index + 1);
      if (line) {
        try {
          const event = JSON.parse(line) as EngineEvent;
          if (process.env.LANGBRIDGE_TUI_DEBUG) {
            fs.appendFileSync(process.env.LANGBRIDGE_TUI_DEBUG, `recv ${line}\n`);
          }
          if (this.listenerCount("event") === 0) this.backlog.push(event);
          else this.emit("event", event);
        } catch {
          this.emit("stderr", line);
        }
      }
      index = this.buffer.indexOf("\n");
    }
  }

  send(message: ClientMessage): void {
    if (process.env.LANGBRIDGE_TUI_DEBUG) {
      fs.appendFileSync(process.env.LANGBRIDGE_TUI_DEBUG, `send ${JSON.stringify(message)}\n`);
    }
    if (this.child.stdin.writable) {
      this.child.stdin.write(JSON.stringify(message) + "\n");
    }
  }

  quit(): void {
    this.send({ type: "quit" });
    setTimeout(() => {
      if (!this.child.killed) this.child.kill("SIGTERM");
    }, 800);
  }
}
