#!/usr/bin/env node

import { Command } from "commander";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { parse as parseYaml } from "yaml";

const MOCK_URL = process.env.HILLCLIMB_MOCK_URL ?? "http://localhost:8081";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function api(
  path: string,
  opts: { method?: string; body?: unknown } = {}
): Promise<unknown> {
  const res = await fetch(`${MOCK_URL}${path}`, {
    method: opts.method ?? "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  const ct = res.headers.get("content-type") ?? "";
  return ct.includes("application/json") ? res.json() : res.text();
}

function pretty(data: unknown): void {
  console.log(JSON.stringify(data, null, 2));
}

// ---------------------------------------------------------------------------
// Program
// ---------------------------------------------------------------------------

const program = new Command();

program
  .name("hillclimb")
  .description("Hill Climb eval harness CLI")
  .version("0.1.0");

// ---- mock ----------------------------------------------------------------

program
  .command("mock")
  .description("Start the mock connector server (wraps mock-services/app.py)")
  .option("-p, --port <port>", "Port to listen on", "8081")
  .option(
    "-d, --dir <path>",
    "Path to mock-services directory",
    resolve(process.cwd(), "mock-services")
  )
  .action((opts: { port: string; dir: string }) => {
    const appPath = resolve(opts.dir, "app.py");
    console.log(`Starting mock server on port ${opts.port} ...`);
    console.log(`  python ${appPath}`);

    const child = spawn("python", [appPath], {
      env: { ...process.env, PORT: opts.port },
      stdio: "inherit",
      cwd: opts.dir,
    });

    child.on("error", (err) => {
      console.error(`Failed to start mock server: ${err.message}`);
      process.exit(1);
    });

    child.on("exit", (code) => {
      process.exit(code ?? 0);
    });

    // Forward SIGINT / SIGTERM to child
    const forward = (sig: NodeJS.Signals) => {
      child.kill(sig);
    };
    process.on("SIGINT", forward);
    process.on("SIGTERM", forward);
  });

// ---- seed ----------------------------------------------------------------

program
  .command("seed <file>")
  .description("Seed mock state from a JSON file")
  .action(async (file: string) => {
    const abs = resolve(process.cwd(), file);
    const raw = await readFile(abs, "utf-8");
    const data = JSON.parse(raw);

    const result = await api("/seed", { method: "POST", body: data });
    console.log("Seeded mock state.");
    pretty(result);
  });

// ---- reset ---------------------------------------------------------------

program
  .command("reset")
  .description("Reset all mock state to defaults")
  .action(async () => {
    const result = await api("/reset", { method: "POST" });
    console.log("Mock state reset.");
    pretty(result);
  });

// ---- state ---------------------------------------------------------------

const RESOURCES = ["sheets", "emails", "calendar", "drive", "tool-calls"] as const;

program
  .command("state [resource]")
  .description(
    `Inspect mock state. Resources: ${RESOURCES.join(", ")} (omit for all)`
  )
  .action(async (resource?: string) => {
    if (resource && !RESOURCES.includes(resource as (typeof RESOURCES)[number])) {
      console.error(
        `Unknown resource "${resource}". Choose from: ${RESOURCES.join(", ")}`
      );
      process.exit(1);
    }

    const path = resource ? `/state/${resource}` : "/state";
    const data = await api(path);
    pretty(data);
  });

// ---- test ----------------------------------------------------------------

interface TaskAssertion {
  type: string;
  resource?: string;
  expected?: unknown;
  [key: string]: unknown;
}

interface TaskSpec {
  name?: string;
  description?: string;
  seed?: Record<string, unknown>;
  prompt?: string;
  assertions?: TaskAssertion[];
}

program
  .command("test <task>")
  .description("Run an eval task from a YAML file against the mock server")
  .option("--dry-run", "Parse and display the task without executing")
  .action(async (taskPath: string, opts: { dryRun?: boolean }) => {
    const abs = resolve(process.cwd(), taskPath);
    const raw = await readFile(abs, "utf-8");
    const task: TaskSpec = parseYaml(raw);

    console.log(`Task: ${task.name ?? "(unnamed)"}`);
    if (task.description) console.log(`  ${task.description}`);
    console.log();

    if (opts.dryRun) {
      console.log("--- Parsed task (dry run) ---");
      pretty(task);
      return;
    }

    // Step 1: seed if provided
    if (task.seed) {
      console.log("Seeding mock state ...");
      await api("/seed", { method: "POST", body: task.seed });
      console.log("  done.\n");
    }

    // Step 2: show prompt (agent invocation is out of scope for v0.1)
    if (task.prompt) {
      console.log("Prompt (agent execution not yet wired):");
      console.log(`  "${task.prompt}"\n`);
    }

    // Step 3: check assertions against current state
    if (!task.assertions || task.assertions.length === 0) {
      console.log("No assertions defined. Done.");
      return;
    }

    console.log(`Checking ${task.assertions.length} assertion(s) ...\n`);
    let passed = 0;
    let failed = 0;

    for (const assertion of task.assertions) {
      const label = `[${assertion.type}] ${assertion.resource ?? ""}`;
      try {
        const result = await checkAssertion(assertion);
        if (result.pass) {
          console.log(`  PASS  ${label}`);
          passed++;
        } else {
          console.log(`  FAIL  ${label} -- ${result.reason}`);
          failed++;
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.log(`  ERROR ${label} -- ${msg}`);
        failed++;
      }
    }

    console.log(`\nResults: ${passed} passed, ${failed} failed`);
    if (failed > 0) process.exit(1);
  });

// ---------------------------------------------------------------------------
// Assertion checker (intentionally simple for v0.1)
// ---------------------------------------------------------------------------

interface AssertionResult {
  pass: boolean;
  reason?: string;
}

async function checkAssertion(a: TaskAssertion): Promise<AssertionResult> {
  switch (a.type) {
    case "state_contains": {
      if (!a.resource) return { pass: false, reason: "missing resource field" };
      const data = await api(`/state/${a.resource}`);
      const haystack = JSON.stringify(data);
      const needle = JSON.stringify(a.expected);
      if (haystack.includes(needle)) return { pass: true };
      return {
        pass: false,
        reason: `expected ${needle} not found in ${a.resource}`,
      };
    }

    case "tool_called": {
      const calls = (await api("/state/tool-calls")) as unknown[];
      const name = a.expected as string;
      const found = Array.isArray(calls) && calls.some(
        (c: any) => c.tool === name || c.name === name
      );
      return found
        ? { pass: true }
        : { pass: false, reason: `tool "${name}" was not called` };
    }

    case "state_empty": {
      if (!a.resource) return { pass: false, reason: "missing resource field" };
      const data = await api(`/state/${a.resource}`);
      const empty =
        data === null ||
        data === undefined ||
        (Array.isArray(data) && data.length === 0) ||
        (typeof data === "object" && Object.keys(data as object).length === 0);
      return empty
        ? { pass: true }
        : { pass: false, reason: `${a.resource} is not empty` };
    }

    default:
      return { pass: false, reason: `unknown assertion type "${a.type}"` };
  }
}

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------

program.parse();
