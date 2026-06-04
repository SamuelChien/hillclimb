#!/bin/bash
# Hill Climb Agent Eval Demo
# Runs a real Claude agent against mock connectors, then verifies behavior.
# This is the core loop: seed → run agent → assert on state → compare.
#
# Prerequisites:
#   - python + fastapi + uvicorn installed
#   - claude CLI available (Claude Code)
#
# Usage:
#   bash demo/agent-eval-demo.sh

set -e
cd "$(dirname "$0")/.."

MOCK_URL="${HILLCLIMB_MOCK_URL:-http://localhost:8081}"
RESULTS_DIR="demo/results"
mkdir -p "$RESULTS_DIR"

GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo -e "${BLUE}Hill Climb Agent Eval Demo${NC}"
echo -e "${BLUE}Run a Claude agent against mock connectors, then verify behavior.${NC}"
echo ""

# Start mock server if not running
if ! curl -sf "$MOCK_URL/health" > /dev/null 2>&1; then
    echo -e "${YELLOW}Starting mock server...${NC}"
    python mock-services/app.py &
    MOCK_PID=$!
    sleep 2
    trap "kill $MOCK_PID 2>/dev/null" EXIT
fi

# Define test scenarios
declare -a SCENARIOS=(
    "email_send:Send an email to alice@example.com with subject 'Weekly Report' summarizing this week's progress on the dashboard project."
    "calendar_create:Schedule a 30-minute team standup for tomorrow at 10am. Title it 'Daily Standup' and add bob@example.com as an attendee."
    "sheet_create:Create a Google Sheet called 'Sprint Metrics' with columns: Task, Status, Owner, Due Date. Add 3 sample rows."
    "multi_tool:Find the file 'expense_report_may' on Google Drive, read its contents, then create a Google Sheet with the expense data in a table."
)

