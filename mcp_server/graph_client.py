"""
Microsoft Graph API client for calendar free/busy queries.

Authentication: OAuth2 client-credentials flow (app-only, no user sign-in needed
when the app has Calendars.Read application permission in Azure AD).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import msal


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def _get_access_token() -> str:
    """Obtain a bearer token using the client-credentials flow."""
    tenant_id = os.environ["AZURE_TENANT_ID"]
    client_id = os.environ["AZURE_CLIENT_ID"]
    client_secret = os.environ["AZURE_CLIENT_SECRET"]

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret,
    )

    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )

    if "access_token" not in result:
        error = result.get("error_description", result.get("error", "unknown"))
        raise RuntimeError(f"Failed to acquire MS Graph token: {error}")

    return result["access_token"]


# ---------------------------------------------------------------------------
# Free / busy
# ---------------------------------------------------------------------------

def get_free_busy(
    user_emails: list[str],
    start: datetime,
    end: datetime,
    interval_minutes: int = 30,
) -> dict[str, Any]:
    """
    Call the Graph ``getSchedule`` endpoint and return raw schedule data.

    Returns a dict keyed by email → list of busy blocks:
        {
          "user@example.com": [
              {"start": "2025-06-01T09:00:00", "end": "2025-06-01T10:00:00",
               "status": "busy"},
              ...
          ],
          ...
        }
    """
    token = _get_access_token()

    # We call getSchedule as an app using the /users/{organizer}/calendar path.
    # The organizer is the first user in the list (or a dedicated service account).
    organizer = user_emails[0]

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = {
        "schedules": user_emails,
        "startTime": {
            "dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        },
        "endTime": {
            "dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        },
        "availabilityViewInterval": interval_minutes,
    }

    url = f"https://graph.microsoft.com/v1.0/users/{organizer}/calendar/getSchedule"

    with httpx.Client(timeout=30) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    result: dict[str, list[dict]] = {}

    for schedule in data.get("value", []):
        email = schedule["scheduleId"]
        busy_blocks = []
        for item in schedule.get("scheduleItems", []):
            # status can be: busy, tentative, oof, workingElsewhere, free
            if item.get("status", "free") not in ("free",):
                busy_blocks.append(
                    {
                        "start": item["start"]["dateTime"],
                        "end": item["end"]["dateTime"],
                        "status": item["status"],
                    }
                )
        result[email] = busy_blocks

    return result


# ---------------------------------------------------------------------------
# Best-slot finder (pure logic, no API call)
# ---------------------------------------------------------------------------

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
    Given a free/busy dict (output of get_free_busy), slide a window of
    ``duration_minutes`` over the search range in ``slot_step_minutes`` steps
    and rank candidate slots by how many attendees are free.

    Returns up to ``max_results`` slots, sorted best-first:
        [
          {
            "start": "2025-06-01T09:00:00+00:00",
            "end":   "2025-06-01T10:00:00+00:00",
            "free_count": 10,
            "busy_count": 0,
            "busy_users": []
          },
          ...
        ]
    """
    total_users = len(free_busy)
    step = timedelta(minutes=slot_step_minutes)
    duration = timedelta(minutes=duration_minutes)

    # Build a set of (email, busy_start, busy_end) intervals
    busy_intervals: list[tuple[str, datetime, datetime]] = []
    for email, blocks in free_busy.items():
        for block in blocks:
            bs = _parse_dt(block["start"])
            be = _parse_dt(block["end"])
            busy_intervals.append((email, bs, be))

    candidates: list[dict[str, Any]] = []
    cursor = start

    while cursor + duration <= end:
        slot_start = cursor
        slot_end = cursor + duration

        # Only consider slots within working hours
        if slot_start.hour >= work_hour_start and slot_end.hour <= work_hour_end:
            busy_users_here: list[str] = []
            for email, bs, be in busy_intervals:
                # overlap check
                if bs < slot_end and be > slot_start:
                    if email not in busy_users_here:
                        busy_users_here.append(email)

            free_count = total_users - len(busy_users_here)
            candidates.append(
                {
                    "start": slot_start.isoformat(),
                    "end": slot_end.isoformat(),
                    "free_count": free_count,
                    "busy_count": len(busy_users_here),
                    "busy_users": busy_users_here,
                }
            )

        cursor += step

    # Sort: most free first, then earliest
    candidates.sort(key=lambda x: (-x["free_count"], x["start"]))
    return candidates[:max_results]


def _parse_dt(value: str) -> datetime:
    """Parse an ISO datetime string, ensuring UTC awareness."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
