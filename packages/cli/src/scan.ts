/**
 * Session ingestion + failure detection.
 *
 * Scans Claude Code session JSONL files, extracts tool calls and errors,
 * clusters failures by type, and outputs prioritized failure reports.
 *
 * Usage:
 *   hillclimb scan                     # scan all sessions from last 7 days
 *   hillclimb scan --since 3d          # last 3 days
 *   hillclimb scan --project /path     # specific project
 *   hillclimb scan --json              # machine-readable output
 */

import { readdir, readFile, stat } from "node:fs/promises";
import { join, basename } from "node:path";
import { homedir } from "node:os";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SessionTurn {
  type: string;
  role?: string;
  toolName?: string;
  toolInput?: Record<string, unknown>;
  isError?: boolean;
  errorMessage?: string;
  text?: string;
  timestamp?: string;
}

interface SessionSummary {
  sessionId: string;
  project: string;
  turnCount: number;
  toolCalls: number;
  errors: number;
  toolNames: string[];
  errorMessages: string[];
  consecutiveBashCount: number;
  editWithoutRead: boolean;
  startedAt?: string;
}

interface FailureCluster {
  id: string;
  category: string;
  title: string;
  count: number;
  severity: number;
  sessions: string[];
  examples: string[];
  priority: number;
}

// ---------------------------------------------------------------------------
// JSONL Parser
// ---------------------------------------------------------------------------

function parseSession(lines: string[], sessionId: string, project: string): SessionSummary {
  const turns: SessionTurn[] = [];
  let consecutiveBash = 0;
  let maxConsecutiveBash = 0;
  let editWithoutRead = false;
  const readFiles = new Set<string>();
  const toolNames: string[] = [];
  const errorMessages: string[] = [];

  for (const line of lines) {
    if (!line.trim()) continue;
    let d: Record<string, unknown>;
    try {
      d = JSON.parse(line);
    } catch {
      continue;
    }

    const type = d.type as string;

    // Parse assistant messages for tool_use blocks
    if (type === "assistant") {
      const msg = d.message as Record<string, unknown> | undefined;
      if (!msg) continue;
      const content = msg.content as Array<Record<string, unknown>> | undefined;
      if (!Array.isArray(content)) continue;

      for (const block of content) {
        const blockType = block.type as string;

        if (blockType === "tool_use") {
          const name = block.name as string;
          const input = block.input as Record<string, unknown> | undefined;
          toolNames.push(name);

          if (name === "Bash") {
            consecutiveBash++;
            maxConsecutiveBash = Math.max(maxConsecutiveBash, consecutiveBash);
          } else {
            consecutiveBash = 0;
          }

          if (name === "Read") {
            const path = input?.file_path as string;
            if (path) readFiles.add(path);
          }
          if (name === "Edit" || name === "Write") {
            const path = input?.file_path as string;
            if (path && !readFiles.has(path)) {
              editWithoutRead = true;
            }
          }

          turns.push({ type: "tool_use", toolName: name, toolInput: input ?? {} });
        }
      }
    }

    // Parse user messages for tool_result blocks (where errors actually live)
    if (type === "user") {
      const msg = d.message as Record<string, unknown> | undefined;
      if (!msg) continue;
      const content = msg.content;
      if (!Array.isArray(content)) continue;

      for (const block of content as Array<Record<string, unknown>>) {
        if (block.type !== "tool_result") continue;

        const isError = block.is_error === true;
        let resultText = block.content as string | Array<Record<string, unknown>>;
        if (Array.isArray(resultText)) {
          resultText = resultText
            .filter((x) => typeof x === "object" && x.type === "text")
            .map((x) => (x as Record<string, string>).text ?? "")
            .join(" ");
        }
        if (typeof resultText !== "string") resultText = "";

        // Detect errors from is_error flag
        if (isError && resultText) {
          errorMessages.push(resultText.slice(0, 200));
        }

        // Detect errors from Bash output content (exit code, traceback, etc.)
        if (!isError && resultText) {
          const lower = resultText.toLowerCase().slice(0, 500);
          if (
            lower.startsWith("exit code ") &&
            !lower.startsWith("exit code 0")
          ) {
            errorMessages.push(resultText.slice(0, 200));
          }
        }

        turns.push({
          type: "tool_result",
          isError,
          errorMessage: isError ? resultText.slice(0, 200) : undefined,
        });
      }
    }
  }

  return {
    sessionId,
    project,
    turnCount: turns.length,
    toolCalls: toolNames.length,
    errors: errorMessages.length,
    toolNames: [...new Set(toolNames)],
    errorMessages,
    consecutiveBashCount: maxConsecutiveBash,
    editWithoutRead,
  };
}

