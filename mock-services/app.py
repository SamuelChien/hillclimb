"""Hill Climb Mock Connector Library.

Implements the Composio v3 REST surface with 10 enterprise toolkits and 60+
tools, backed by SQLite. Point your agent's COMPOSIO_BASE_URL here and every
tool call lands in a local database you can inspect and assert against.

API surface:
  GET  /api/v3/connected_accounts
  GET  /api/v3/tools
  GET  /api/v3/tools/{slug}
  POST /api/v3/tools/{slug}/execute
  GET  /api/v3/toolkits/{slug}

State inspection:
  POST /seed          - pre-populate state
  POST /reset         - wipe all state
  GET  /state/sheets  - inspect spreadsheet data
  GET  /state/emails  - inspect sent emails
  GET  /state/calendar - inspect calendar events
  GET  /state/drive   - inspect drive files
  GET  /state/tool-calls - audit log of all tool executions
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [mock] %(message)s")
logger = logging.getLogger("hillclimb-mocks")

DB_PATH = Path("/tmp/mock_state.db")

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS spreadsheets (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT 'benchmark-user',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sheets (
    spreadsheet_id TEXT NOT NULL,
    sheet_name TEXT NOT NULL DEFAULT 'Sheet1',
    row_index INTEGER NOT NULL,
    col_index INTEGER NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (spreadsheet_id, sheet_name, row_index, col_index)
);

CREATE TABLE IF NOT EXISTS drive_files (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    parent_folder_id TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    content TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    attendees TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS emails_sent (
    id TEXT PRIMARY KEY,
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    success INTEGER NOT NULL DEFAULT 1,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_action ON tool_calls(action);
CREATE INDEX IF NOT EXISTS idx_sheets_spreadsheet ON sheets(spreadsheet_id);
"""

_db: Optional[sqlite3.Connection] = None


def get_db() -> sqlite3.Connection:
    global _db
    if _db is None:
        _db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.executescript(SCHEMA)
    return _db


def reset_db():
    global _db
    if _db:
        _db.close()
        _db = None
    DB_PATH.unlink(missing_ok=True)
    get_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_db()
    logger.info("Mock Composio API ready (SQLite: %s)", DB_PATH)
    yield
    if _db:
        _db.close()


app = FastAPI(title="hillclimb-mocks", lifespan=lifespan)


# ═══════════════════════════════════════════════════════════════════════════════
# Health + state inspection (for benchmark assertions)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {"status": "ok", "service": "hillclimb-mocks"}


@app.post("/reset")
async def reset():
    reset_db()
    return {"status": "reset"}


@app.post("/seed")
async def seed(request: Request):
    """Bulk-populate mock state before a benchmark task runs.

    Body: {
      "spreadsheets": [{"id": "s1", "title": "Q2 Report"}],
      "sheets": [{"spreadsheet_id": "s1", "sheet_name": "Sheet1", "rows": [["A","B"],["1","2"]]}],
      "drive_files": [{"id": "f1", "name": "report.pdf", "mime_type": "application/pdf", "content": "..."}],
      "calendar_events": [{"id": "e1", "summary": "Standup", "start_time": "...", "end_time": "..."}],
      "emails": [{"id": "m1", "to_addr": "a@b.com", "subject": "Hi", "body": "Hello"}],
    }
    """
    body = await request.json()
    db = get_db()
    counts: dict[str, int] = {}

    for ss in body.get("spreadsheets", []):
        db.execute("INSERT OR REPLACE INTO spreadsheets (id, title, owner) VALUES (?, ?, ?)",
                   (ss["id"], ss.get("title", "Untitled"), ss.get("owner", "seed")))
        counts["spreadsheets"] = counts.get("spreadsheets", 0) + 1

    for sheet in body.get("sheets", []):
        sid = sheet["spreadsheet_id"]
        sname = sheet.get("sheet_name", "Sheet1")
        for ri, row in enumerate(sheet.get("rows", [])):
            cells = row if isinstance(row, list) else [row]
            for ci, val in enumerate(cells):
                db.execute(
                    "INSERT OR REPLACE INTO sheets (spreadsheet_id, sheet_name, row_index, col_index, value) VALUES (?, ?, ?, ?, ?)",
                    (sid, sname, ri, ci, str(val)),
                )
        counts["sheet_rows"] = counts.get("sheet_rows", 0) + len(sheet.get("rows", []))

    for f in body.get("drive_files", []):
        db.execute("INSERT OR REPLACE INTO drive_files (id, name, mime_type, content, size_bytes) VALUES (?, ?, ?, ?, ?)",
                   (f["id"], f.get("name", ""), f.get("mime_type", "application/octet-stream"),
                    f.get("content", ""), len(f.get("content", ""))))
        counts["drive_files"] = counts.get("drive_files", 0) + 1

    for ev in body.get("calendar_events", []):
        db.execute("INSERT OR REPLACE INTO calendar_events (id, summary, start_time, end_time, attendees) VALUES (?, ?, ?, ?, ?)",
                   (ev["id"], ev.get("summary", ""), ev.get("start_time", ""), ev.get("end_time", ""),
                    json.dumps(ev.get("attendees", []))))
        counts["calendar_events"] = counts.get("calendar_events", 0) + 1

    for em in body.get("emails", []):
        db.execute("INSERT OR REPLACE INTO emails_sent (id, to_addr, subject, body) VALUES (?, ?, ?, ?)",
                   (em["id"], em.get("to_addr", ""), em.get("subject", ""), em.get("body", "")))
        counts["emails"] = counts.get("emails", 0) + 1

    db.commit()
    logger.info("Seeded: %s", counts)
    return {"status": "seeded", "counts": counts}


@app.get("/state/sheets/{spreadsheet_id}")
async def get_sheet_state(spreadsheet_id: str, sheet_name: str = "Sheet1"):
    db = get_db()
    rows = db.execute(
        "SELECT row_index, col_index, value FROM sheets WHERE spreadsheet_id = ? AND sheet_name = ? ORDER BY row_index, col_index",
        (spreadsheet_id, sheet_name),
    ).fetchall()
    cells = {f"{r['row_index']},{r['col_index']}": r["value"] for r in rows}
    return {"spreadsheet_id": spreadsheet_id, "sheet_name": sheet_name, "cells": cells}


