"""Tests for the Hill Climb mock connector library."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "mock-services"))

import pytest
from fastapi.testclient import TestClient
from app import app, reset_db


@pytest.fixture(autouse=True)
def clean_state():
    reset_db()
    yield
    reset_db()


client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_seed_and_inspect_sheets():
    client.post("/seed", json={
        "spreadsheets": [{"id": "s1", "title": "Test Sheet"}],
        "sheets": [{"spreadsheet_id": "s1", "rows": [["Name", "Value"], ["A", "100"]]}],
    })
    r = client.get("/state/sheets/s1")
    assert r.status_code == 200
    cells = r.json()["cells"]
    assert cells["0,0"] == "Name"
    assert cells["1,1"] == "100"


def test_create_spreadsheet():
    r = client.post("/api/v3/tools/GOOGLESHEETS_CREATE_GOOGLE_SHEET1/execute", json={
        "arguments": {"title": "New Sheet"}
    })
    assert r.json()["successful"] is True
    assert "spreadsheetId" in r.json()["data"]


def test_append_rows():
    client.post("/api/v3/tools/GOOGLESHEETS_CREATE_GOOGLE_SHEET1/execute", json={
        "arguments": {"title": "Append Test"}
    })
    ss = client.get("/state/spreadsheets").json()
    sid = ss[0]["id"]

    r = client.post(f"/api/v3/tools/GOOGLESHEETS_APPEND_ROWS/execute", json={
        "arguments": {"spreadsheet_id": sid, "rows": [["A", "B"], ["1", "2"]]}
    })
    assert r.json()["successful"] is True
    assert r.json()["data"]["updatedRows"] == 2


def test_create_calendar_event():
    r = client.post("/api/v3/tools/GOOGLECALENDAR_CREATE_EVENT/execute", json={
        "arguments": {"summary": "Standup", "start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:30:00Z"}
    })
    assert r.json()["successful"] is True
    assert r.json()["data"]["summary"] == "Standup"

    events = client.get("/state/calendar").json()
    assert len(events) == 1
    assert events[0]["summary"] == "Standup"


def test_send_gmail():
    r = client.post("/api/v3/tools/GMAIL_SEND_EMAIL/execute", json={
        "arguments": {"to": "alice@example.com", "subject": "Test", "body": "Hello"}
    })
    assert r.json()["successful"] is True

    emails = client.get("/state/emails").json()
    assert len(emails) == 1
    assert emails[0]["to_addr"] == "alice@example.com"


def test_send_outlook():
    r = client.post("/api/v3/tools/OUTLOOK_SEND_EMAIL/execute", json={
        "arguments": {"to": "bob@example.com", "subject": "Outlook Test", "body": "Hi"}
    })
    assert r.json()["successful"] is True

    emails = client.get("/state/emails").json()
    assert len(emails) == 1
    assert emails[0]["to_addr"] == "bob@example.com"


def test_drive_create_and_find():
    r = client.post("/api/v3/tools/GOOGLEDRIVE_CREATE_FILE_FROM_TEXT/execute", json={
        "arguments": {"name": "report.txt", "content": "Q2 revenue: $1M"}
    })
    assert r.json()["successful"] is True
    fid = r.json()["data"]["id"]

    r2 = client.post("/api/v3/tools/GOOGLEDRIVE_FIND_FILE/execute", json={
        "arguments": {"query": "report"}
    })
    assert len(r2.json()["data"]["files"]) == 1


def test_tool_call_audit_log():
    client.post("/api/v3/tools/GMAIL_SEND_EMAIL/execute", json={
        "arguments": {"to": "a@b.com", "subject": "S", "body": "B"}
    })
    r = client.get("/state/tool-calls")
    calls = r.json()
    assert len(calls) == 1
    assert calls[0]["action"] == "GMAIL_SEND_EMAIL"
    assert calls[0]["success"] == 1


def test_list_tools_by_toolkit():
    r = client.get("/api/v3/tools?toolkit_slug=GMAIL")
    items = r.json()["items"]
    assert all(t["toolkit"]["slug"] == "gmail" for t in items)
    assert len(items) >= 2


def test_connected_accounts():
    r = client.get("/api/v3/connected_accounts?statuses=ACTIVE")
    items = r.json()["items"]
    assert len(items) == 10
    slugs = {a["toolkit"]["slug"] for a in items}
    assert "gmail" in slugs
    assert "googlesheets" in slugs
    assert "outlook" in slugs


def test_reset():
    client.post("/api/v3/tools/GMAIL_SEND_EMAIL/execute", json={
        "arguments": {"to": "a@b.com", "subject": "S", "body": "B"}
    })
    client.post("/reset")
    emails = client.get("/state/emails").json()
    assert len(emails) == 0


def test_hubspot_search():
    r = client.post("/api/v3/tools/HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA/execute", json={
        "arguments": {"query": "Bench"}
    })
    assert r.json()["successful"] is True
    assert len(r.json()["data"]["results"]) >= 1


def test_slack_send():
    r = client.post("/api/v3/tools/SLACK_SEND_MESSAGE/execute", json={
        "arguments": {"channel": "engineering", "text": "Deploy complete"}
    })
    assert r.json()["successful"] is True


def test_gong_list_calls():
    r = client.post("/api/v3/tools/GONG_LIST_CALLS/execute", json={
        "arguments": {}
    })
    assert r.json()["successful"] is True
    assert len(r.json()["data"]["calls"]) == 2


def test_teams_send_message():
    r = client.post("/api/v3/tools/MICROSOFT_TEAMS_SEND_MESSAGE/execute", json={
        "arguments": {"chat_id": "ch_001", "content": "Hello Teams"}
    })
    assert r.json()["successful"] is True