// ---------------------------------------------------------------------------
// Failure Detection
// ---------------------------------------------------------------------------

function detectFailures(sessions: SessionSummary[]): FailureCluster[] {
  const clusters: Map<string, FailureCluster> = new Map();

  for (const s of sessions) {
    // Consecutive Bash (doom loop)
    if (s.consecutiveBashCount >= 4) {
      upsertCluster(clusters, "CONSECUTIVE_BASH", "Bash doom loop (4+ consecutive)", 3, s);
    }

    // Edit without Read
    if (s.editWithoutRead) {
      upsertCluster(clusters, "EDIT_WITHOUT_READ", "Edit/Write without prior Read", 2, s);
    }

    // Tool errors
    for (const err of s.errorMessages) {
      const normalized = normalizeError(err);

      if (err.includes("File has not been read")) {
        upsertCluster(clusters, "FILE_NOT_READ", "File has not been read yet", 2, s, err);
      } else if (err.includes("ENOENT") || err.includes("does not exist")) {
        upsertCluster(clusters, "FILE_NOT_FOUND", "File or path not found", 2, s, err);
      } else if (err.includes("JSON") || err.includes("parse") || err.includes("SyntaxError")) {
        upsertCluster(clusters, "JSON_MALFORMED", "JSON parse or format error", 3, s, err);
      } else if (err.includes("timeout") || err.includes("ETIMEDOUT")) {
        upsertCluster(clusters, "TIMEOUT", "Command or request timeout", 2, s, err);
      } else if (err.includes("permission") || err.includes("EACCES")) {
        upsertCluster(clusters, "PERMISSION_DENIED", "Permission denied", 2, s, err);
      } else if (err.includes("Exit code")) {
        upsertCluster(clusters, "NONZERO_EXIT", "Command exited with non-zero status", 1, s, err);
      } else {
        upsertCluster(clusters, `ERROR_${normalized}`, `Error: ${err.slice(0, 60)}`, 1, s, err);
      }
    }

    // High tool call count with errors (struggling session)
    if (s.toolCalls > 50 && s.errors > 5) {
      upsertCluster(clusters, "STRUGGLING_SESSION", "High tool calls with many errors (>50 calls, >5 errors)", 4, s);
    }
  }

  // Calculate priority scores and sort
  const result = [...clusters.values()].map((c) => ({
    ...c,
    priority: c.count * c.severity,
  }));
  result.sort((a, b) => b.priority - a.priority);
  return result;
}

function upsertCluster(
  clusters: Map<string, FailureCluster>,
  category: string,
  title: string,
  severity: number,
  session: SessionSummary,
  example?: string
) {
  const existing = clusters.get(category);
  if (existing) {
    existing.count++;
    if (!existing.sessions.includes(session.sessionId)) {
      existing.sessions.push(session.sessionId);
    }
    if (example && existing.examples.length < 3) {
      existing.examples.push(example);
    }
  } else {
    clusters.set(category, {
      id: `fc_${category.toLowerCase().replace(/[^a-z0-9]/g, "_")}`,
      category,
      title,
      count: 1,
      severity,
      sessions: [session.sessionId],
      examples: example ? [example] : [],
      priority: 0,
    });
  }
}

function normalizeError(err: string): string {
  return err
    .replace(/\/Users\/[^\s/]+/g, "<user>")
    .replace(/\/tmp\/[^\s]+/g, "<tmp>")
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g, "<uuid>")
    .replace(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/g, "<timestamp>")
    .replace(/line \d+/g, "line <N>")
    .slice(0, 40)
    .replace(/[^a-zA-Z0-9]/g, "_")
    .toUpperCase();
}

// ---------------------------------------------------------------------------
// Scanner
// ---------------------------------------------------------------------------

