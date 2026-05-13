"""
Mock MS Graph client — replaces graph_client.py when USE_MOCK=true.

Generates realistic fake free/busy data for 10 people across a work week
so you can develop and test the full agent → MCP → slot-ranking flow
without any Azure account, credentials, or internet connection.

Busy patterns are intentionally varied:
  - Some people are mostly free
  - Some are heavily booked mornings
  - Some have afternoon blocks
  - Lunch (12:00-13:00) is busy for everyone on Wednesday
  - One person is on leave (fully blocked) on Friday
  This makes the slot-ranking algorithm return interesting, non-trivial results.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# Re-export find_best_slots from the real module — pure Python, no Azure needed
from .graph_client import find_best_slots  # noqa: F401


# ---------------------------------------------------------------------------
# Fake attendee roster
# ---------------------------------------------------------------------------

MOCK_USERS = [
    "alice@contoso.com",
    "bob@contoso.com",
    "carol@contoso.com",
    "dave@contoso.com",
    "eve@contoso.com",
    "frank@contoso.com",
    "grace@contoso.com",
    "heidi@contoso.com",
    "ivan@contoso.com",
    "judy@contoso.com",
]


# ---------------------------------------------------------------------------
# Busy block definitions
# Tuples of (weekday 0=Mon, hour_start, hour_end, status)
# ---------------------------------------------------------------------------

_BUSY_PATTERNS: dict[str, list[tuple[int, int, int, str]]] = {
    "alice@contoso.com": [
        (0, 9, 10, "busy"),    # Monday standup
        (1, 14, 15, "busy"),   # Tuesday 1:1
        (2, 12, 13, "busy"),   # Wednesday lunch all-hands
        (3, 10, 11, "busy"),   # Thursday review
    ],
    "bob@contoso.com": [
        (0, 9, 11, "busy"),    # Monday planning (2h)
        (1, 9, 10, "busy"),
        (2, 9, 10, "busy"),
        (2, 12, 13, "busy"),
        (3, 15, 17, "busy"),   # Thursday afternoon block
        (4, 9, 18, "oof"),     # Friday — out of office all day
    ],
    "carol@contoso.com": [
        (0, 10, 11, "busy"),
        (1, 11, 12, "busy"),
        (2, 12, 13, "busy"),
        (4, 14, 15, "busy"),
    ],
    "dave@contoso.com": [
        (0, 9, 10, "busy"),
        (0, 14, 16, "busy"),   # Monday long review
        (2, 10, 11, "busy"),
        (2, 12, 13, "busy"),
        (3, 9, 10, "busy"),
    ],
    "eve@contoso.com": [
        (1, 9, 10, "busy"),
        (2, 12, 13, "busy"),
        (3, 11, 12, "busy"),
        (4, 10, 11, "busy"),
    ],
    "frank@contoso.com": [
        (0, 9, 10, "busy"),
        (0, 11, 12, "busy"),
        (1, 14, 15, "tentative"),
        (2, 12, 13, "busy"),
        (3, 9, 11, "busy"),    # Thursday morning block
    ],
    "grace@contoso.com": [
        (2, 12, 13, "busy"),
        (4, 9, 10, "busy"),
    ],
    "heidi@contoso.com": [
        (0, 10, 11, "busy"),
        (1, 10, 12, "busy"),   # Tuesday long meeting
        (2, 12, 13, "busy"),
        (3, 14, 15, "busy"),
    ],
    "ivan@contoso.com": [
        (0, 9, 10, "busy"),
        (2, 12, 13, "busy"),
        (3, 10, 11, "tentative"),
        (4, 11, 12, "busy"),
    ],
    "judy@contoso.com": [
        (1, 9, 10, "busy"),
        (2, 12, 13, "busy"),
        (2, 15, 16, "busy"),
        (4, 9, 10, "busy"),
    ],
}


# ---------------------------------------------------------------------------
# Public interface — same signature as graph_client.get_free_busy
# ---------------------------------------------------------------------------

def get_free_busy(
    user_emails: list[str],
    start: datetime,
    end: datetime,
    interval_minutes: int = 30,
) -> dict[str, Any]:
    """
    Return fake free/busy data for the given users over the date range.

    The busy blocks are generated from _BUSY_PATTERNS relative to the Monday
    of the week containing `start`.  If the search range spans multiple weeks,
    the same weekly pattern repeats.

    Same return format as the real graph_client.get_free_busy.
    """
    # Ensure start is UTC-aware
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    # Find the Monday of the week containing `start`
    monday = start - timedelta(days=start.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)

    result: dict[str, list[dict]] = {}

    for email in user_emails:
        patterns = _BUSY_PATTERNS.get(email, [])
        blocks: list[dict] = []

        # Generate blocks for enough weeks to cover the range
        week_offset = timedelta(weeks=0)
        while monday + week_offset < end:
            for (weekday, h_start, h_end, status) in patterns:
                block_start = monday + week_offset + timedelta(
                    days=weekday, hours=h_start
                )
                block_end = monday + week_offset + timedelta(
                    days=weekday, hours=h_end
                )
                # Only include if it overlaps with [start, end]
                if block_start < end and block_end > start:
                    blocks.append(
                        {
                            "start": block_start.isoformat(),
                            "end": block_end.isoformat(),
                            "status": status,
                        }
                    )
            week_offset += timedelta(weeks=1)

        result[email] = blocks

    return result
