# Hill Climb

**CI for agent prompt changes.** Mock enterprise connectors. Eval your agent. Know your prompt change didn't break something.

Software teams build agents that connect to Gmail, Google Drive, Calendar, Outlook, Slack, HubSpot, and more. Those agents break: malformatted JSON, hallucinated tool calls, wrong parameters. Teams patch the system prompt, but **how do you know the fix didn't break something else?**

Hill Climb gives you:

1. **Mock connector library** with 10 enterprise toolkits, 60+ tools, and stateful SQLite backing
2. **Eval task definitions** for common connector workflows (create event, send email, read spreadsheet, multi-service orchestration)
3. **A hill-climbing loop** that detects failures, creates evals, measures baselines, and compares revisions

## Quick Start

### Option 1: Docker (30 seconds)

```bash
docker run -p 8081:8081 ghcr.io/samuelchien/hillclimb-mocks
```

Point your agent's tool API base URL to `http://localhost:8081` and every tool call lands in a local SQLite database you can inspect.

### Option 2: Python

```bash
pip install fastapi uvicorn
python mock-services/app.py
```

### Option 3: npm (includes CLI + eval runner)

```bash
npm install -g hillclimb
hillclimb quickstart
```

## Try the Demo (2 minutes)

```bash
git clone https://github.com/SamuelChien/hillclimb.git
cd hillclimb
pip install fastapi uvicorn

# Demo 1: CRM workflow (seed data, execute 5 tool calls, verify state)
python mock-services/app.py &
bash demo/demo.sh

# Demo 2: Agent eval suite (4 scenarios with assertions)
bash demo/agent-eval-demo.sh
```

Demo 1 shows the mock connector in action: seed CRM leads, search Drive, read files, send email, create calendar event, update spreadsheet, verify everything via the audit log.

Demo 2 shows the eval loop: run 4 scenarios (email, calendar, sheets, multi-tool), check assertions, get pass/fail results.

### The CI Loop (what you actually want)

```bash
# 1. Save baseline BEFORE your prompt change
bash demo/compare.sh baseline

# 2. Make your prompt change (edit system prompt, tool description, skill, etc.)

# 3. Run revision AFTER your change
bash demo/compare.sh revision
```

Output:

```
Hill Climb Comparison Report
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Overall
  ┌──────────────┬──────────┬──────────┬────────┐
  │ Metric       │ Baseline │ Revision │ Delta  │
  ├──────────────┼──────────┼──────────┼────────┤
  │ Pass rate    │   75.0%  │  100.0%  │ +25.0% │
  │ Passed       │    3/4   │    4/4   │    +1  │
  └──────────────┴──────────┴──────────┴────────┘

  Per Scenario
  ┌──────────────────┬──────────┬──────────┬───────────┐
  │ Scenario         │ Baseline │ Revision │ Status    │
  ├──────────────────┼──────────┼──────────┼───────────┤
  │ email_send       │ PASS     │ PASS     │ STABLE    │
  │ calendar_create  │ FAIL     │ PASS     │ FIXED     │
  │ sheet_create     │ PASS     │ PASS     │ STABLE    │
  │ multi_tool       │ PASS     │ PASS     │ STABLE    │
  └──────────────────┴──────────┴──────────┴───────────┘

  VERDICT: SAFE TO DEPLOY
  Pass rate improved (75.0% -> 100.0%), no regressions.
```

Exits non-zero on regressions. Drop it in your CI pipeline.

## What's Inside

### Mock Connector Library (`mock-services/`)

A single FastAPI server that implements the Composio v3 REST API surface with stateful SQLite backing. Every tool call mutates real state that you can inspect and assert against.

| Toolkit | Tools | State |
|---------|-------|-------|
| Google Sheets | Create, Read, Append, Update, Clear, Share | SQLite rows |
| Google Drive | Create, Find, Upload, Read, List, Move, Share | SQLite files |
| Google Calendar | Create, List, Find, Delete | SQLite events |
| Gmail | Send, Fetch, Draft | SQLite emails |
| Outlook | Send, Fetch, Draft, List Folders | SQLite emails |
| Slack | Send Message, List Channels | Stateless |
| HubSpot | Contacts CRUD, Properties, Objects, Search | Stateless |
| GitHub | List Repos, Create Issue | Stateless |
| Gong | List Calls, Details, Transcript, Users | Stateless |
| Microsoft Teams | Send Message, List Channels/Teams, Create Channel | Stateless |

