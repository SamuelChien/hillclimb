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

// ---- scan ---------------------------------------------------------------

program
  .command("scan")
  .description("Scan Claude Code sessions for failure patterns")
  .option("--since <duration>", "Time window (e.g. 7d, 24h, 3d)", "7d")
  .option("--project <path>", "Filter to a specific project")
  .option("--json", "Output machine-readable JSON")
  .action(async (opts: { since: string; project?: string; json?: boolean }) => {
    const { scan } = await import("./scan.js");
    const result = await scan({
      since: opts.since,
      project: opts.project,
      json: opts.json,
    });

    if (opts.json) {
      console.log(JSON.stringify(result, null, 2));
      return;
    }

    console.log(`\nHill Climb Session Scan`);
    console.log(`${"=".repeat(50)}`);
    console.log(`Sessions scanned:  ${result.sessionsScanned}`);
    console.log(`Secrets redacted:  ${result.secretsRedacted}`);
    console.log(`Total tool calls:  ${result.totalToolCalls}`);
    console.log(`Total errors:      ${result.totalErrors}`);
    console.log(`Failure clusters:  ${result.clusters.length}`);
    console.log();

    if (result.clusters.length === 0) {
      console.log("No failure patterns detected. Your agent is doing well.");
      return;
    }

    console.log("Top failure clusters (by priority):");
    console.log();
    for (const c of result.clusters.slice(0, 10)) {
      const bar = "█".repeat(Math.min(c.priority, 20));
      console.log(`  [${c.priority.toString().padStart(3)}] ${c.title}`);
      console.log(`        ${bar} ${c.count} occurrences across ${c.sessions.length} sessions`);
      if (c.examples.length > 0) {
        console.log(`        Example: ${c.examples[0].slice(0, 80)}`);
      }
      console.log();
    }

    console.log(`Next: hillclimb generate --cluster ${result.clusters[0]?.id ?? "<cluster-id>"}`);
  });

// ---- generate -----------------------------------------------------------

program
  .command("generate")
  .description("Generate eval task YAML from a failure cluster")
  .option("--cluster <id>", "Cluster ID from scan results")
  .option("--from-scan <file>", "Path to scan result JSON")
  .action(async (opts: { cluster?: string; fromScan?: string }) => {
    if (!opts.fromScan) {
      console.log("Run 'hillclimb scan --json > scan.json' first, then:");
      console.log("  hillclimb generate --from-scan scan.json --cluster <id>");
      return;
    }
    const raw = await readFile(resolve(process.cwd(), opts.fromScan), "utf-8");
    const scanResult = JSON.parse(raw);
    const clusters = scanResult.clusters || [];

    const target = opts.cluster
      ? clusters.find((c: { id: string }) => c.id === opts.cluster)
      : clusters[0];

    if (!target) {
      console.log(`Cluster ${opts.cluster ?? "(none)"} not found.`);
      console.log(`Available: ${clusters.map((c: { id: string }) => c.id).join(", ")}`);
      return;
    }

    const yaml = generateTaskYaml(target);
    console.log(yaml);
    console.log(`\n# Save this to tasks/${target.id}.yaml`);
  });

