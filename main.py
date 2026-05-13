"""
main.py — demo entry point.

Usage:
    python main.py

Or with a custom request:
    python main.py "Schedule a 30-min daily standup for my team next week"

The attendee list is read from the ATTENDEES env var (comma-separated emails),
or falls back to the demo list below.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()  # load .env file if present

from agent.scheduler_agent import schedule  # noqa: E402  (after dotenv)


# ---------------------------------------------------------------------------
# Demo attendees — replace with real emails or set ATTENDEES env var
# ---------------------------------------------------------------------------

DEFAULT_ATTENDEES = [
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


def main() -> None:
    # Attendees from env or default
    raw = os.environ.get("ATTENDEES", "")
    attendees = [e.strip() for e in raw.split(",") if e.strip()] or DEFAULT_ATTENDEES

    # Date range: next Mon–Fri  (you can override via env vars)
    start_date = os.environ.get("SEARCH_START", "2025-06-02T08:00:00")
    end_date = os.environ.get("SEARCH_END", "2025-06-06T18:00:00")
    duration = int(os.environ.get("MEETING_DURATION_MINUTES", "60"))

    attendees_str = ", ".join(attendees)

    # Accept custom request from CLI arg
    if len(sys.argv) > 1:
        user_request = " ".join(sys.argv[1:])
    else:
        user_request = (
            f"I need to schedule a {duration}-minute meeting with the following "
            f"{len(attendees)} people:\n{attendees_str}\n\n"
            f"Please check their availability between {start_date} and {end_date} UTC "
            f"and recommend the best time slot."
        )

    print("=" * 70)
    print("Calendar Scheduling Agent")
    print("=" * 70)
    print(f"\nRequest:\n{user_request}\n")
    print("-" * 70)

    answer = schedule(user_request)

    print("\nAgent recommendation:")
    print(answer)
    print("=" * 70)


if __name__ == "__main__":
    main()
