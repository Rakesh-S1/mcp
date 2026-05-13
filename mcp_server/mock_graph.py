"""
mock_graph.py — Drop-in replacement for graph_client.py that uses
                generated fake calendar data. No Azure credentials needed.

Set env var USE_MOCK=1 to activate, or import directly in tests.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any


# Seed for reproducible results in tests
random.seed(42)


def get_free_busy(
    user_emails: list[str],
    start: datetime,
    end: datetime,
    interval_minutes: int = 30,
) -> dict[str, list[dict]]:
    """
    Generate realistic fake busy blocks for each user.
    Each user gets 0–4 random meetings per working day.
    """
    result: dict[str, list[dict]] = {}
    rng = random.Random(hash(tuple(sorted(user_emails))))  # deterministic per group

    current = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= end:
        # Skip weekends
        if current.weekday() < 5:
            for email in user_emails:
                num_meetings = rng.randint(0, 3)
                for _ in range(num_meetings):
                    hour = rng.choice([9, 10, 11, 13, 14, 15, 16])
                    duration = rng.choice([30, 60, 90])
                    meeting_start = current.replace(
                        hour=hour, minute=0, tzinfo=timezone.utc
                    )
                    meeting_end = meeting_start + timedelta(minutes=duration)
                    if meeting_end.hour <= 18:
                        result.setdefault(email, []).append(
                            {
                                "start": meeting_start.isoformat(),
                                "end": meeting_end.isoformat(),
                                "status": rng.choice(["busy", "tentative"]),
                            }
                        )
        current += timedelta(days=1)

    # Ensure every email has an entry (even if empty)
    for email in user_emails:
        result.setdefault(email, [])

    return result


def find_best_slots(
    free_busy: dict[str, list[dict]],
    start: datetime,
    end: datetime,
    duration_minutes: int = 60,
    slot_step_minutes: int = 30,
    work_hour_start: int = 9,
    work_hour_end: int = 18,
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """
    Identical slot-ranking logic as the real graph_client — works on the
    fake free_busy dict produced above.
    """
    from mcp_server.graph_client import find_best_slots as _real_find
    return _real_find(
        free_busy=free_busy,
        start=start,
        end=end,
        duration_minutes=duration_minutes,
        slot_step_minutes=slot_step_minutes,
        work_hour_start=work_hour_start,
        work_hour_end=work_hour_end,
        max_results=max_results,
    )