function generateTaskYaml(cluster: {
  id: string;
  category: string;
  title: string;
  count: number;
  examples: string[];
}): string {
  const lines = [
    `id: ${cluster.id}`,
    `name: "Regression test: ${cluster.title}"`,
    `description: >`,
    `  Auto-generated from ${cluster.count} occurrences of ${cluster.category}.`,
    `  ${cluster.examples[0] ? "Example: " + cluster.examples[0].slice(0, 100) : ""}`,
    ``,
  ];

  switch (cluster.category) {
    case "CONSECUTIVE_BASH":
      lines.push(
        `turns:`,
        `  - role: user`,
        `    content: >`,
        `      Find all TypeScript files that import the "utils" module`,
        `      and list which functions they use.`,
        ``,
        `assertions:`,
        `  - type: llm_judge`,
        `    target: >`,
        `      The agent should use Read or Grep tools, NOT consecutive Bash calls.`,
        `      If the agent uses 4+ Bash calls in a row (grep, find, cat, ls),`,
        `      score 0.0. If it uses Read/Grep appropriately, score 1.0.`,
        `    weight: 3.0`,
        ``,
        `tags: [regression, bash-loop, tool-selection]`,
        `timeout_seconds: 120`
      );
      break;

    case "EDIT_WITHOUT_READ":
    case "FILE_NOT_READ":
      lines.push(
        `turns:`,
        `  - role: user`,
        `    content: >`,
        `      Add a new function called "validateInput" to src/utils.ts`,
        `      that checks if a string is valid JSON.`,
        ``,
        `assertions:`,
        `  - type: llm_judge`,
        `    target: >`,
        `      The agent MUST Read src/utils.ts before attempting to Edit it.`,
        `      If Edit is called without a prior Read on the same file, score 0.0.`,
        `      If Read then Edit, score 1.0.`,
        `    weight: 3.0`,
        ``,
        `tags: [regression, read-before-edit, prerequisite]`,
        `timeout_seconds: 90`
      );
      break;

    case "JSON_MALFORMED":
      lines.push(
        `turns:`,
        `  - role: user`,
        `    content: >`,
        `      Create a Google Sheet with this data and send it via email:`,
        `      Name: Alice, Revenue: $12,000, Status: Active`,
        ``,
        `assertions:`,
        `  - type: llm_judge`,
        `    target: >`,
        `      All tool call inputs must be valid JSON. Check that no tool_use block`,
        `      has malformed JSON in its arguments. Score 1.0 if all valid, 0.0 if any`,
        `      parse errors.`,
        `    weight: 4.0`,
        ``,
        `tags: [regression, json-format, tool-arguments]`,
        `timeout_seconds: 120`
      );
      break;

    default:
      lines.push(
        `turns:`,
        `  - role: user`,
        `    content: >`,
        `      [TODO: Add a user prompt that would trigger this failure pattern]`,
        ``,
        `assertions:`,
        `  - type: llm_judge`,
        `    target: >`,
        `      [TODO: Define what correct behavior looks like for "${cluster.title}"]`,
        `    weight: 3.0`,
        ``,
        `tags: [regression, auto-generated, ${cluster.category.toLowerCase()}]`,
        `timeout_seconds: 120`
      );
  }

  return lines.join("\n");
}

// ---- run (full pipeline) ------------------------------------------------

program
  .command("run")
  .description("Full pipeline: scan sessions → detect failures → generate report")
  .option("--since <duration>", "Time window", "7d")
  .option("--project <path>", "Filter to project")
  .option("--top <n>", "Number of top clusters to report", "5")
  .action(async (opts: { since: string; project?: string; top: string }) => {
    const { scan: scanFn } = await import("./scan.js");
    const topN = parseInt(opts.top);

    console.log("\n[1/3] Scanning sessions...\n");
    const result = await scanFn({ since: opts.since, project: opts.project });

    console.log(`Sessions:  ${result.sessionsScanned}`);
    console.log(`Secrets:   ${result.secretsRedacted} redacted`);
    console.log(`Tools:     ${result.totalToolCalls} calls`);
    console.log(`Errors:    ${result.totalErrors}`);
    console.log(`Clusters:  ${result.clusters.length}`);
    console.log();

    if (result.clusters.length === 0) {
      console.log("No failures detected. Nothing to improve.");
      return;
    }

    console.log(`[2/3] Top ${topN} failure clusters:\n`);
    const top = result.clusters.slice(0, topN);
    for (let i = 0; i < top.length; i++) {
      const c = top[i];
      console.log(`  ${i + 1}. [priority ${c.priority}] ${c.title}`);
      console.log(`     ${c.count}x across ${c.sessions.length} sessions`);
      if (c.examples[0]) {
        console.log(`     Example: ${c.examples[0].slice(0, 80)}`);
      }
      console.log();
    }

    console.log("[3/3] Generated eval tasks:\n");
    for (const c of top) {
      const yaml = generateTaskYaml(c);
      const filename = `tasks/${c.id}.yaml`;
      console.log(`  ${filename}`);
    }

    console.log(`
Next steps:
  1. Review the generated tasks in tasks/
  2. Start mock server:  python mock-services/app.py &
  3. Run baseline:       bash demo/compare.sh baseline --simulate
  4. Make your change
  5. Run revision:       bash demo/compare.sh revision --simulate
  6. Check the verdict:  SAFE TO DEPLOY / REGRESSED
`);
  });

// ---- version info -------------------------------------------------------

program.on("--help", () => {
  console.log("\nPipeline: scan → detect → generate → baseline → revise → compare");
  console.log("Docs: https://samuelchien.github.io/hillclimb/");
});

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------

program.parse();
