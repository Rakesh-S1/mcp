"""
MCP server — Calendar scheduling tools.

─── Modes ────────────────────────────────────────────────────────────────────

  Mock mode (zero Azure, zero credentials — great for local testing):
      USE_MOCK=true python -m mcp_server.server
      Uses fake calendar data from mock_graph_client.py.
      No MS Graph calls, no Azure AD tokens required.

  Local dev (real Graph, stdio transport, no HTTP auth):
      python -m mcp_server.server
      Needs: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET in .env

  Production (real Graph, HTTP transport, Azure AD auth enforced):
      MCP_TRANSPORT=http python -m mcp_server.server
      Needs all of the above plus MCP_SERVER_CLIENT_ID.
      Or via uvicorn: uvicorn mcp_server.server:http_app --host 0.0.0.0 --port 8000

─── Tools ────────────────────────────────────────────────────────────────────
  1. check_availability   — raw free/busy for a list of users over a time range
  2. find_meeting_slots   — ranked list of candidate slots
  3. get_attendee_summary — human-readable summary of who is free/busy
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .auth import AzureADAuthMiddleware

# Switch between real MS Graph and local mock data via USE_MOCK env var
if os.environ.get("USE_MOCK", "").lower() in ("1", "true", "yes"):
    from .mock_graph import find_best_slots, get_free_busy  # type: ignore[assignment]
    print("[MCP server] Running in MOCK mode — no Azure credentials needed.")
else:
    from .graph_client import find_best_slots, get_free_busy

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="calendar-scheduler",
    instructions=(
        "You are a calendar scheduling assistant. "
        "Use check_availability to get raw free/busy data, "
        "find_meeting_slots to get ranked candidate slots, "
        "and get_attendee_summary for a human-readable overview."
    ),
)


# ---------------------------------------------------------------------------
# Tool 1 — raw free/busy
# ---------------------------------------------------------------------------

@mcp.tool()
def check_availability(
    attendee_emails: list[str],
    start_datetime: str,
    end_datetime: str,
    interval_minutes: int = 30,
) -> str:
    """
    Check calendar availability (free/busy) for a list of attendees.

    Args:
        attendee_emails:  List of Office 365 email addresses to check.
        start_datetime:   ISO-8601 datetime string for the range start (UTC).
                          Example: "2025-06-02T08:00:00"
        end_datetime:     ISO-8601 datetime string for the range end (UTC).
        interval_minutes: Granularity of the availability view (default 30 min).

    Returns:
        JSON string mapping each email to a list of busy blocks.
    """
    start = _parse(start_datetime)
    end = _parse(end_datetime)

    free_busy = get_free_busy(
        user_emails=attendee_emails,
        start=start,
        end=end,
        interval_minutes=interval_minutes,
    )

    # Summarise counts
    summary = {
        email: {
            "busy_blocks": blocks,
            "busy_block_count": len(blocks),
            "status": "free" if not blocks else "has_conflicts",
        }
        for email, blocks in free_busy.items()
    }

    return json.dumps(summary, indent=2)


# ---------------------------------------------------------------------------
# Tool 2 — ranked candidate slots
# ---------------------------------------------------------------------------

@mcp.tool()
def find_meeting_slots(
    attendee_emails: list[str],
    start_datetime: str,
    end_datetime: str,
    duration_minutes: int = 60,
    max_results: int = 5,
) -> str:
    """
    Find the best available meeting slots for a group of attendees.

    The tool queries each attendee's calendar, then slides a window of
    `duration_minutes` across the search range and ranks slots by the number
    of free attendees (highest first).

    Args:
        attendee_emails: List of Office 365 email addresses.
        start_datetime:  ISO-8601 search-window start (UTC).
                         Example: "2025-06-02T08:00:00"
        end_datetime:    ISO-8601 search-window end (UTC).
                         Example: "2025-06-06T18:00:00"
        duration_minutes: Desired meeting length in minutes (default 60).
        max_results:     Maximum number of candidate slots to return (default 5).

    Returns:
        JSON list of candidate slots, sorted best-first:
        [
          {
            "start": "...",
            "end": "...",
            "free_count": 9,
            "busy_count": 1,
            "busy_users": ["someone@example.com"]
          },
          ...
        ]
    """
    start = _parse(start_datetime)
    end = _parse(end_datetime)

    free_busy = get_free_busy(
        user_emails=attendee_emails,
        start=start,
        end=end,
    )

    slots = find_best_slots(
        free_busy=free_busy,
        start=start,
        end=end,
        duration_minutes=duration_minutes,
        max_results=max_results,
    )

    return json.dumps(slots, indent=2)


# ---------------------------------------------------------------------------
# Tool 3 — human-readable attendee summary
# ---------------------------------------------------------------------------

@mcp.tool()
def get_attendee_summary(
    attendee_emails: list[str],
    start_datetime: str,
    end_datetime: str,
) -> str:
    """
    Return a plain-English summary of attendee availability for a time range.

    Useful for giving the AI model a quick overview before it decides on a slot.

    Args:
        attendee_emails: List of Office 365 email addresses.
        start_datetime:  ISO-8601 range start (UTC).
        end_datetime:    ISO-8601 range end (UTC).

    Returns:
        A plain-text summary string.
    """
    start = _parse(start_datetime)
    end = _parse(end_datetime)

    free_busy = get_free_busy(
        user_emails=attendee_emails,
        start=start,
        end=end,
    )

    total = len(attendee_emails)
    fully_free = [e for e, b in free_busy.items() if not b]
    has_conflicts = [e for e, b in free_busy.items() if b]

    lines = [
        f"Availability check for {total} attendees",
        f"Range: {start_datetime} → {end_datetime} (UTC)",
        "",
        f"✅ Fully free ({len(fully_free)}): {', '.join(fully_free) or 'none'}",
        f"⛔ Has conflicts ({len(has_conflicts)}): {', '.join(has_conflicts) or 'none'}",
        "",
        "Conflict details:",
    ]

    for email in has_conflicts:
        lines.append(f"  {email}:")
        for block in free_busy[email]:
            lines.append(f"    • {block['start']} – {block['end']}  [{block['status']}]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# HTTP app — wraps the MCP ASGI app with Azure AD auth middleware.
# Exposed as `http_app` so uvicorn can import it directly:
#     uvicorn mcp_server.server:http_app
# ---------------------------------------------------------------------------

async def _health(request: Request) -> JSONResponse:
    """Liveness probe — exempt from auth, used by load-balancers."""
    return JSONResponse({"status": "ok"})


def _build_http_app() -> Starlette:
    """Build a Starlette app: health route + MCP ASGI app + auth middleware."""
    mcp_asgi = mcp.streamable_http_app()  # FastMCP exposes ASGI for HTTP transport

    app = Starlette(
        routes=[
            Route("/health", _health),
        ]
    )

    # Mount MCP at /mcp  (clients connect to https://<host>/mcp/)
    app.mount("/mcp", mcp_asgi)

    # Azure AD enforcement — validates Bearer token on every request except /health
    app.add_middleware(AzureADAuthMiddleware)

    return app


http_app = _build_http_app()


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")

    if transport == "http":
        import uvicorn
        port = int(os.environ.get("PORT", "8000"))
        uvicorn.run(http_app, host="0.0.0.0", port=port)
    else:
        # Default: stdio — used by local LangChain agents
        mcp.run(transport="stdio")
