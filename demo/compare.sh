#!/bin/bash
# Hill Climb: Baseline vs Revision Comparison
#
# This is the CI loop. Run it twice:
#   1. BEFORE your prompt change → saves baseline
#   2. AFTER your prompt change  → compares against baseline
#
# Usage:
#   bash demo/compare.sh baseline    # Save baseline results
#   bash demo/compare.sh revision    # Compare against baseline
#   bash demo/compare.sh report      # Show comparison report

set -e
cd "$(dirname "$0")/.."

MOCK_URL="${HILLCLIMB_MOCK_URL:-http://localhost:8081}"
RESULTS_DIR="demo/results"
BASELINE_FILE="$RESULTS_DIR/baseline.json"
REVISION_FILE="$RESULTS_DIR/revision.json"

GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

# Scenarios to run
declare -a SCENARIO_NAMES=("email_send" "calendar_create" "sheet_create" "multi_tool")

run_scenarios() {
    local run_type=$1
    local output_file=$2
    local total=0
    local passed=0

    echo "{\"run_type\": \"$run_type\", \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"scenarios\": [" > "$output_file"
    local first=true

    for name in "${SCENARIO_NAMES[@]}"; do
        # Reset state
        curl -sf -X POST "$MOCK_URL/reset" > /dev/null

        # Seed for multi_tool
        if [ "$name" = "multi_tool" ]; then
            curl -sf -X POST "$MOCK_URL/seed" \
                -H "Content-Type: application/json" \
                -d '{"drive_files": [{"id": "file_expense_report", "name": "expense_report_may.txt", "mime_type": "text/plain", "content": "Expense Report\nTravel: $2,340\nMeals: $890\nSoftware: $1,200\nTotal: $4,770"}]}' > /dev/null
        fi

        # Simulate agent tool calls (same as agent-eval-demo.sh)
        case "$name" in
            email_send)
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GMAIL_SEND_EMAIL/execute" \
                    -H "Content-Type: application/json" \
                    -d '{"arguments": {"to": "alice@example.com", "subject": "Weekly Report", "body": "Progress update."}}' > /dev/null
                ;;
            calendar_create)
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLECALENDAR_CREATE_EVENT/execute" \
                    -H "Content-Type: application/json" \
                    -d '{"arguments": {"summary": "Daily Standup", "start": "2026-06-05T10:00:00Z", "end": "2026-06-05T10:30:00Z"}}' > /dev/null
                ;;
            sheet_create)
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLESHEETS_CREATE_GOOGLE_SHEET1/execute" \
                    -H "Content-Type: application/json" \
                    -d '{"arguments": {"title": "Sprint Metrics"}}' > /dev/null
                SSID=$(curl -sf "$MOCK_URL/state/spreadsheets" | python -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLESHEETS_APPEND_ROWS/execute" \
                    -H "Content-Type: application/json" \
                    -d "{\"arguments\": {\"spreadsheet_id\": \"$SSID\", \"rows\": [[\"Task\",\"Status\"],[\"Auth\",\"Done\"],[\"Metrics\",\"WIP\"]]}}" > /dev/null
                ;;
            multi_tool)
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLEDRIVE_FIND_FILE/execute" \
                    -H "Content-Type: application/json" \
                    -d '{"arguments": {"query": "expense_report"}}' > /dev/null
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLEDRIVE_GET_FILE_CONTENT/execute" \
                    -H "Content-Type: application/json" \
                    -d '{"arguments": {"file_id": "file_expense_report"}}' > /dev/null
                curl -sf -X POST "$MOCK_URL/api/v3/tools/GOOGLESHEETS_CREATE_GOOGLE_SHEET1/execute" \
                    -H "Content-Type: application/json" \
                    -d '{"arguments": {"title": "May Expenses"}}' > /dev/null
                ;;
        esac

        # Get tool calls for this scenario
        CALLS=$(curl -sf "$MOCK_URL/state/tool-calls")
        CALL_COUNT=$(echo "$CALLS" | python -c "import sys,json; print(len(json.load(sys.stdin)))")

        # Run assertions
        local scenario_pass=true
        local assertions_passed=0
        local assertions_total=0

        case "$name" in
            email_send)
                assertions_total=1
                EMAILS=$(curl -sf "$MOCK_URL/state/emails")
                if echo "$EMAILS" | python -c "import sys,json; d=json.load(sys.stdin); assert len(d)>=1 and 'alice@example.com' in d[0]['to_addr']" 2>/dev/null; then
                    assertions_passed=1
                else
                    scenario_pass=false
                fi
                ;;
            calendar_create)
                assertions_total=1
                EVENTS=$(curl -sf "$MOCK_URL/state/calendar")
                if echo "$EVENTS" | python -c "import sys,json; d=json.load(sys.stdin); assert len(d)>=1 and 'Standup' in d[0]['summary']" 2>/dev/null; then
                    assertions_passed=1
                else
                    scenario_pass=false
                fi
                ;;
            sheet_create)
                assertions_total=2
                TC1=$(curl -sf "$MOCK_URL/state/tool-calls?action=GOOGLESHEETS_CREATE_GOOGLE_SHEET1")
                if echo "$TC1" | python -c "import sys,json; assert len(json.load(sys.stdin))>=1" 2>/dev/null; then
                    ((assertions_passed++))
                else
                    scenario_pass=false
                fi
                TC2=$(curl -sf "$MOCK_URL/state/tool-calls?action=GOOGLESHEETS_APPEND_ROWS")
                if echo "$TC2" | python -c "import sys,json; assert len(json.load(sys.stdin))>=1" 2>/dev/null; then
                    ((assertions_passed++))
                else
                    scenario_pass=false
                fi
                ;;
            multi_tool)
                assertions_total=2
                if [ "$CALL_COUNT" -ge 3 ]; then
                    ((assertions_passed++))
                else
                    scenario_pass=false
                fi
                TOOLS=$(echo "$CALLS" | python -c "import sys,json; print(' '.join(set(c['action'] for c in json.load(sys.stdin))))")
                if echo "$TOOLS" | grep -q "GOOGLEDRIVE" && echo "$TOOLS" | grep -q "GOOGLESHEETS"; then
                    ((assertions_passed++))
                else
                    scenario_pass=false
                fi
                ;;
        esac

        if [ "$scenario_pass" = true ]; then
            ((passed++))
        fi
        ((total++))

        # Write JSON
        if [ "$first" = true ]; then
            first=false
        else
            echo "," >> "$output_file"
        fi
        echo "  {\"name\": \"$name\", \"pass\": $scenario_pass, \"tool_calls\": $CALL_COUNT, \"assertions_passed\": $assertions_passed, \"assertions_total\": $assertions_total}" >> "$output_file"
    done

    echo "], \"summary\": {\"total\": $total, \"passed\": $passed, \"failed\": $((total - passed)), \"pass_rate\": $(python -c "print(round($passed/$total*100, 1))")}}" >> "$output_file"

    echo "$passed/$total"
}

