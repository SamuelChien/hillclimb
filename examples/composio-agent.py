"""Example: Point a Composio-based agent at Hill Climb mocks.

This shows how any team using Composio can run their agent against
Hill Climb's mock connector library for eval/CI purposes.

Usage:
    # Start the mock server
    python mock-services/app.py &

    # Seed test data
    curl -X POST http://localhost:8081/seed -H "Content-Type: application/json" \
        -d '{"calendar_events": [{"id": "e1", "summary": "Standup", "start_time": "2026-06-05T10:00:00Z"}]}'

    # Run your agent with COMPOSIO_BASE_URL pointing at mocks
    COMPOSIO_BASE_URL=http://localhost:8081 python examples/composio-agent.py

    # Inspect what happened
    curl http://localhost:8081/state/tool-calls | python -m json.tool
"""

import os

MOCK_URL = os.environ.get("COMPOSIO_BASE_URL", "http://localhost:8081")

print(f"""
Hill Climb Composio Integration Example
========================================

To run your Composio-based agent against Hill Climb mocks:

1. Set the environment variable:
   export COMPOSIO_BASE_URL=http://localhost:8081

2. The Composio SDK (@composio/core or composio-core) reads this
   variable and routes all API calls to the mock server.

3. Your agent code stays UNCHANGED. The only difference is the
   base URL pointing at the mock instead of production Composio.

4. After the agent runs, inspect state:
   curl http://localhost:8081/state/tool-calls   # What tools were called
   curl http://localhost:8081/state/emails        # What emails were sent
   curl http://localhost:8081/state/calendar      # What events were created
   curl http://localhost:8081/state/sheets/s1     # What data was written

5. Assert on the state to verify your agent behaved correctly.

This works with ANY Composio SDK:
  - @composio/core (TypeScript)
  - composio-core (Python)
  - composio-openai, composio-langchain, composio-crewai, etc.

The mock server implements the exact Composio v3 REST surface:
  GET  /api/v3/connected_accounts
  GET  /api/v3/tools
  POST /api/v3/tools/{{slug}}/execute
  GET  /api/v3/toolkits/{{slug}}

Mock URL: {MOCK_URL}
""")