@app.get("/state/spreadsheets")
async def list_spreadsheet_state():
    db = get_db()
    rows = db.execute("SELECT * FROM spreadsheets ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.get("/state/tool-calls")
async def get_tool_calls(action: Optional[str] = None, limit: int = 100):
    db = get_db()
    if action:
        rows = db.execute(
            "SELECT * FROM tool_calls WHERE action = ? ORDER BY id DESC LIMIT ?", (action, limit),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM tool_calls ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/state/emails")
async def list_emails():
    db = get_db()
    rows = db.execute("SELECT * FROM emails_sent ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.get("/state/calendar")
async def list_calendar():
    db = get_db()
    rows = db.execute("SELECT * FROM calendar_events ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.get("/state/drive")
async def list_drive():
    db = get_db()
    rows = db.execute("SELECT * FROM drive_files ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# Langfuse / Elasticsearch stubs — nario points LANGFUSE_BASEURL and
# ELASTICSEARCH_URL here. Without these, the Langfuse SDK retries every trace
# against a 404 and floods the logs (drowning real agent errors). Accept and
# swallow so the agent runs cleanly and logs stay debuggable.
# ═══════════════════════════════════════════════════════════════════════════════

@app.api_route("/mock-langfuse/{path:path}", methods=["GET", "POST", "PUT", "PATCH"])
async def mock_langfuse(path: str):
    # Langfuse ingestion expects a 207-style body; a plain 200 satisfies the SDK.
    return {"successes": [], "errors": []}


@app.api_route("/mock-es/{path:path}", methods=["GET", "POST", "PUT", "HEAD", "DELETE"])
async def mock_elasticsearch(path: str):
    # Minimal OK shape for index/search calls during benchmark runs.
    return {"acknowledged": True, "hits": {"total": {"value": 0}, "hits": []}}


# ═══════════════════════════════════════════════════════════════════════════════
# Composio v3 API — connected_accounts
# ═══════════════════════════════════════════════════════════════════════════════

def _account(id: str, toolkit_slug: str, toolkit_name: str, ac_id: str) -> dict:
    """Build a connected account in the exact format the Composio SDK expects (snake_case)."""
    return {
        "id": id,
        "status": "ACTIVE",
        "toolkit": {"slug": toolkit_slug, "name": toolkit_name},
        "auth_config": {"id": ac_id, "is_composio_managed": True, "is_disabled": False},
        "data": None,
        "state": None,
        "status_reason": None,
        "is_disabled": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "test_request_endpoint": "",
    }


CONNECTED_ACCOUNTS = [
    _account("ca_bench_sheets", "googlesheets", "Google Sheets", "ac_bench_sheets"),
    _account("ca_bench_drive", "googledrive", "Google Drive", "ac_bench_drive"),
    _account("ca_bench_calendar", "googlecalendar", "Google Calendar", "ac_bench_cal"),
    _account("ca_bench_gmail", "gmail", "Gmail", "ac_bench_gmail"),
    _account("ca_bench_github", "github", "GitHub", "ac_bench_gh"),
    _account("ca_bench_slack", "slack", "Slack", "ac_bench_slack"),
    _account("ca_bench_hubspot", "hubspot", "HubSpot", "ac_bench_hs"),
    _account("ca_bench_outlook", "outlook", "Outlook", "ac_bench_outlook"),
    _account("ca_bench_teams", "microsoft_teams", "Microsoft Teams", "ac_bench_teams"),
    _account("ca_bench_gong", "gong", "Gong", "ac_bench_gong"),
]


@app.get("/api/v3/connected_accounts")
async def list_connected_accounts(
    user_ids: Optional[str] = Query(None),
    statuses: Optional[str] = Query(None),
):
    items = CONNECTED_ACCOUNTS
    if statuses:
        allowed = set(s.strip().upper() for s in statuses.split(","))
        items = [a for a in items if a["status"] in allowed]
    # Filter by user_ids to match production behavior — only return accounts
    # belonging to the requested user.  The benchmark is single-tenant: the
    # seeded CONNECTED_ACCOUNTS carry no explicit user_id, so they belong to
    # whichever benchmark user the agent resolves (e.g. "benchmark-user-eval").
    # Accounts that DO pin a user_id are still scoped to that user.
    if user_ids:
        requested = set(uid.strip() for uid in user_ids.split(",") if uid.strip())
        items = [
            a for a in items
            if a.get("user_id") is None or a.get("user_id") in requested
        ]
    return {"items": items, "total_pages": 1, "page": 1, "next_cursor": None}


@app.get("/api/v3/connected_accounts/{connection_id}")
async def get_connected_account(connection_id: str):
    for acc in CONNECTED_ACCOUNTS:
        if acc["id"] == connection_id:
            return acc
    return JSONResponse(status_code=404, content={"error": {"message": "not found"}})


# ═══════════════════════════════════════════════════════════════════════════════
# Composio v3 API — tools (list, retrieve, execute)
# ═══════════════════════════════════════════════════════════════════════════════

def _tool(slug: str, toolkit: str, name: str, desc: str, params: dict) -> dict:
    """Build a Composio-format tool schema."""
    return {
        "slug": slug,
        "name": name,
        "description": desc,
        "toolkit": {"slug": toolkit.lower(), "name": toolkit},
        "input_parameters": {
            "type": "object",
            "title": f"{name} Input",
            "properties": params,
            "required": [k for k, v in params.items() if not v.get("default")],
        },
        "output_parameters": {"type": "object", "properties": {}},
        "no_auth": False,
        "deprecated": {"is_deprecated": False},
        "available_versions": ["latest"],
    }


# ─── Google Sheets tools (matching Composio GOOGLESHEETS toolkit) ─────────────
TOOLS: dict[str, dict] = {}

TOOLS["GOOGLESHEETS_CREATE_GOOGLE_SHEET1"] = _tool(
    "GOOGLESHEETS_CREATE_GOOGLE_SHEET1", "GOOGLESHEETS", "Create Google Sheet",
    "Create a new Google Sheets spreadsheet",
    {"title": {"type": "string", "description": "Title of the spreadsheet"}},
)
TOOLS["GOOGLESHEETS_BATCH_GET"] = _tool(
    "GOOGLESHEETS_BATCH_GET", "GOOGLESHEETS", "Batch Get Values",
    "Read values from one or more ranges in a spreadsheet",
    {"spreadsheet_id": {"type": "string"}, "ranges": {"type": "string", "description": "A1 notation ranges"}},
)
TOOLS["GOOGLESHEETS_GET_SPREADSHEET_INFO"] = _tool(
    "GOOGLESHEETS_GET_SPREADSHEET_INFO", "GOOGLESHEETS", "Get Spreadsheet Info",
    "Get metadata about a spreadsheet",
    {"spreadsheet_id": {"type": "string"}},
)
TOOLS["GOOGLESHEETS_GET_SHEET_NAMES"] = _tool(
    "GOOGLESHEETS_GET_SHEET_NAMES", "GOOGLESHEETS", "Get Sheet Names",
    "List all sheet tab names in a spreadsheet",
    {"spreadsheet_id": {"type": "string"}},
)
TOOLS["GOOGLESHEETS_ADD_SHEET"] = _tool(
    "GOOGLESHEETS_ADD_SHEET", "GOOGLESHEETS", "Add Sheet",
    "Add a new sheet tab to a spreadsheet",
    {"spreadsheet_id": {"type": "string"}, "sheet_name": {"type": "string"},
     "rows": {"type": "integer", "default": 1000}, "columns": {"type": "integer", "default": 26}},
)
TOOLS["GOOGLESHEETS_APPEND_ROWS"] = _tool(
    "GOOGLESHEETS_APPEND_ROWS", "GOOGLESHEETS", "Append Rows",
    "Append rows of data to a sheet",
    {"spreadsheet_id": {"type": "string"}, "sheet_name": {"type": "string", "default": "Sheet1"},
     "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "2D array of values"}},
)
TOOLS["GOOGLESHEETS_UPDATE_CELLS"] = _tool(
    "GOOGLESHEETS_UPDATE_CELLS", "GOOGLESHEETS", "Update Cells",
    "Update specific cells in a sheet",
    {"spreadsheet_id": {"type": "string"}, "sheet_name": {"type": "string", "default": "Sheet1"},
     "start_cell": {"type": "string", "description": "e.g. A1"}, "values": {"type": "array", "description": "2D array"}},
)
TOOLS["GOOGLESHEETS_BATCH_UPDATE"] = _tool(
    "GOOGLESHEETS_BATCH_UPDATE", "GOOGLESHEETS", "Batch Update",
    "Execute multiple update operations on a spreadsheet",
    {"spreadsheet_id": {"type": "string"}, "requests": {"type": "array"}},
)
TOOLS["GOOGLESHEETS_CLEAR_SHEET"] = _tool(
    "GOOGLESHEETS_CLEAR_SHEET", "GOOGLESHEETS", "Clear Sheet",
    "Clear all values from a sheet",
    {"spreadsheet_id": {"type": "string"}, "sheet_name": {"type": "string", "default": "Sheet1"}},
)
TOOLS["GOOGLESHEETS_SHARE_SPREADSHEET"] = _tool(
    "GOOGLESHEETS_SHARE_SPREADSHEET", "GOOGLESHEETS", "Share Spreadsheet",
    "Share a spreadsheet with a user",
    {"spreadsheet_id": {"type": "string"}, "email": {"type": "string"}, "role": {"type": "string", "default": "writer"}},
)

# ─── Google Drive tools ───────────────────────────────────────────────────────
TOOLS["GOOGLEDRIVE_CREATE_FILE_FROM_TEXT"] = _tool(
    "GOOGLEDRIVE_CREATE_FILE_FROM_TEXT", "GOOGLEDRIVE", "Create File",
    "Create a file on Google Drive from text content",
    {"name": {"type": "string"}, "content": {"type": "string"}, "mime_type": {"type": "string", "default": "text/plain"}},
)
TOOLS["GOOGLEDRIVE_FIND_FILE"] = _tool(
    "GOOGLEDRIVE_FIND_FILE", "GOOGLEDRIVE", "Find File",
    "Search for files on Google Drive",
    {"query": {"type": "string"}},
)
TOOLS["GOOGLEDRIVE_UPLOAD_FILE"] = _tool(
    "GOOGLEDRIVE_UPLOAD_FILE", "GOOGLEDRIVE", "Upload File",
    "Upload a file to Google Drive",
    {"name": {"type": "string"}, "content": {"type": "string"}, "folder_id": {"type": "string", "default": ""}, "mime_type": {"type": "string", "default": "application/octet-stream"}},
)
TOOLS["GOOGLEDRIVE_GET_FILE_CONTENT"] = _tool(
    "GOOGLEDRIVE_GET_FILE_CONTENT", "GOOGLEDRIVE", "Get File Content",
    "Read the content of a file from Google Drive",
    {"file_id": {"type": "string"}},
)
TOOLS["GOOGLEDRIVE_LIST_FILES"] = _tool(
    "GOOGLEDRIVE_LIST_FILES", "GOOGLEDRIVE", "List Files",
    "List files in a Drive folder",
    {"folder_id": {"type": "string", "default": ""}, "query": {"type": "string", "default": ""}},
)
# create-sheet/create-doc move the new file into the user's scoped folder right
# after creating it; without this slug in the registry the Composio SDK can't
# resolve it ("Unable to retrieve tool with slug GOOGLEDRIVE_MOVE_FILE") and the
# whole create fails. Also used for permission/sharing on created files.
TOOLS["GOOGLEDRIVE_MOVE_FILE"] = _tool(
    "GOOGLEDRIVE_MOVE_FILE", "GOOGLEDRIVE", "Move File",
    "Move a file into a folder (add_parents)",
    {"file_id": {"type": "string"}, "add_parents": {"type": "string"}, "remove_parents": {"type": "string", "default": ""}},
)
TOOLS["GOOGLEDRIVE_CREATE_PERMISSION"] = _tool(
    "GOOGLEDRIVE_CREATE_PERMISSION", "GOOGLEDRIVE", "Share File",
    "Set a sharing permission on a Drive file (applySharingDefault after create)",
    {"file_id": {"type": "string"}, "role": {"type": "string", "default": "reader"}, "type": {"type": "string", "default": "anyone"}},
)

# ─── Google Docs tools ────────────────────────────────────────────────────────
TOOLS["GOOGLEDOCS_CREATE_DOCUMENT_MARKDOWN"] = _tool(
    "GOOGLEDOCS_CREATE_DOCUMENT_MARKDOWN", "GOOGLEDOCS", "Create Document",
    "Create a Google Doc from markdown",
    {"title": {"type": "string"}, "markdown_text": {"type": "string", "default": ""}},
)

# ─── Google Calendar tools ────────────────────────────────────────────────────
TOOLS["GOOGLECALENDAR_CREATE_EVENT"] = _tool(
    "GOOGLECALENDAR_CREATE_EVENT", "GOOGLECALENDAR", "Create Event",
    "Create a new calendar event",
    {"summary": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
     "description": {"type": "string", "default": ""}, "attendees": {"type": "array", "default": []}},
)
TOOLS["GOOGLECALENDAR_LIST_EVENTS"] = _tool(
    "GOOGLECALENDAR_LIST_EVENTS", "GOOGLECALENDAR", "List Events",
    "List upcoming calendar events",
    {"max_results": {"type": "integer", "default": 10}, "time_min": {"type": "string", "default": ""}},
)
# nario's calendar sub-agent reads via GOOGLECALENDAR_EVENTS_LIST / _FIND_EVENT,
# NOT _LIST_EVENTS. Without these slugs the SDK can't resolve them, the agent
# concludes "I can only create/delete events", and creates a junk event for a
# read query. Alias both to the list handler so reads actually work.
TOOLS["GOOGLECALENDAR_EVENTS_LIST"] = _tool(
    "GOOGLECALENDAR_EVENTS_LIST", "GOOGLECALENDAR", "List Events",
    "List events in a calendar",
    {"calendar_id": {"type": "string", "default": "primary"}, "max_results": {"type": "integer", "default": 10},
     "timeMin": {"type": "string", "default": ""}, "timeMax": {"type": "string", "default": ""}},
)
TOOLS["GOOGLECALENDAR_FIND_EVENT"] = _tool(
    "GOOGLECALENDAR_FIND_EVENT", "GOOGLECALENDAR", "Find Event",
    "Find events by text query or time range",
    {"query": {"type": "string", "default": ""}, "calendar_id": {"type": "string", "default": "primary"},
     "timeMin": {"type": "string", "default": ""}, "timeMax": {"type": "string", "default": ""}},
)
TOOLS["GOOGLECALENDAR_DELETE_EVENT"] = _tool(
    "GOOGLECALENDAR_DELETE_EVENT", "GOOGLECALENDAR", "Delete Event",
    "Delete a calendar event",
    {"event_id": {"type": "string"}},
)

# ─── Gmail tools ──────────────────────────────────────────────────────────────
TOOLS["GMAIL_SEND_EMAIL"] = _tool(
    "GMAIL_SEND_EMAIL", "GMAIL", "Send Email",
    "Send an email message",
    {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
     "cc": {"type": "string", "default": ""}, "bcc": {"type": "string", "default": ""}},
)
TOOLS["GMAIL_FETCH_EMAILS"] = _tool(
    "GMAIL_FETCH_EMAILS", "GMAIL", "Fetch Emails",
    "Fetch emails matching a query",
    {"query": {"type": "string", "default": ""}, "max_results": {"type": "integer", "default": 10}},
)
TOOLS["GMAIL_CREATE_DRAFT"] = _tool(
    "GMAIL_CREATE_DRAFT", "GMAIL", "Create Draft",
    "Create an email draft",
    {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
)

# ─── GitHub tools ─────────────────────────────────────────────────────────────
TOOLS["GITHUB_GET_REPOS"] = _tool(
    "GITHUB_GET_REPOS", "GITHUB", "Get Repos",
    "List repositories for the authenticated user",
    {"per_page": {"type": "integer", "default": 30}},
)
TOOLS["GITHUB_CREATE_ISSUE"] = _tool(
    "GITHUB_CREATE_ISSUE", "GITHUB", "Create Issue",
    "Create a new issue in a repository",
    {"owner": {"type": "string"}, "repo": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string", "default": ""}},
)

# ─── Slack tools ──────────────────────────────────────────────────────────────
TOOLS["SLACK_SEND_MESSAGE"] = _tool(
    "SLACK_SEND_MESSAGE", "SLACK", "Send Message",
    "Send a message to a Slack channel",
    {"channel": {"type": "string"}, "text": {"type": "string"}},
)
TOOLS["SLACK_LIST_CHANNELS"] = _tool(
    "SLACK_LIST_CHANNELS", "SLACK", "List Channels",
    "List Slack channels",
    {"limit": {"type": "integer", "default": 100}},
)

# ─── HubSpot tools ────────────────────────────────────────────────────────────
TOOLS["HUBSPOT_LIST_CONTACTS_PAGE"] = _tool(
    "HUBSPOT_LIST_CONTACTS_PAGE", "HUBSPOT", "List Contacts",
    "List contacts from HubSpot CRM",
    {"limit": {"type": "integer", "default": 10}},
)
TOOLS["HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA"] = _tool(
    "HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA", "HUBSPOT", "Search Contacts",
    "Search contacts by criteria",
    {"query": {"type": "string"}},
)
TOOLS["HUBSPOT_CREATE_BATCH_OF_CONTACTS"] = _tool(
    "HUBSPOT_CREATE_BATCH_OF_CONTACTS", "HUBSPOT", "Create Contacts",
    "Create a batch of contacts",
    {"inputs": {"type": "array"}},
)
TOOLS["HUBSPOT_READ_ALL_PROPERTIES_FOR_OBJECT_TYPE"] = _tool(
    "HUBSPOT_READ_ALL_PROPERTIES_FOR_OBJECT_TYPE", "HUBSPOT", "Read All Properties for Object Type",
    "Read all properties for a given object type in HubSpot",
    {"object_type": {"type": "string", "description": "e.g. contacts, companies, deals"}},
)
TOOLS["HUBSPOT_READ_BATCH_OF_CONTACTS_BY_ID_OR_PROPERTIES"] = _tool(
    "HUBSPOT_READ_BATCH_OF_CONTACTS_BY_ID_OR_PROPERTIES", "HUBSPOT", "Read Batch of Contacts",
    "Read a batch of contacts by ID or properties",
    {"inputs": {"type": "array", "description": "List of contact IDs or property filters"},
     "properties": {"type": "array", "default": [], "description": "Properties to return"}},
)
TOOLS["HUBSPOT_READ_CRM_CONTACT_BY_ID"] = _tool(
    "HUBSPOT_READ_CRM_CONTACT_BY_ID", "HUBSPOT", "Read Contact by ID",
    "Read a single CRM contact by its ID",
    {"contact_id": {"type": "string"}, "properties": {"type": "array", "default": []}},
)
TOOLS["HUBSPOT_SEARCH_CRM_OBJECTS_BY_CRITERIA"] = _tool(
    "HUBSPOT_SEARCH_CRM_OBJECTS_BY_CRITERIA", "HUBSPOT", "Search CRM Objects",
    "Search CRM objects by criteria",
    {"object_type": {"type": "string"}, "filter_groups": {"type": "array"},
     "properties": {"type": "array", "default": []}, "limit": {"type": "integer", "default": 10}},
)
TOOLS["HUBSPOT_UPDATE_A_BATCH_OF_CONTACTS"] = _tool(
    "HUBSPOT_UPDATE_A_BATCH_OF_CONTACTS", "HUBSPOT", "Update Batch of Contacts",
    "Update a batch of contacts",
    {"inputs": {"type": "array", "description": "List of {id, properties} objects"}},
)
TOOLS["HUBSPOT_ARCHIVE_BATCH_OF_CONTACTS_BY_ID"] = _tool(
    "HUBSPOT_ARCHIVE_BATCH_OF_CONTACTS_BY_ID", "HUBSPOT", "Archive Batch of Contacts",
    "Archive (delete) a batch of contacts by ID",
    {"inputs": {"type": "array", "description": "List of contact IDs to archive"}},
)
TOOLS["HUBSPOT_CREATE_BATCH_OF_OBJECTS"] = _tool(
    "HUBSPOT_CREATE_BATCH_OF_OBJECTS", "HUBSPOT", "Create Batch of Objects",
    "Create a batch of CRM objects",
    {"object_type": {"type": "string"}, "inputs": {"type": "array"}},
)
TOOLS["HUBSPOT_CREATE_BATCH_OF_PROPERTIES"] = _tool(
    "HUBSPOT_CREATE_BATCH_OF_PROPERTIES", "HUBSPOT", "Create Batch of Properties",
    "Create a batch of properties for an object type",
    {"object_type": {"type": "string"}, "inputs": {"type": "array"}},
)
TOOLS["HUBSPOT_READ_APAGE_OF_OBJECTS_BY_TYPE"] = _tool(
    "HUBSPOT_READ_APAGE_OF_OBJECTS_BY_TYPE", "HUBSPOT", "Read a Page of Objects",
    "Read a page of CRM objects by type",
    {"object_type": {"type": "string"}, "limit": {"type": "integer", "default": 10},
     "after": {"type": "string", "default": ""}, "properties": {"type": "array", "default": []}},
)

# ─── Gong tools ──────────────────────────────────────────────────────────────
TOOLS["GONG_LIST_CALLS"] = _tool(
    "GONG_LIST_CALLS", "GONG", "List Calls",
    "List recorded calls from Gong",
    {"from_date": {"type": "string", "default": ""}, "to_date": {"type": "string", "default": ""},
     "limit": {"type": "integer", "default": 20}},
)
TOOLS["GONG_GET_CALL_DETAILS"] = _tool(
    "GONG_GET_CALL_DETAILS", "GONG", "Get Call Details",
    "Get detailed information about a specific Gong call",
    {"call_id": {"type": "string"}},
)
TOOLS["GONG_GET_CALL_TRANSCRIPT"] = _tool(
    "GONG_GET_CALL_TRANSCRIPT", "GONG", "Get Call Transcript",
    "Get the transcript of a Gong call",
    {"call_id": {"type": "string"}},
)
TOOLS["GONG_LIST_USERS"] = _tool(
    "GONG_LIST_USERS", "GONG", "List Users",
    "List Gong users in the workspace",
    {"limit": {"type": "integer", "default": 50}},
)

# ─── Microsoft Teams tools ───────────────────────────────────────────────────
TOOLS["MICROSOFT_TEAMS_SEND_MESSAGE"] = _tool(
    "MICROSOFT_TEAMS_SEND_MESSAGE", "MICROSOFT_TEAMS", "Send Message",
    "Send a message to a Microsoft Teams channel or chat",
    {"chat_id": {"type": "string"}, "content": {"type": "string"}},
)
TOOLS["MICROSOFT_TEAMS_LIST_CHANNELS"] = _tool(
    "MICROSOFT_TEAMS_LIST_CHANNELS", "MICROSOFT_TEAMS", "List Channels",
    "List channels in a Microsoft Teams team",
    {"team_id": {"type": "string"}},
)
TOOLS["MICROSOFT_TEAMS_LIST_TEAMS"] = _tool(
    "MICROSOFT_TEAMS_LIST_TEAMS", "MICROSOFT_TEAMS", "List Teams",
    "List teams the user is a member of",
    {"limit": {"type": "integer", "default": 50}},
)
TOOLS["MICROSOFT_TEAMS_CREATE_CHANNEL"] = _tool(
    "MICROSOFT_TEAMS_CREATE_CHANNEL", "MICROSOFT_TEAMS", "Create Channel",
    "Create a new channel in a Microsoft Teams team",
    {"team_id": {"type": "string"}, "display_name": {"type": "string"},
     "description": {"type": "string", "default": ""}},
)

# ─── Outlook tools ───────────────────────────────────────────────────────────
TOOLS["OUTLOOK_SEND_EMAIL"] = _tool(
    "OUTLOOK_SEND_EMAIL", "OUTLOOK", "Send Email",
    "Send an email via Outlook",
    {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
     "cc": {"type": "string", "default": ""}, "bcc": {"type": "string", "default": ""}},
)
TOOLS["OUTLOOK_FETCH_EMAILS"] = _tool(
    "OUTLOOK_FETCH_EMAILS", "OUTLOOK", "Fetch Emails",
    "Fetch emails from Outlook inbox",
    {"query": {"type": "string", "default": ""}, "max_results": {"type": "integer", "default": 10}},
)
TOOLS["OUTLOOK_CREATE_DRAFT"] = _tool(
    "OUTLOOK_CREATE_DRAFT", "OUTLOOK", "Create Draft",
    "Create an email draft in Outlook",
    {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
)
TOOLS["OUTLOOK_LIST_FOLDERS"] = _tool(
    "OUTLOOK_LIST_FOLDERS", "OUTLOOK", "List Folders",
    "List mail folders in Outlook",
    {"limit": {"type": "integer", "default": 50}},
)


@app.get("/api/v3/tools")
async def list_tools(
    toolkit_slug: Optional[str] = Query(None),
    tool_slugs: Optional[str] = Query(None),
    limit: int = Query(100),
    important: Optional[str] = Query(None),
    toolkit_versions: Optional[str] = Query(None),
    tags: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    auth_config_ids: Optional[str] = Query(None),
):
    result = []
    for slug, tool in TOOLS.items():
        if toolkit_slug:
            slugs = set(s.strip().upper() for s in toolkit_slug.split(","))
            if tool["toolkit"]["slug"].upper() not in slugs:
                continue
        if tool_slugs:
            allowed = set(s.strip().upper() for s in tool_slugs.split(","))
            if slug.upper() not in allowed:
                continue
        if search and search.lower() not in slug.lower() and search.lower() not in tool["description"].lower():
            continue
        result.append(tool)
    return {"items": result[:limit], "totalPages": 1, "page": 1, "totalItems": len(result)}


@app.get("/api/v3/tools/{slug}")
async def get_tool(slug: str):
    tool = TOOLS.get(slug.upper())
    if tool:
        return tool
    return JSONResponse(status_code=404, content={"error": {"message": f"Tool {slug} not found"}})


@app.get("/api/v3/toolkits/{slug}")
async def get_toolkit(slug: str):
    toolkit_tools = [t for t in TOOLS.values() if t["toolkit"]["slug"].upper() == slug.upper()]
    return {"slug": slug.lower(), "name": slug, "tools": toolkit_tools, "totalTools": len(toolkit_tools)}


# ═══════════════════════════════════════════════════════════════════════════════
# Composio v3 API — tool execution
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/v3/tools/execute/{slug}")
@app.post("/api/v3/tools/{slug}/execute")
async def execute_tool(slug: str, request: Request):
    body = await request.json()
    params = body.get("arguments", body.get("input", body.get("params", {})))
    user_id = body.get("user_id", "unknown")
    start = time.monotonic()
    log_id = f"log_{uuid.uuid4().hex[:12]}"

    logger.info("EXECUTE %s user=%s params=%s", slug, user_id, json.dumps(params, default=str)[:200])

    try:
        result = _dispatch(slug.upper(), params)
        dur = int((time.monotonic() - start) * 1000)
        _record(slug.upper(), params, result, True, dur)
        return {"successful": True, "data": result, "error": None, "log_id": log_id}
    except Exception as e:
        dur = int((time.monotonic() - start) * 1000)
        _record(slug.upper(), params, {"error": str(e)}, False, dur)
        return {"successful": False, "data": None, "error": str(e), "log_id": log_id}


def _record(action: str, params: dict, result: dict, ok: bool, dur: int):
    db = get_db()
    db.execute(
        "INSERT INTO tool_calls (action, input_json, output_json, success, duration_ms) VALUES (?, ?, ?, ?, ?)",
        (action, json.dumps(params, default=str), json.dumps(result, default=str), int(ok), dur),
    )
    db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Action dispatch → SQLite state mutations
# ═══════════════════════════════════════════════════════════════════════════════

def _dispatch(action: str, p: dict) -> dict:
    h = HANDLERS.get(action)
    if h:
        return h(p)
    logger.warning("Unhandled action: %s (returning no-op success)", action)
    return {"message": f"Action {action} completed (mock no-op)", "input": p}


# ─── Google Sheets ────────────────────────────────────────────────────────────

def _sheets_create(p: dict) -> dict:
    sid = f"bench_{uuid.uuid4().hex[:10]}"
    title = p.get("title", "Untitled")
    db = get_db()
    db.execute("INSERT INTO spreadsheets (id, title) VALUES (?, ?)", (sid, title))
    db.commit()
    logger.info("Created spreadsheet %s: %s", sid, title)
    return {"spreadsheetId": sid, "spreadsheetUrl": f"https://docs.google.com/spreadsheets/d/{sid}", "title": title}


def _sheets_batch_get(p: dict) -> dict:
    sid = p.get("spreadsheet_id", "")
    ranges = p.get("ranges", "Sheet1")
    db = get_db()
    rows = db.execute(
        "SELECT row_index, col_index, value FROM sheets WHERE spreadsheet_id = ? ORDER BY row_index, col_index", (sid,),
    ).fetchall()
    grid: dict[int, list[str]] = {}
    for r in rows:
        grid.setdefault(r["row_index"], [])
        while len(grid[r["row_index"]]) <= r["col_index"]:
            grid[r["row_index"]].append("")
        grid[r["row_index"]][r["col_index"]] = r["value"]
    values = [grid[ri] for ri in sorted(grid.keys())]
    return {"spreadsheetId": sid, "valueRanges": [{"range": ranges, "values": values}]}


def _sheets_info(p: dict) -> dict:
    sid = p.get("spreadsheet_id", "")
    db = get_db()
    row = db.execute("SELECT * FROM spreadsheets WHERE id = ?", (sid,)).fetchone()
    names = db.execute("SELECT DISTINCT sheet_name FROM sheets WHERE spreadsheet_id = ?", (sid,)).fetchall()
    sheet_list = [{"title": r["sheet_name"]} for r in names] or [{"title": "Sheet1"}]
    return {"spreadsheetId": sid, "title": row["title"] if row else "Unknown", "sheets": sheet_list}


def _sheets_names(p: dict) -> dict:
    sid = p.get("spreadsheet_id", "")
    db = get_db()
    rows = db.execute("SELECT DISTINCT sheet_name FROM sheets WHERE spreadsheet_id = ?", (sid,)).fetchall()
    return {"spreadsheetId": sid, "sheetNames": [r["sheet_name"] for r in rows] or ["Sheet1"]}


def _sheets_add(p: dict) -> dict:
    return {"spreadsheetId": p.get("spreadsheet_id"), "sheetName": p.get("sheet_name", "Sheet2")}


def _sheets_append(p: dict) -> dict:
    sid = p.get("spreadsheet_id", "")
    sheet = p.get("sheet_name", "Sheet1")
    data_rows = p.get("rows", [])
    db = get_db()
    existing = db.execute("SELECT MAX(row_index) as mr FROM sheets WHERE spreadsheet_id = ? AND sheet_name = ?", (sid, sheet)).fetchone()
    start_row = (existing["mr"] or -1) + 1
    for ri, row in enumerate(data_rows):
        cells = row if isinstance(row, list) else [row]
        for ci, val in enumerate(cells):
            db.execute(
                "INSERT OR REPLACE INTO sheets (spreadsheet_id, sheet_name, row_index, col_index, value) VALUES (?, ?, ?, ?, ?)",
                (sid, sheet, start_row + ri, ci, str(val)),
            )
    db.commit()
    logger.info("Appended %d rows to %s/%s", len(data_rows), sid, sheet)
    return {"spreadsheetId": sid, "updatedRows": len(data_rows), "updatedRange": f"{sheet}!A{start_row + 1}"}


def _sheets_update(p: dict) -> dict:
    sid = p.get("spreadsheet_id", "")
    sheet = p.get("sheet_name", "Sheet1")
    start = p.get("start_cell", "A1")
    values = p.get("values", [])
    col_off = ord(start[0].upper()) - ord("A") if start else 0
    row_off = int(start[1:]) - 1 if start and len(start) > 1 else 0
    db = get_db()
    n = 0
    for ri, row in enumerate(values):
        cells = row if isinstance(row, list) else [row]
        for ci, val in enumerate(cells):
            db.execute(
                "INSERT OR REPLACE INTO sheets (spreadsheet_id, sheet_name, row_index, col_index, value) VALUES (?, ?, ?, ?, ?)",
                (sid, sheet, row_off + ri, col_off + ci, str(val)),
            )
            n += 1
    db.commit()
    return {"spreadsheetId": sid, "updatedCells": n}


def _sheets_batch_update(p: dict) -> dict:
    """Persist updateCells / appendCells requests so writes are verifiable.

    Agents commonly add rows or set a header via batchUpdate(updateCells/
    appendCells) rather than APPEND_ROWS. Previously this was a no-op stub, so
    the row never landed in mock state and outcome checks couldn't see it.
    """
    sid = p.get("spreadsheet_id", "")
    sheet = p.get("sheet_name", "Sheet1")
    db = get_db()
    n = 0
    for req in p.get("requests", []) or []:
        uc = req.get("updateCells") or req.get("appendCells")
        if not uc:
            continue
        rng = uc.get("range") or uc.get("start") or {}
        if "appendCells" in req:
            mr = db.execute(
                "SELECT MAX(row_index) AS mr FROM sheets WHERE spreadsheet_id = ? AND sheet_name = ?",
                (sid, sheet),
            ).fetchone()
            base_row = (mr["mr"] if mr and mr["mr"] is not None else -1) + 1
            base_col = 0
        else:
            base_row = rng.get("startRowIndex", rng.get("rowIndex", 0))
            base_col = rng.get("startColumnIndex", rng.get("columnIndex", 0))
        for ri, row in enumerate(uc.get("rows", []) or []):
            for ci, cell in enumerate(row.get("values", []) or []):
                uev = cell.get("userEnteredValue", {}) or {}
                if not uev:
                    continue
                val = uev.get("stringValue")
                if val is None:
                    val = uev.get("numberValue", uev.get("formulaValue", uev.get("boolValue", "")))
                db.execute(
                    "INSERT OR REPLACE INTO sheets (spreadsheet_id, sheet_name, row_index, col_index, value) VALUES (?, ?, ?, ?, ?)",
                    (sid, sheet, base_row + ri, base_col + ci, str(val)),
                )
                n += 1
    db.commit()
    return {"spreadsheetId": sid, "replies": [{"status": "ok"} for _ in p.get("requests", []) or []], "updatedCells": n}


def _sheets_clear(p: dict) -> dict:
    sid = p.get("spreadsheet_id", "")
    sheet = p.get("sheet_name", "Sheet1")
    db = get_db()
    db.execute("DELETE FROM sheets WHERE spreadsheet_id = ? AND sheet_name = ?", (sid, sheet))
    db.commit()
    return {"spreadsheetId": sid, "clearedRange": f"{sheet}!A:Z"}


def _sheets_share(p: dict) -> dict:
    return {"spreadsheetId": p.get("spreadsheet_id"), "shared_with": p.get("email"), "role": p.get("role", "writer")}


# ─── Google Drive ─────────────────────────────────────────────────────────────

def _drive_create(p: dict) -> dict:
    fid = f"file_{uuid.uuid4().hex[:10]}"
    db = get_db()
    db.execute("INSERT INTO drive_files (id, name, mime_type, content) VALUES (?, ?, ?, ?)",
               (fid, p.get("name", "untitled"), p.get("mime_type", "text/plain"), p.get("content", "")))
    db.commit()
    return {"id": fid, "name": p.get("name"), "webViewLink": f"https://drive.google.com/file/d/{fid}"}


def _drive_find(p: dict) -> dict:
    db = get_db()
    rows = db.execute("SELECT * FROM drive_files WHERE name LIKE ?", (f"%{p.get('query', '')}%",)).fetchall()
    return {"files": [{"id": r["id"], "name": r["name"], "mimeType": r["mime_type"]} for r in rows]}


def _drive_upload(p: dict) -> dict:
    return _drive_create(p)


def _drive_get(p: dict) -> dict:
    db = get_db()
    row = db.execute("SELECT * FROM drive_files WHERE id = ?", (p.get("file_id", ""),)).fetchone()
    if row:
        return {"id": row["id"], "name": row["name"], "content": row["content"], "mimeType": row["mime_type"]}
    return {"error": "File not found"}


def _drive_list(p: dict) -> dict:
    db = get_db()
    rows = db.execute("SELECT * FROM drive_files ORDER BY created_at DESC LIMIT 50").fetchall()
    return {"files": [{"id": r["id"], "name": r["name"], "mimeType": r["mime_type"]} for r in rows]}


# ─── Google Docs ──────────────────────────────────────────────────────────────

def _docs_create(p: dict) -> dict:
    did = f"doc_{uuid.uuid4().hex[:10]}"
    db = get_db()
    db.execute("INSERT INTO drive_files (id, name, mime_type, content) VALUES (?, ?, ?, ?)",
               (did, p.get("title", "Untitled"), "application/vnd.google-apps.document", p.get("markdown_text", "")))
    db.commit()
    return {"documentId": did, "title": p.get("title"), "documentUrl": f"https://docs.google.com/document/d/{did}"}


# ─── Google Calendar ──────────────────────────────────────────────────────────

def _cal_create(p: dict) -> dict:
    eid = f"evt_{uuid.uuid4().hex[:10]}"
    db = get_db()
    db.execute("INSERT INTO calendar_events (id, summary, start_time, end_time, attendees) VALUES (?, ?, ?, ?, ?)",
               (eid, p.get("summary", ""), p.get("start", ""), p.get("end", ""), json.dumps(p.get("attendees", []))))
    db.commit()
    return {"id": eid, "summary": p.get("summary"), "htmlLink": f"https://calendar.google.com/event/{eid}"}


def _cal_list(p: dict) -> dict:
    db = get_db()
    rows = db.execute("SELECT * FROM calendar_events ORDER BY start_time LIMIT ?", (p.get("max_results", 10),)).fetchall()
    return {"items": [{"id": r["id"], "summary": r["summary"], "start": r["start_time"], "end": r["end_time"]} for r in rows]}


def _cal_delete(p: dict) -> dict:
    db = get_db()
    db.execute("DELETE FROM calendar_events WHERE id = ?", (p.get("event_id", ""),))
    db.commit()
    return {"deleted": True, "event_id": p.get("event_id")}


# ─── Gmail ────────────────────────────────────────────────────────────────────

def _gmail_send(p: dict) -> dict:
    mid = f"msg_{uuid.uuid4().hex[:10]}"
    db = get_db()
    db.execute("INSERT INTO emails_sent (id, to_addr, subject, body) VALUES (?, ?, ?, ?)",
               (mid, p.get("to", ""), p.get("subject", ""), p.get("body", "")))
    db.commit()
    return {"id": mid, "labelIds": ["SENT"], "threadId": f"thread_{mid}"}


def _gmail_fetch(p: dict) -> dict:
    return {"messages": [], "resultSizeEstimate": 0}


def _gmail_draft(p: dict) -> dict:
    did = f"draft_{uuid.uuid4().hex[:10]}"
    return {"id": did, "message": {"to": p.get("to"), "subject": p.get("subject")}}


# ─── GitHub ───────────────────────────────────────────────────────────────────

def _gh_repos(p: dict) -> dict:
    return {"items": [{"full_name": "benchmark-org/sample-repo", "html_url": "https://github.com/benchmark-org/sample-repo"}]}


def _gh_issue(p: dict) -> dict:
    iid = uuid.uuid4().hex[:8]
    return {"number": int(iid, 16) % 1000, "title": p.get("title"), "html_url": f"https://github.com/{p.get('owner')}/{p.get('repo')}/issues/{iid}"}


# ─── Slack ────────────────────────────────────────────────────────────────────

def _slack_send(p: dict) -> dict:
    return {"ok": True, "channel": p.get("channel"), "ts": str(time.time())}


def _slack_channels(p: dict) -> dict:
    return {"ok": True, "channels": [{"id": "C001", "name": "general"}, {"id": "C002", "name": "engineering"}]}


# ─── HubSpot ──────────────────────────────────────────────────────────────────

def _hs_contacts(p: dict) -> dict:
    return {"results": [{"id": "101", "properties": {"firstname": "Alice", "lastname": "Bench", "email": "alice@bench.test"}}]}


def _hs_search(p: dict) -> dict:
    return _hs_contacts(p)


def _hs_create_contacts(p: dict) -> dict:
    inputs = p.get("inputs", [])
    return {"results": [{"id": str(1000 + i)} for i in range(len(inputs))]}


def _hs_read_all_properties(p: dict) -> dict:
    obj_type = p.get("object_type", "contacts")
    return {"results": [
        {"name": "firstname", "label": "First Name", "type": "string", "fieldType": "text", "groupName": "contactinformation"},
        {"name": "lastname", "label": "Last Name", "type": "string", "fieldType": "text", "groupName": "contactinformation"},
        {"name": "email", "label": "Email", "type": "string", "fieldType": "text", "groupName": "contactinformation"},
        {"name": "phone", "label": "Phone Number", "type": "string", "fieldType": "phonenumber", "groupName": "contactinformation"},
        {"name": "company", "label": "Company Name", "type": "string", "fieldType": "text", "groupName": "contactinformation"},
        {"name": "jobtitle", "label": "Job Title", "type": "string", "fieldType": "text", "groupName": "contactinformation"},
        {"name": "lifecyclestage", "label": "Lifecycle Stage", "type": "enumeration", "fieldType": "radio", "groupName": "contactinformation"},
        {"name": "hs_lead_status", "label": "Lead Status", "type": "enumeration", "fieldType": "radio", "groupName": "contactinformation"},
    ]}


def _hs_read_batch_contacts(p: dict) -> dict:
    inputs = p.get("inputs", [])
    return {"results": [
        {"id": str(inp) if isinstance(inp, str) else str(inp.get("id", f"c{i}")),
         "properties": {"firstname": "Alice", "lastname": "Bench", "email": f"contact{i}@bench.test"}}
        for i, inp in enumerate(inputs)
    ]}


def _hs_read_contact_by_id(p: dict) -> dict:
    cid = p.get("contact_id", "unknown")
    return {"id": cid, "properties": {"firstname": "Alice", "lastname": "Bench", "email": "alice@bench.test",
            "phone": "+1-555-0100", "company": "Bench Corp", "jobtitle": "Engineer"}}


def _hs_search_objects(p: dict) -> dict:
    obj_type = p.get("object_type", "contacts")
    return {"total": 1, "results": [
        {"id": "201", "properties": {"firstname": "Bob", "lastname": "Search", "email": "bob@bench.test"}}
    ]}


def _hs_update_batch_contacts(p: dict) -> dict:
    inputs = p.get("inputs", [])
    return {"results": [{"id": inp.get("id", str(2000 + i)), "properties": inp.get("properties", {})}
                        for i, inp in enumerate(inputs)]}


def _hs_archive_batch_contacts(p: dict) -> dict:
    inputs = p.get("inputs", [])
    return {"status": "COMPLETE", "archivedCount": len(inputs)}


def _hs_create_batch_objects(p: dict) -> dict:
    inputs = p.get("inputs", [])
    obj_type = p.get("object_type", "contacts")
    return {"results": [{"id": str(3000 + i), "properties": inp.get("properties", {})}
                        for i, inp in enumerate(inputs)]}


def _hs_create_batch_properties(p: dict) -> dict:
    inputs = p.get("inputs", [])
    return {"results": [{"name": inp.get("name", f"prop_{i}"), "label": inp.get("label", f"Property {i}"),
                         "type": inp.get("type", "string"), "fieldType": inp.get("fieldType", "text")}
                        for i, inp in enumerate(inputs)]}


def _hs_read_page_objects(p: dict) -> dict:
    obj_type = p.get("object_type", "contacts")
    return {"results": [
        {"id": "301", "properties": {"firstname": "Charlie", "lastname": "Page", "email": "charlie@bench.test"}},
        {"id": "302", "properties": {"firstname": "Dana", "lastname": "Page", "email": "dana@bench.test"}},
    ], "paging": {"next": {"after": "302"}}}


# ─── Gong ────────────────────────────────────────────────────────────────────

def _gong_list_calls(p: dict) -> dict:
    return {"calls": [
        {"id": "call_001", "title": "Discovery Call - Acme Corp", "started": "2026-01-15T10:00:00Z",
         "duration": 1800, "direction": "Inbound", "parties": [
            {"name": "Alice Bench", "email": "alice@bench.test"},
            {"name": "Bob Client", "email": "bob@acme.test"},
         ]},
        {"id": "call_002", "title": "Demo - Widget Inc", "started": "2026-01-16T14:00:00Z",
         "duration": 2700, "direction": "Outbound", "parties": [
            {"name": "Alice Bench", "email": "alice@bench.test"},
            {"name": "Carol Prospect", "email": "carol@widget.test"},
         ]},
    ], "totalRecords": 2}


def _gong_get_call_details(p: dict) -> dict:
    call_id = p.get("call_id", "call_001")
    return {"id": call_id, "title": "Discovery Call - Acme Corp", "started": "2026-01-15T10:00:00Z",
            "duration": 1800, "direction": "Inbound", "language": "en",
            "parties": [
                {"name": "Alice Bench", "email": "alice@bench.test", "speakerId": "s1"},
                {"name": "Bob Client", "email": "bob@acme.test", "speakerId": "s2"},
            ],
            "interaction_stats": {"talkRatio": 0.45, "interactivity": 0.72, "longestMonologue": 120}}


def _gong_get_transcript(p: dict) -> dict:
    call_id = p.get("call_id", "call_001")
    return {"callId": call_id, "transcript": [
        {"speakerId": "s1", "topic": "Introduction", "sentences": [
            {"start": 0.0, "end": 5.2, "text": "Hi Bob, thanks for joining today."},
            {"start": 5.5, "end": 12.0, "text": "I wanted to walk you through our platform and see how we can help."},
        ]},
        {"speakerId": "s2", "topic": "Pain Points", "sentences": [
            {"start": 12.5, "end": 18.0, "text": "Sure, we've been looking for a solution to streamline our workflow."},
            {"start": 18.5, "end": 25.0, "text": "The main challenge is keeping track of customer interactions across channels."},
        ]},
    ]}


def _gong_list_users(p: dict) -> dict:
    return {"users": [
        {"id": "u_001", "emailAddress": "alice@bench.test", "firstName": "Alice", "lastName": "Bench", "active": True},
        {"id": "u_002", "emailAddress": "bob@bench.test", "firstName": "Bob", "lastName": "Bench", "active": True},
    ]}


# ─── Microsoft Teams ─────────────────────────────────────────────────────────

def _teams_send_message(p: dict) -> dict:
    return {"id": f"msg_{uuid.uuid4().hex[:10]}", "chatId": p.get("chat_id"),
            "body": {"content": p.get("content", "")}, "createdDateTime": "2026-01-15T10:00:00Z"}


def _teams_list_channels(p: dict) -> dict:
    team_id = p.get("team_id", "team_001")
    return {"value": [
        {"id": "ch_001", "displayName": "General", "description": "General discussion", "membershipType": "standard"},
        {"id": "ch_002", "displayName": "Engineering", "description": "Engineering team", "membershipType": "standard"},
        {"id": "ch_003", "displayName": "Sales", "description": "Sales team", "membershipType": "private"},
    ]}


def _teams_list_teams(p: dict) -> dict:
    return {"value": [
        {"id": "team_001", "displayName": "Nario Benchmark", "description": "Benchmark test team"},
        {"id": "team_002", "displayName": "Engineering", "description": "Engineering org"},
    ]}


def _teams_create_channel(p: dict) -> dict:
    return {"id": f"ch_{uuid.uuid4().hex[:8]}", "displayName": p.get("display_name", "New Channel"),
            "description": p.get("description", ""), "membershipType": "standard"}


# ─── Outlook ─────────────────────────────────────────────────────────────────

def _outlook_send(p: dict) -> dict:
    mid = f"outlook_msg_{uuid.uuid4().hex[:10]}"
    db = get_db()
    db.execute("INSERT INTO emails_sent (id, to_addr, subject, body) VALUES (?, ?, ?, ?)",
               (mid, p.get("to", ""), p.get("subject", ""), p.get("body", "")))
    db.commit()
    return {"id": mid, "conversationId": f"conv_{mid}", "isRead": True,
            "sentDateTime": "2026-01-15T10:00:00Z"}


def _outlook_fetch(p: dict) -> dict:
    return {"value": [], "totalItemCount": 0}


def _outlook_draft(p: dict) -> dict:
    did = f"outlook_draft_{uuid.uuid4().hex[:10]}"
    return {"id": did, "subject": p.get("subject"), "toRecipients": [{"emailAddress": {"address": p.get("to", "")}}]}


def _outlook_list_folders(p: dict) -> dict:
    return {"value": [
        {"id": "folder_inbox", "displayName": "Inbox", "totalItemCount": 42, "unreadItemCount": 5},
        {"id": "folder_sent", "displayName": "Sent Items", "totalItemCount": 128, "unreadItemCount": 0},
        {"id": "folder_drafts", "displayName": "Drafts", "totalItemCount": 3, "unreadItemCount": 0},
    ]}


HANDLERS: dict[str, Any] = {
    "GOOGLESHEETS_CREATE_GOOGLE_SHEET1": _sheets_create,
    "GOOGLESHEETS_BATCH_GET": _sheets_batch_get,
    "GOOGLESHEETS_GET_SPREADSHEET_INFO": _sheets_info,
    "GOOGLESHEETS_GET_SHEET_NAMES": _sheets_names,
    "GOOGLESHEETS_ADD_SHEET": _sheets_add,
    "GOOGLESHEETS_APPEND_ROWS": _sheets_append,
    "GOOGLESHEETS_UPDATE_CELLS": _sheets_update,
    "GOOGLESHEETS_BATCH_UPDATE": _sheets_batch_update,
    "GOOGLESHEETS_CLEAR_SHEET": _sheets_clear,
    "GOOGLESHEETS_SHARE_SPREADSHEET": _sheets_share,
    "GOOGLEDRIVE_CREATE_FILE_FROM_TEXT": _drive_create,
    "GOOGLEDRIVE_FIND_FILE": _drive_find,
    "GOOGLEDRIVE_UPLOAD_FILE": _drive_upload,
    "GOOGLEDRIVE_GET_FILE_CONTENT": _drive_get,
    "GOOGLEDRIVE_LIST_FILES": _drive_list,
    "GOOGLEDOCS_CREATE_DOCUMENT_MARKDOWN": _docs_create,
    "GOOGLECALENDAR_CREATE_EVENT": _cal_create,
    "GOOGLECALENDAR_LIST_EVENTS": _cal_list,
    "GOOGLECALENDAR_EVENTS_LIST": _cal_list,
    "GOOGLECALENDAR_FIND_EVENT": _cal_list,
    "GOOGLECALENDAR_DELETE_EVENT": _cal_delete,
    "GMAIL_SEND_EMAIL": _gmail_send,
    "GMAIL_FETCH_EMAILS": _gmail_fetch,
    "GMAIL_CREATE_DRAFT": _gmail_draft,
    "GITHUB_GET_REPOS": _gh_repos,
    "GITHUB_CREATE_ISSUE": _gh_issue,
    "SLACK_SEND_MESSAGE": _slack_send,
    "SLACK_LIST_CHANNELS": _slack_channels,
    "HUBSPOT_LIST_CONTACTS_PAGE": _hs_contacts,
    "HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA": _hs_search,
    "HUBSPOT_CREATE_BATCH_OF_CONTACTS": _hs_create_contacts,
    "HUBSPOT_READ_ALL_PROPERTIES_FOR_OBJECT_TYPE": _hs_read_all_properties,
    "HUBSPOT_READ_BATCH_OF_CONTACTS_BY_ID_OR_PROPERTIES": _hs_read_batch_contacts,
    "HUBSPOT_READ_CRM_CONTACT_BY_ID": _hs_read_contact_by_id,
    "HUBSPOT_SEARCH_CRM_OBJECTS_BY_CRITERIA": _hs_search_objects,
    "HUBSPOT_UPDATE_A_BATCH_OF_CONTACTS": _hs_update_batch_contacts,
    "HUBSPOT_ARCHIVE_BATCH_OF_CONTACTS_BY_ID": _hs_archive_batch_contacts,
    "HUBSPOT_CREATE_BATCH_OF_OBJECTS": _hs_create_batch_objects,
    "HUBSPOT_CREATE_BATCH_OF_PROPERTIES": _hs_create_batch_properties,
    "HUBSPOT_READ_APAGE_OF_OBJECTS_BY_TYPE": _hs_read_page_objects,
    "GONG_LIST_CALLS": _gong_list_calls,
    "GONG_GET_CALL_DETAILS": _gong_get_call_details,
    "GONG_GET_CALL_TRANSCRIPT": _gong_get_transcript,
    "GONG_LIST_USERS": _gong_list_users,
    "MICROSOFT_TEAMS_SEND_MESSAGE": _teams_send_message,
    "MICROSOFT_TEAMS_LIST_CHANNELS": _teams_list_channels,
    "MICROSOFT_TEAMS_LIST_TEAMS": _teams_list_teams,
    "MICROSOFT_TEAMS_CREATE_CHANNEL": _teams_create_channel,
    "OUTLOOK_SEND_EMAIL": _outlook_send,
    "OUTLOOK_FETCH_EMAILS": _outlook_fetch,
    "OUTLOOK_CREATE_DRAFT": _outlook_draft,
    "OUTLOOK_LIST_FOLDERS": _outlook_list_folders,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Catch-all for any unmatched Composio or other mock endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def catch_all_api(path: str, request: Request):
    body = None
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            body = await request.json()
        except Exception:
            pass
    logger.info("CATCH-ALL: %s /api/%s q=%s body=%s", request.method, path, request.query_params, json.dumps(body)[:200] if body else "-")
    return {"status": "ok", "mock": True, "path": f"/api/{path}"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
