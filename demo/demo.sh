#!/bin/bash
# Hill Climb Demo — CI for Agent Prompt Changes
# Shows the mock connector library in action with a real CRM workflow

set -e
cd "$(dirname "$0")/.."

MOCK_URL="${HILLCLIMB_MOCK_URL:-http://localhost:8081}"
CLI="node packages/cli/dist/cli.js"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  Hill Climb — CI for Agent Prompt Changes               ║${NC}"
echo -e "${BLUE}║  Mock 10 enterprise connectors. Eval your agent.        ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check mock server
echo -e "${YELLOW}1. Checking mock server...${NC}"
if ! curl -sf "$MOCK_URL/health" > /dev/null 2>&1; then
    echo "   Mock server not running. Starting..."
    python mock-services/app.py &
    sleep 2
fi
echo -e "   ${GREEN}Mock server healthy at $MOCK_URL${NC}"
echo ""

# Reset state
echo -e "${YELLOW}2. Resetting mock state...${NC}"
curl -sf -X POST "$MOCK_URL/reset" | python -m json.tool
echo ""

# Seed CRM data
echo -e "${YELLOW}3. Seeding CRM workflow data (leads, proposals, meetings)...${NC}"
curl -sf -X POST "$MOCK_URL/seed" \
  -H "Content-Type: application/json" \
  -d @demo/seed-crm-workflow.json | python -m json.tool
echo ""

# Show what's in the mock
echo -e "${YELLOW}4. Inspecting mock state...${NC}"
echo ""
echo -e "   ${BLUE}Spreadsheets:${NC}"
curl -sf "$MOCK_URL/state/spreadsheets" | python -m json.tool
echo ""
echo -e "   ${BLUE}Sheet data (CRM leads):${NC}"
curl -sf "$MOCK_URL/state/sheets/crm_leads" | python -m json.tool
echo ""
echo -e "   ${BLUE}Drive files:${NC}"
curl -sf "$MOCK_URL/state/drive" | python -m json.tool
echo ""
echo -e "   ${BLUE}Calendar events:${NC}"
curl -sf "$MOCK_URL/state/calendar" | python -m json.tool
echo ""

# Simulate agent tool calls
echo -e "${YELLOW}5. Simulating agent workflow: 'Send Bob the proposal and schedule a demo'${NC}"
echo ""

echo -e "   ${BLUE}Step 1: Agent searches Drive for Widget proposal...${NC}"
curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLEDRIVE_FIND_FILE/execute" \
  -H "Content-Type: application/json" \
  -d '{"arguments": {"query": "Widget"}}' | python -m json.tool
echo ""

echo -e "   ${BLUE}Step 2: Agent reads the discovery notes...${NC}"
curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLEDRIVE_GET_FILE_CONTENT/execute" \
  -H "Content-Type: application/json" \
  -d '{"arguments": {"file_id": "meeting_notes_widget"}}' | python -m json.tool
echo ""

echo -e "   ${BLUE}Step 3: Agent sends email to Bob with proposal...${NC}"
curl -sf -X POST "$MOCK_URL/api/v3/tools/GMAIL_SEND_EMAIL/execute" \
  -H "Content-Type: application/json" \
  -d '{"arguments": {"to": "bob@widget.io", "subject": "Hill Climb Proposal for Widget Inc", "body": "Hi Bob, following up on our discovery call. Based on your need to eliminate manual data entry across 3 systems, I'\''ve attached our Enterprise proposal ($30K/year, 10 seats). Would love to schedule a demo this week."}}' | python -m json.tool
echo ""

echo -e "   ${BLUE}Step 4: Agent creates calendar event for demo...${NC}"
curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLECALENDAR_CREATE_EVENT/execute" \
  -H "Content-Type: application/json" \
  -d '{"arguments": {"summary": "Demo: Widget Inc", "start": "2026-06-06T15:00:00Z", "end": "2026-06-06T16:00:00Z", "attendees": ["bob@widget.io"]}}' | python -m json.tool
echo ""

echo -e "   ${BLUE}Step 5: Agent updates CRM sheet with new status...${NC}"
curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLESHEETS_UPDATE_CELLS/execute" \
  -H "Content-Type: application/json" \
  -d '{"arguments": {"spreadsheet_id": "crm_leads", "start_cell": "D3", "values": [["Proposal Sent"]]}}' | python -m json.tool
echo ""

# Verify the results
echo -e "${YELLOW}6. Verifying results — this is what your eval assertions check${NC}"
echo ""

echo -e "   ${BLUE}Emails sent:${NC}"
curl -sf "$MOCK_URL/state/emails" | python -m json.tool
echo ""

echo -e "   ${BLUE}Calendar events (now 2):${NC}"
curl -sf "$MOCK_URL/state/calendar" | python -m json.tool
echo ""

echo -e "   ${BLUE}Updated sheet (Bob's status = Proposal Sent):${NC}"
curl -sf "$MOCK_URL/state/sheets/crm_leads" | python -m json.tool
echo ""

echo -e "   ${BLUE}Tool call audit log (5 calls):${NC}"
curl -sf "$MOCK_URL/state/tool-calls" | python -m json.tool
echo ""

echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Demo complete.                                         ║${NC}"
echo -e "${GREEN}║                                                         ║${NC}"
echo -e "${GREEN}║  What you just saw:                                     ║${NC}"
echo -e "${GREEN}║  - 10 connector toolkits with stateful SQLite backing   ║${NC}"
echo -e "${GREEN}║  - Seed → execute → inspect → assert workflow           ║${NC}"
echo -e "${GREEN}║  - Every tool call logged in the audit trail            ║${NC}"
echo -e "${GREEN}║                                                         ║${NC}"
echo -e "${GREEN}║  This is how you test agent prompt changes:             ║${NC}"
echo -e "${GREEN}║  1. Seed mock state (your test fixture)                 ║${NC}"
echo -e "${GREEN}║  2. Run your agent against the mocks                   ║${NC}"
echo -e "${GREEN}║  3. Assert on the resulting state                      ║${NC}"
echo -e "${GREEN}║  4. Change the prompt, re-run, compare                 ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