case "${1:-report}" in
    baseline)
        echo -e "${BLUE}Running baseline...${NC}"
        if ! curl -sf "$MOCK_URL/health" > /dev/null 2>&1; then
            echo "Starting mock server..."
            python mock-services/app.py &
            sleep 2
        fi
        RESULT=$(run_scenarios "baseline" "$BASELINE_FILE")
        echo -e "${GREEN}Baseline saved: $RESULT scenarios passed${NC}"
        echo -e "File: $BASELINE_FILE"
        echo ""
        echo -e "Now make your prompt change, then run:"
        echo -e "  ${BOLD}bash demo/compare.sh revision${NC}"
        ;;

    revision)
        if [ ! -f "$BASELINE_FILE" ]; then
            echo -e "${RED}No baseline found. Run 'bash demo/compare.sh baseline' first.${NC}"
            exit 1
        fi
        echo -e "${BLUE}Running revision...${NC}"
        if ! curl -sf "$MOCK_URL/health" > /dev/null 2>&1; then
            echo "Starting mock server..."
            python mock-services/app.py &
            sleep 2
        fi
        RESULT=$(run_scenarios "revision" "$REVISION_FILE")
        echo -e "${GREEN}Revision complete: $RESULT scenarios passed${NC}"
        echo ""
        # Auto-show report
        bash demo/compare.sh report
        ;;

    report)
        if [ ! -f "$BASELINE_FILE" ] || [ ! -f "$REVISION_FILE" ]; then
            echo -e "${RED}Need both baseline and revision. Run:${NC}"
            echo "  bash demo/compare.sh baseline"
            echo "  bash demo/compare.sh revision"
            exit 1
        fi

        echo ""
        echo -e "${BOLD}Hill Climb Comparison Report${NC}"
        echo -e "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""

        B_RATE=$(python -c "import json; d=json.load(open('$BASELINE_FILE')); print(d['summary']['pass_rate'])")
        R_RATE=$(python -c "import json; d=json.load(open('$REVISION_FILE')); print(d['summary']['pass_rate'])")
        B_PASS=$(python -c "import json; d=json.load(open('$BASELINE_FILE')); print(d['summary']['passed'])")
        R_PASS=$(python -c "import json; d=json.load(open('$REVISION_FILE')); print(d['summary']['passed'])")
        B_TOTAL=$(python -c "import json; d=json.load(open('$BASELINE_FILE')); print(d['summary']['total'])")

        echo -e "  ${BOLD}Overall${NC}"
        echo -e "  ┌──────────────┬──────────┬──────────┬────────┐"
        echo -e "  │ Metric       │ Baseline │ Revision │ Delta  │"
        echo -e "  ├──────────────┼──────────┼──────────┼────────┤"
        printf "  │ Pass rate    │ %6s%%  │ %6s%%  │" "$B_RATE" "$R_RATE"
        DELTA=$(python -c "print(round($R_RATE - $B_RATE, 1))")
        if (( $(echo "$DELTA > 0" | bc -l) )); then
            printf " ${GREEN}+%s%%${NC} │\n" "$DELTA"
        elif (( $(echo "$DELTA < 0" | bc -l) )); then
            printf " ${RED}%s%%${NC} │\n" "$DELTA"
        else
            printf " %s%%  │\n" "$DELTA"
        fi
        printf "  │ Passed       │ %6s   │ %6s   │ %+5s  │\n" "$B_PASS/$B_TOTAL" "$R_PASS/$B_TOTAL" "$(python -c "print($R_PASS - $B_PASS)")"
        echo -e "  └──────────────┴──────────┴──────────┴────────┘"
        echo ""

        # Per-scenario comparison
        echo -e "  ${BOLD}Per Scenario${NC}"
        echo -e "  ┌──────────────────┬──────────┬──────────┬───────────┐"
        echo -e "  │ Scenario         │ Baseline │ Revision │ Status    │"
        echo -e "  ├──────────────────┼──────────┼──────────┼───────────┤"

        REGRESSIONS=0
        python -c "
