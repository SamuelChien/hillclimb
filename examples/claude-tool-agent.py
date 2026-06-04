"""Run a Claude agent with enterprise connector tools against Hill Climb mocks.

This is the integration point: Claude makes real tool_use calls, the handler
routes them to the mock server, and you assert on the resulting state.

Usage:
    # Start mock server
    python mock-services/app.py &

    # Run the agent
    python examples/claude-tool-agent.py "Send an email to alice@example.com about the Q2 report"

    # Check what happened
    curl http://localhost:8081/state/emails
    curl http://localhost:8081/state/tool-calls

Requires: pip install anthropic
Uses Claude Code subscription auth (claude CLI must be authenticated).
"""

import json
import os
import subprocess
import sys
from urllib.request import Request, urlopen
from urllib.error import URLError

MOCK_URL = os.environ.get("HILLCLIMB_MOCK_URL", "http://localhost:8081")

TOOLS = [
    {
        "name": "gmail_send_email",
        "description": "Send an email via Gmail. Use when the user asks to send, email, or message someone.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Email body text"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "calendar_create_event",
        "description": "Create a Google Calendar event. Use when the user asks to schedule, book, or create a meeting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Event title"},
                "start": {"type": "string", "description": "Start time in ISO 8601 format"},
                "end": {"type": "string", "description": "End time in ISO 8601 format"},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of attendee email addresses",
                    "default": [],
                },
            },
            "required": ["summary", "start", "end"],
        },
    },
    {
        "name": "sheets_create",
        "description": "Create a new Google Sheets spreadsheet. Use when the user asks to create a spreadsheet or sheet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Spreadsheet title"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "sheets_append_rows",
        "description": "Append rows of data to a Google Sheet. Use after creating a sheet to add data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string", "description": "ID of the spreadsheet"},
                "rows": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                    "description": "2D array of row data",
                },
            },
            "required": ["spreadsheet_id", "rows"],
        },
    },
    {
        "name": "drive_find_file",
        "description": "Search for files on Google Drive by name or query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "drive_read_file",
        "description": "Read the content of a file from Google Drive.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "ID of the file to read"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "slack_send_message",
        "description": "Send a message to a Slack channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel name or ID"},
                "text": {"type": "string", "description": "Message text"},
            },
            "required": ["channel", "text"],
        },
    },
]

TOOL_TO_COMPOSIO = {
    "gmail_send_email": "GMAIL_SEND_EMAIL",
    "calendar_create_event": "GOOGLECALENDAR_CREATE_EVENT",
    "sheets_create": "GOOGLESHEETS_CREATE_GOOGLE_SHEET1",
    "sheets_append_rows": "GOOGLESHEETS_APPEND_ROWS",
    "drive_find_file": "GOOGLEDRIVE_FIND_FILE",
    "drive_read_file": "GOOGLEDRIVE_GET_FILE_CONTENT",
    "slack_send_message": "SLACK_SEND_MESSAGE",
}


def mock_execute(tool_name: str, arguments: dict) -> dict:
    composio_slug = TOOL_TO_COMPOSIO.get(tool_name)
    if not composio_slug:
        return {"error": f"Unknown tool: {tool_name}"}

    url = f"{MOCK_URL}/api/v3/tools/{composio_slug}/execute"
    data = json.dumps({"arguments": arguments}).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")

    try:
        with urlopen(req) as resp:
            result = json.loads(resp.read())
            return result.get("data", result)
    except URLError as e:
        return {"error": str(e)}


def run_agent(prompt: str, max_turns: int = 10):
    try:
        import anthropic
    except ImportError:
        print("pip install anthropic")
        print("Or use: claude -p with tool definitions")
        sys.exit(1)

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": prompt}]

    print(f"\nUser: {prompt}\n")

    for turn in range(max_turns):
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            tools=TOOLS,
            messages=messages,
        )

        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        tool_uses = [b for b in assistant_content if b.type == "tool_use"]
        text_blocks = [b for b in assistant_content if b.type == "text"]

        for tb in text_blocks:
            if tb.text.strip():
                print(f"Agent: {tb.text}")

        if not tool_uses:
            break

        tool_results = []
        for tu in tool_uses:
            print(f"  Tool: {tu.name}({json.dumps(tu.input, indent=None)[:100]})")
            result = mock_execute(tu.name, tu.input)
            print(f"  Result: {json.dumps(result, indent=None)[:100]}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(result),
            })

        messages.append({"role": "user", "content": tool_results})

    print(f"\nCompleted in {turn + 1} turn(s).")
    return messages


def main():
    if len(sys.argv) < 2:
        print("Usage: python examples/claude-tool-agent.py '<prompt>'")
        print()
        print("Examples:")
        print("  python examples/claude-tool-agent.py 'Send an email to bob@acme.com about the Q2 report'")
        print("  python examples/claude-tool-agent.py 'Create a spreadsheet called Budget 2026 with 3 sample rows'")
        print("  python examples/claude-tool-agent.py 'Schedule a team standup for tomorrow at 10am'")
        print()
        print("The agent makes real tool calls against the Hill Climb mock server.")
        print(f"Mock server: {MOCK_URL}")
        sys.exit(0)

    # Check mock server
    try:
        req = Request(f"{MOCK_URL}/health")
        with urlopen(req) as resp:
            pass
    except URLError:
        print(f"Mock server not running at {MOCK_URL}")
        print("Start it: python mock-services/app.py &")
        sys.exit(1)

    # Reset state
    req = Request(f"{MOCK_URL}/reset", method="POST")
    urlopen(req)

    prompt = " ".join(sys.argv[1:])
    run_agent(prompt)

    # Show what happened
    print("\n--- State after agent run ---")
    for resource in ["tool-calls", "emails", "calendar"]:
        req = Request(f"{MOCK_URL}/state/{resource}")
        with urlopen(req) as resp:
            data = json.loads(resp.read())
            if data and (not isinstance(data, list) or len(data) > 0):
                print(f"\n{resource}:")
                print(json.dumps(data, indent=2)[:500])


if __name__ == "__main__":
    main()