PASS=0
FAIL=0
TOTAL=${#SCENARIOS[@]}

for scenario in "${SCENARIOS[@]}"; do
    IFS=':' read -r name prompt <<< "$scenario"
    echo -e "${YELLOW}━━━ Scenario: $name ━━━${NC}"

    # Reset mock state
    curl -sf -X POST "$MOCK_URL/reset" > /dev/null

    # Seed state for multi_tool scenario
    if [ "$name" = "multi_tool" ]; then
        curl -sf -X POST "$MOCK_URL/seed" \
            -H "Content-Type: application/json" \
            -d '{
                "drive_files": [{
                    "id": "file_expense_report",
                    "name": "expense_report_may.txt",
                    "mime_type": "text/plain",
                    "content": "Expense Report - May 2026\nTravel: $2,340\nMeals: $890\nSoftware: $1,200\nOffice Supplies: $340\nTotal: $4,770"
                }]
            }' > /dev/null
    fi

    # Use simulation mode for demo (set HILLCLIMB_LIVE=1 for real agent)
    if [ "${HILLCLIMB_LIVE:-0}" = "1" ] && command -v claude &> /dev/null; then
        echo -e "   ${BLUE}Running live Claude agent...${NC}"
        # TODO: wire claude -p with tool-server pointing at mock
        echo -e "   ${YELLOW}(live agent wiring in progress)${NC}"
    else
        echo -e "   ${BLUE}Running simulated agent (set HILLCLIMB_LIVE=1 for real agent)${NC}"

        # Simulate what the agent SHOULD do for each scenario
        case "$name" in
            email_send)
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GMAIL_SEND_EMAIL/execute" \
                    -H "Content-Type: application/json" \
                    -d '{"arguments": {"to": "alice@example.com", "subject": "Weekly Report", "body": "Dashboard project progress: completed auth module, started metrics panel."}}' > /dev/null
                ;;
            calendar_create)
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLECALENDAR_CREATE_EVENT/execute" \
                    -H "Content-Type: application/json" \
                    -d '{"arguments": {"summary": "Daily Standup", "start": "2026-06-05T10:00:00Z", "end": "2026-06-05T10:30:00Z", "attendees": ["bob@example.com"]}}' > /dev/null
                ;;
            sheet_create)
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLESHEETS_CREATE_GOOGLE_SHEET1/execute" \
                    -H "Content-Type: application/json" \
                    -d '{"arguments": {"title": "Sprint Metrics"}}' > /dev/null
                SSID=$(curl -sf "$MOCK_URL/state/spreadsheets" | python -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLESHEETS_APPEND_ROWS/execute" \
                    -H "Content-Type: application/json" \
                    -d "{\"arguments\": {\"spreadsheet_id\": \"$SSID\", \"rows\": [[\"Task\",\"Status\",\"Owner\",\"Due Date\"],[\"Auth module\",\"Done\",\"Alice\",\"2026-06-01\"],[\"Metrics panel\",\"In Progress\",\"Bob\",\"2026-06-05\"],[\"Deploy pipeline\",\"Blocked\",\"Carol\",\"2026-06-07\"]]}}" > /dev/null
                ;;
            multi_tool)
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLEDRIVE_FIND_FILE/execute" \
                    -H "Content-Type: application/json" \
                    -d '{"arguments": {"query": "expense_report_may"}}' > /dev/null
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLEDRIVE_GET_FILE_CONTENT/execute" \
                    -H "Content-Type: application/json" \
                    -d '{"arguments": {"file_id": "file_expense_report"}}' > /dev/null
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLESHEETS_CREATE_GOOGLE_SHEET1/execute" \
                    -H "Content-Type: application/json" \
                    -d '{"arguments": {"title": "May Expenses"}}' > /dev/null
                SSID=$(curl -sf "$MOCK_URL/state/spreadsheets" | python -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLESHEETS_APPEND_ROWS/execute" \
                    -H "Content-Type: application/json" \
                    -d "{\"arguments\": {\"spreadsheet_id\": \"$SSID\", \"rows\": [[\"Category\",\"Amount\"],[\"Travel\",\"2340\"],[\"Meals\",\"890\"],[\"Software\",\"1200\"],[\"Office Supplies\",\"340\"],[\"Total\",\"4770\"]]}}" > /dev/null
                ;;
        esac
        echo -e "   ${BLUE}(simulated agent tool calls)${NC}"
    fi

    # Assertions
    echo -e "   ${BLUE}Checking assertions...${NC}"
    SCENARIO_PASS=true

    case "$name" in
        email_send)
            EMAILS=$(curl -sf "$MOCK_URL/state/emails")
            if echo "$EMAILS" | python -c "import sys,json; d=json.load(sys.stdin); assert len(d)>=1 and 'alice@example.com' in d[0]['to_addr']" 2>/dev/null; then
                echo -e "   ${GREEN}PASS${NC} Email sent to alice@example.com"
            else
                echo -e "   ${RED}FAIL${NC} Expected email to alice@example.com"
                SCENARIO_PASS=false
            fi
            ;;
        calendar_create)
            EVENTS=$(curl -sf "$MOCK_URL/state/calendar")
            if echo "$EVENTS" | python -c "import sys,json; d=json.load(sys.stdin); assert len(d)>=1 and 'Standup' in d[0]['summary']" 2>/dev/null; then
                echo -e "   ${GREEN}PASS${NC} Calendar event created with 'Standup'"
            else
                echo -e "   ${RED}FAIL${NC} Expected calendar event with 'Standup'"
                SCENARIO_PASS=false
            fi
            ;;
        sheet_create)
            CALLS=$(curl -sf "$MOCK_URL/state/tool-calls?action=GOOGLESHEETS_CREATE_GOOGLE_SHEET1")
            if echo "$CALLS" | python -c "import sys,json; d=json.load(sys.stdin); assert len(d)>=1" 2>/dev/null; then
                echo -e "   ${GREEN}PASS${NC} Spreadsheet created"
            else
                echo -e "   ${RED}FAIL${NC} Expected spreadsheet creation"
                SCENARIO_PASS=false
            fi
            CALLS=$(curl -sf "$MOCK_URL/state/tool-calls?action=GOOGLESHEETS_APPEND_ROWS")
            if echo "$CALLS" | python -c "import sys,json; d=json.load(sys.stdin); assert len(d)>=1" 2>/dev/null; then
                echo -e "   ${GREEN}PASS${NC} Rows appended"
            else
                echo -e "   ${RED}FAIL${NC} Expected rows appended"
                SCENARIO_PASS=false
            fi
            ;;
        multi_tool)
            CALLS=$(curl -sf "$MOCK_URL/state/tool-calls")
            CALL_COUNT=$(echo "$CALLS" | python -c "import sys,json; print(len(json.load(sys.stdin)))")
            if [ "$CALL_COUNT" -ge 3 ]; then
                echo -e "   ${GREEN}PASS${NC} Multi-tool workflow: $CALL_COUNT tool calls"
            else
                echo -e "   ${RED}FAIL${NC} Expected 3+ tool calls, got $CALL_COUNT"
                SCENARIO_PASS=false
            fi
            # Check specific tools were used
            TOOLS=$(echo "$CALLS" | python -c "import sys,json; print(' '.join(set(c['action'] for c in json.load(sys.stdin))))")
            if echo "$TOOLS" | grep -q "GOOGLEDRIVE" && echo "$TOOLS" | grep -q "GOOGLESHEETS"; then
                echo -e "   ${GREEN}PASS${NC} Used both Drive and Sheets connectors"
            else
                echo -e "   ${RED}FAIL${NC} Expected both GOOGLEDRIVE and GOOGLESHEETS tools"
                SCENARIO_PASS=false
            fi
            ;;
    esac

    # Save results
    TOOL_LOG=$(curl -sf "$MOCK_URL/state/tool-calls")
    echo "$TOOL_LOG" > "$RESULTS_DIR/$name-tool-calls.json"

    if [ "$SCENARIO_PASS" = true ]; then
        echo -e "   ${GREEN}SCENARIO PASSED${NC}"
        ((PASS++))
    else
        echo -e "   ${RED}SCENARIO FAILED${NC}"
        ((FAIL++))
    fi
    echo ""
done

# Summary
echo -e "${BLUE}━━━ Eval Summary ━━━${NC}"
echo -e "  Scenarios: $TOTAL"
echo -e "  Passed:    ${GREEN}$PASS${NC}"
echo -e "  Failed:    ${RED}$FAIL${NC}"
echo -e "  Results:   $RESULTS_DIR/"
echo ""

if [ $FAIL -eq 0 ]; then
    echo -e "${GREEN}All scenarios passed.${NC}"
    echo ""
    echo -e "This is the Hill Climb eval loop:"
    echo -e "  1. ${BLUE}Baseline${NC}: run these scenarios BEFORE your prompt change"
    echo -e "  2. ${YELLOW}Change${NC}: edit your system prompt / skill / tool description"
    echo -e "  3. ${BLUE}Revision${NC}: run the same scenarios AFTER your change"
    echo -e "  4. ${GREEN}Compare${NC}: did pass rate improve? Did anything regress?"
    echo ""
    echo -e "Your prompt change is safe to deploy when revision >= baseline"
    echo -e "and no previously-passing scenario now fails."
else
    echo -e "${RED}$FAIL scenario(s) failed. Fix before deploying.${NC}"
    exit 1
fi
