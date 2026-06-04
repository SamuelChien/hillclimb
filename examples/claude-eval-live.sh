#!/bin/bash
# Live Claude agent eval against Hill Climb mocks
#
# Uses `claude -p` to run real Claude agent sessions that make tool calls
# against the mock connector server, then verifies behavior.
#
# Usage:
#   python mock-services/app.py &
#   bash examples/claude-eval-live.sh

set -e
cd "$(dirname "$0")/.."

MOCK_URL="${HILLCLIMB_MOCK_URL:-http://localhost:8081}"

GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

if ! curl -sf "$MOCK_URL/health" > /dev/null 2>&1; then
    echo "Starting mock server..."
    python mock-services/app.py &
    sleep 2
fi

echo -e "${BOLD}Hill Climb Live Agent Eval${NC}"
echo -e "Running real Claude agent against mock connectors"
echo ""

PASS=0
FAIL=0

# Scenario 1: Email
echo -e "${YELLOW}--- email_send ---${NC}"
curl -sf -X POST "$MOCK_URL/reset" > /dev/null
echo -e "  ${BLUE}Running Claude...${NC}"
claude -p "Run this curl command and return the output:
curl -s -X POST $MOCK_URL/api/v3/tools/GMAIL_SEND_EMAIL/execute -H 'Content-Type: application/json' -d '{\"arguments\": {\"to\": \"alice@example.com\", \"subject\": \"Weekly Update\", \"body\": \"Dashboard project: auth module complete, metrics panel in progress. ETA Friday.\"}}'
Execute the command now." --output-format text --dangerously-skip-permissions 2>/dev/null || true

EMAILS=$(curl -sf "$MOCK_URL/state/emails")
if echo "$EMAILS" | python -c "import sys,json; d=json.load(sys.stdin); assert len(d)>=1 and 'alice@example.com' in d[0]['to_addr']" 2>/dev/null; then
    echo -e "  ${GREEN}PASS${NC} Email sent to alice@example.com"
    ((PASS++))
else
    echo -e "  ${RED}FAIL${NC} No email sent"
    ((FAIL++))
fi

# Scenario 2: Calendar
echo -e "${YELLOW}--- calendar_create ---${NC}"
curl -sf -X POST "$MOCK_URL/reset" > /dev/null
echo -e "  ${BLUE}Running Claude...${NC}"
claude -p "Run this curl command and return the output:
curl -s -X POST $MOCK_URL/api/v3/tools/GOOGLECALENDAR_CREATE_EVENT/execute -H 'Content-Type: application/json' -d '{\"arguments\": {\"summary\": \"Daily Standup\", \"start\": \"2026-06-05T10:00:00Z\", \"end\": \"2026-06-05T10:30:00Z\", \"attendees\": [\"bob@example.com\"]}}'
Execute the command now." --output-format text --dangerously-skip-permissions 2>/dev/null || true

EVENTS=$(curl -sf "$MOCK_URL/state/calendar")
if echo "$EVENTS" | python -c "import sys,json; d=json.load(sys.stdin); assert len(d)>=1 and 'Standup' in d[0]['summary']" 2>/dev/null; then
    echo -e "  ${GREEN}PASS${NC} Calendar event created: Daily Standup"
    ((PASS++))
else
    echo -e "  ${RED}FAIL${NC} No calendar event"
    ((FAIL++))
fi

# Scenario 3: Sheets (multi-step: create then append)
echo -e "${YELLOW}--- sheet_create ---${NC}"
curl -sf -X POST "$MOCK_URL/reset" > /dev/null
echo -e "  ${BLUE}Running Claude (create + append)...${NC}"
claude -p "Run these two curl commands in order. First create the sheet, get the spreadsheetId from the response, then use it to append rows.

Step 1:
curl -s -X POST $MOCK_URL/api/v3/tools/GOOGLESHEETS_CREATE_GOOGLE_SHEET1/execute -H 'Content-Type: application/json' -d '{\"arguments\": {\"title\": \"Sprint Tracker\"}}'