**Key features:**
- `/seed` endpoint to pre-populate state before each test
- `/reset` endpoint to wipe state between tests
- `/state/*` endpoints to inspect state after tool execution (sheets, emails, calendar, drive, tool calls)
- Full Composio v3 API compatibility (connected_accounts, tools, toolkits, execute)
- Tool call audit log with timing

### Eval Tasks (`tasks/`)

YAML-defined eval scenarios with seed data, user turns, and assertions:

```yaml
id: calendar_event
name: Create a calendar event
turns:
  - role: user
    content: Schedule a team standup for tomorrow at 10am for 30 minutes.
assertions:
  - type: llm_judge
    target: The assistant should use a GOOGLECALENDAR tool call...
tags: [google-calendar, tool-call, create]
```

Included tasks:
- `calendar_event.yaml` - Create calendar events
- `sheet_append.yaml` - Create and populate spreadsheets
- `drive_then_sheet.yaml` - Multi-service orchestration (Drive find + read + Sheets create)
- `email_send.yaml` - Send emails via Gmail/Outlook
- `multi_tool.yaml` - Cross-connector workflows

### CLI (`packages/cli/`)

```bash
hillclimb scan                    # Ingest sessions, detect failure patterns
hillclimb climb <scanId>          # Full hill-climb loop
hillclimb trend                   # Longitudinal improvement tracking
hillclimb impact <skill>          # Measure skill effectiveness
```

## How It Works

```
Your agent sessions (Claude, OpenAI, any LLM)
        |
Hill Climb scans → clusters failures by priority
        |
Investigates root causes (code search, git blame, hypothesis ranking)
        |
Creates eval scenarios with mock connectors
        |
Measures baseline (before your change)
        |
You make the change (prompt, skill, tool description)
        |
Measures revision (after your change)
        |
Compares: did it get better? Did anything regress?
        |
Opens a PR when merge gates pass
```

## API Reference

### Seed State

```bash
curl -X POST http://localhost:8081/seed -H "Content-Type: application/json" -d '{
  "spreadsheets": [{"id": "s1", "title": "Q2 Report"}],
  "sheets": [{"spreadsheet_id": "s1", "rows": [["Product","Revenue"],["Widget","$1000"]]}],
  "drive_files": [{"id": "f1", "name": "report.pdf", "content": "..."}],
  "calendar_events": [{"id": "e1", "summary": "Standup", "start_time": "2026-01-15T10:00:00Z"}]
}'
```

### Inspect State

```bash
curl http://localhost:8081/state/sheets/s1        # Sheet cell data
curl http://localhost:8081/state/tool-calls        # Audit log
curl http://localhost:8081/state/emails             # Sent emails
curl http://localhost:8081/state/calendar           # Calendar events
curl http://localhost:8081/state/drive              # Drive files
```

### Reset State

```bash
curl -X POST http://localhost:8081/reset
```

## Session Ingestion (agentwatch-ai)

For automated failure detection across hundreds of sessions, Hill Climb includes the `agentwatch-ai` CLI:

```bash
npm install -g agentwatch-ai

agentwatch scan --since 7d          # Ingest sessions, detect failure patterns
agentwatch climb <scanId> --live    # Full hill-climb loop with live evals
agentwatch trend                    # Longitudinal improvement tracking
agentwatch impact <skill>           # Measure if a skill fix actually worked
```

agentwatch-ai scans your Claude Code sessions (`~/.claude/projects/`), clusters failures by priority (frequency x severity x confidence x fixability), investigates root causes, generates eval scenarios, and opens PRs when merge gates pass. 22 root-cause categories, 9 evaluators, 7 merge gates, 90 tests.

The mock connector library (this repo) and agentwatch-ai work together: agentwatch finds the failures, Hill Climb mocks let you reproduce and test them.

## Contributing

Every customer's eval tasks and mock connector improvements make the library better for everyone. PRs welcome for:

- New connector toolkits
- New eval task scenarios
- Improved mock fidelity (more realistic responses)
- Bug fixes in tool execution handlers

## License

MIT