import json
b = {s['name']: s for s in json.load(open('$BASELINE_FILE'))['scenarios']}
r = {s['name']: s for s in json.load(open('$REVISION_FILE'))['scenarios']}
regressions = 0
for name in b:
    bp = 'PASS' if b[name]['pass'] else 'FAIL'
    rp = 'PASS' if r.get(name, {}).get('pass', False) else 'FAIL'
    if bp == 'PASS' and rp == 'FAIL':
        status = 'REGRESSED'
        regressions += 1
    elif bp == 'FAIL' and rp == 'PASS':
        status = 'FIXED'
    elif bp == rp:
        status = 'STABLE'
    else:
        status = 'CHANGED'
    print(f'{name}|{bp}|{rp}|{status}')
print(f'REGRESSIONS:{regressions}')
" | while IFS='|' read -r name bp rp status; do
            if [[ "$name" == REGRESSIONS:* ]]; then
                REGRESSIONS="${name#REGRESSIONS:}"
                continue
            fi
            case "$status" in
                REGRESSED) color="$RED" ;;
                FIXED) color="$GREEN" ;;
                *) color="$NC" ;;
            esac
            printf "  │ %-16s │ %-8s │ %-8s │ ${color}%-9s${NC} │\n" "$name" "$bp" "$rp" "$status"
        done
        echo -e "  └──────────────────┴──────────┴──────────┴───────────┘"
        echo ""

        # Verdict
        REGRESSIONS=$(python -c "
import json
b = {s['name']: s['pass'] for s in json.load(open('$BASELINE_FILE'))['scenarios']}
r = {s['name']: s['pass'] for s in json.load(open('$REVISION_FILE'))['scenarios']}
print(sum(1 for n in b if b[n] and not r.get(n, False)))
")

        if [ "$REGRESSIONS" -gt 0 ]; then
            echo -e "  ${RED}${BOLD}VERDICT: DO NOT DEPLOY${NC}"
            echo -e "  ${RED}$REGRESSIONS regression(s) detected. Fix before shipping.${NC}"
            exit 1
        elif (( $(echo "$R_RATE > $B_RATE" | bc -l) )); then
            echo -e "  ${GREEN}${BOLD}VERDICT: SAFE TO DEPLOY${NC}"
            echo -e "  ${GREEN}Pass rate improved ($B_RATE% -> $R_RATE%), no regressions.${NC}"
        elif (( $(echo "$R_RATE == $B_RATE" | bc -l) )); then
            echo -e "  ${YELLOW}${BOLD}VERDICT: NEUTRAL${NC}"
            echo -e "  ${YELLOW}No improvement, no regressions. Change is safe but not impactful.${NC}"
        else
            echo -e "  ${RED}${BOLD}VERDICT: REVIEW REQUIRED${NC}"
            echo -e "  ${RED}Pass rate decreased ($B_RATE% -> $R_RATE%).${NC}"
        fi
        echo ""
        ;;

    *)
        echo "Usage: bash demo/compare.sh [baseline|revision|report]"
        echo ""
        echo "  baseline  Run scenarios and save as baseline"
        echo "  revision  Run scenarios and compare against baseline"
        echo "  report    Show comparison report (needs both baseline and revision)"
        exit 1
        ;;
esac