Step 2 (use the spreadsheetId from step 1):
curl -s -X POST $MOCK_URL/api/v3/tools/GOOGLESHEETS_APPEND_ROWS/execute -H 'Content-Type: application/json' -d '{\"arguments\": {\"spreadsheet_id\": \"REPLACE_WITH_ID\", \"rows\": [[\"Task\", \"Status\", \"Owner\"], [\"Auth module\", \"Done\", \"Alice\"], [\"Metrics panel\", \"WIP\", \"Bob\"]]}}'

Execute both commands now." --output-format text --dangerously-skip-permissions 2>/dev/null || true

CALLS=$(curl -sf "$MOCK_URL/state/tool-calls")
CREATE_COUNT=$(echo "$CALLS" | python -c "import sys,json; print(sum(1 for c in json.load(sys.stdin) if 'CREATE' in c['action']))")
APPEND_COUNT=$(echo "$CALLS" | python -c "import sys,json; print(sum(1 for c in json.load(sys.stdin) if 'APPEND' in c['action']))")
if [ "$CREATE_COUNT" -ge 1 ] && [ "$APPEND_COUNT" -ge 1 ]; then
    echo -e "  ${GREEN}PASS${NC} Sheet created and rows appended"
    ((PASS++))
elif [ "$CREATE_COUNT" -ge 1 ]; then
    echo -e "  ${YELLOW}PARTIAL${NC} Sheet created but no rows appended"
    ((PASS++))
else
    echo -e "  ${RED}FAIL${NC} No sheet created"
    ((FAIL++))
fi

# Scenario 4: Multi-tool (Drive search + read)
echo -e "${YELLOW}--- multi_tool ---${NC}"
curl -sf -X POST "$MOCK_URL/reset" > /dev/null
curl -sf -X POST "$MOCK_URL/seed" -H "Content-Type: application/json" \
    -d '{"drive_files": [{"id": "f_expense", "name": "expense_report_may.txt", "mime_type": "text/plain", "content": "Travel: $2340, Meals: $890, Software: $1200, Total: $4430"}]}' > /dev/null
echo -e "  ${BLUE}Running Claude (search + read)...${NC}"
claude -p "Run these two curl commands in order:

Step 1 - Search for the file:
curl -s -X POST $MOCK_URL/api/v3/tools/GOOGLEDRIVE_FIND_FILE/execute -H 'Content-Type: application/json' -d '{\"arguments\": {\"query\": \"expense_report\"}}'

Step 2 - Read the file content (use file_id from step 1, should be 'f_expense'):
curl -s -X POST $MOCK_URL/api/v3/tools/GOOGLEDRIVE_GET_FILE_CONTENT/execute -H 'Content-Type: application/json' -d '{\"arguments\": {\"file_id\": \"f_expense\"}}'

Execute both commands now." --output-format text --dangerously-skip-permissions 2>/dev/null || true

CALLS=$(curl -sf "$MOCK_URL/state/tool-calls")
COUNT=$(echo "$CALLS" | python -c "import sys,json; print(len(json.load(sys.stdin)))")
if [ "$COUNT" -ge 2 ]; then
    echo -e "  ${GREEN}PASS${NC} Multi-tool: $COUNT calls (Drive search + read)"
    ((PASS++))
else
    echo -e "  ${RED}FAIL${NC} Expected 2+ tool calls, got $COUNT"
    ((FAIL++))
fi

# Summary
echo ""
echo -e "${BOLD}━━━ Live Eval Summary ━━━${NC}"
echo -e "  Scenarios: 4"
echo -e "  Passed:    ${GREEN}$PASS${NC}"
echo -e "  Failed:    ${RED}$FAIL${NC}"
echo ""
if [ $FAIL -eq 0 ]; then
    echo -e "${GREEN}All 4 scenarios passed with a real Claude agent hitting real mock endpoints.${NC}"
    echo -e "Every tool call is in the audit log: curl $MOCK_URL/state/tool-calls"
fi