async function findSessionFiles(projectsDir: string, since?: string): Promise<Array<{ path: string; project: string; sessionId: string }>> {
  const cutoff = since ? parseSince(since) : Date.now() - 7 * 24 * 60 * 60 * 1000;
  const results: Array<{ path: string; project: string; sessionId: string }> = [];

  let projects: string[];
  try {
    projects = await readdir(projectsDir);
  } catch {
    return results;
  }

  for (const project of projects) {
    const projectDir = join(projectsDir, project);
    let files: string[];
    try {
      files = await readdir(projectDir);
    } catch {
      continue;
    }

    for (const file of files) {
      if (!file.endsWith(".jsonl")) continue;
      const filePath = join(projectDir, file);
      try {
        const s = await stat(filePath);
        if (s.mtimeMs >= cutoff) {
          results.push({
            path: filePath,
            project,
            sessionId: basename(file, ".jsonl"),
          });
        }
      } catch {
        continue;
      }
    }
  }

  return results;
}

function parseSince(since: string): number {
  const match = since.match(/^(\d+)([dhm])$/);
  if (!match) return Date.now() - 7 * 24 * 60 * 60 * 1000;
  const [, num, unit] = match;
  const ms = unit === "d" ? 86400000 : unit === "h" ? 3600000 : 60000;
  return Date.now() - parseInt(num) * ms;
}

// ---------------------------------------------------------------------------
// Redaction
// ---------------------------------------------------------------------------

const SECRET_PATTERNS = [
  /(?:sk-|pk_live_|pk_test_)[a-zA-Z0-9]{20,}/g,
  /(?:ghp_|gho_|ghs_|github_pat_)[a-zA-Z0-9_]{20,}/g,
  /AKIA[0-9A-Z]{16}/g,
  /xox[bpas]-[a-zA-Z0-9-]{10,}/g,
  /mongodb\+srv:\/\/[^\s"']+/g,
  /postgres(?:ql)?:\/\/[^\s"']+/g,
  /eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}/g,
  /-----BEGIN (?:RSA |EC )?PRIVATE KEY-----/g,
  /(?:ANTHROPIC|OPENAI|STRIPE|SENDGRID)_[A-Z_]*KEY[=:]\s*["']?[a-zA-Z0-9_-]{20,}/gi,
];

function redactSecrets(text: string): { redacted: string; count: number } {
  let count = 0;
  let redacted = text;
  for (const pattern of SECRET_PATTERNS) {
    redacted = redacted.replace(pattern, () => {
      count++;
      return "<REDACTED>";
    });
  }
  return { redacted, count };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface ScanOptions {
  since?: string;
  project?: string;
  json?: boolean;
}

export interface ScanResult {
  sessionsScanned: number;
  secretsRedacted: number;
  totalToolCalls: number;
  totalErrors: number;
  clusters: FailureCluster[];
  topSessions: SessionSummary[];
}

export async function scan(options: ScanOptions = {}): Promise<ScanResult> {
  const projectsDir = join(homedir(), ".claude", "projects");
  const sessionFiles = await findSessionFiles(projectsDir, options.since);

  if (options.project) {
    const filtered = sessionFiles.filter((f) =>
      f.project.includes(options.project!)
    );
    sessionFiles.length = 0;
    sessionFiles.push(...filtered);
  }

  const sessions: SessionSummary[] = [];
  let totalSecrets = 0;

  for (const { path, project, sessionId } of sessionFiles) {
    try {
      const raw = await readFile(path, "utf-8");
      const { redacted, count } = redactSecrets(raw);
      totalSecrets += count;
      const lines = redacted.split("\n");
      const summary = parseSession(lines, sessionId, project);
      if (summary.toolCalls > 0) {
        sessions.push(summary);
      }
    } catch {
      continue;
    }
  }

  const clusters = detectFailures(sessions);

  sessions.sort((a, b) => b.errors - a.errors);

  return {
    sessionsScanned: sessions.length,
    secretsRedacted: totalSecrets,
    totalToolCalls: sessions.reduce((acc, s) => acc + s.toolCalls, 0),
    totalErrors: sessions.reduce((acc, s) => acc + s.errors, 0),
    clusters,
    topSessions: sessions.slice(0, 10),
  };
}
