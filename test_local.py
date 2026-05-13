"""
test_local.py — Test the full MCP tool chain locally with zero Azure dependencies.

What this tests:
  ✅ Mock calendar data generation (mock_graph_client.py)
  ✅ All three MCP tools: check_availability, find_meeting_slots, get_attendee_summary
  ✅ Slot ranking algorithm (find_best_slots in graph_client.py)
  ✅ Server import and tool wiring (server.py)

What this does NOT need:
  ✗ Azure subscription
  ✗ Azure AD credentials
  ✗ MS Graph API access
  ✗ OpenAI / LLM API key
  ✗ Docker

Run:
    pip install -r requirements.txt
    python test_local.py
"""

import json
import os
import sys

# ── Force mock mode before importing the server ──────────────────────────────
os.environ["USE_MOCK"] = "true"
os.environ["SKIP_AUTH"] = "true"   # only relevant in HTTP mode

# Add the project root to sys.path so imports work from any directory
sys.path.insert(0, os.path.dirname(__file__))

# ── Import the MCP tool functions directly ───────────────────────────────────
# The @mcp.tool() decorator keeps the function callable as a plain Python function,
# so we can test them without spinning up any server or transport.
from mcp_server.server import (  # noqa: E402
    check_availability,
    find_meeting_slots,
    get_attendee_summary,
)

# ── Test parameters ───────────────────────────────────────────────────────────
ATTENDEES = [
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

# Next Mon–Fri (adjust dates as you like)
START = "2025-06-02T08:00:00"
END   = "2025-06-06T18:00:00"

SEP = "=" * 70


def _header(title: str) -> None:
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def test_attendee_summary() -> None:
    _header("TEST 1 — get_attendee_summary")
    result = get_attendee_summary(
        attendee_emails=ATTENDEES,
        start_datetime=START,
        end_datetime=END,
    )
    print(result)
    assert "Availability check for 10 attendees" in result
    assert "Fully free" in result
    assert "Has conflicts" in result
    print("\n✅ PASSED")


def test_check_availability() -> None:
    _header("TEST 2 — check_availability  (raw busy blocks per user)")
    result = check_availability(
        attendee_emails=ATTENDEES[:3],   # just first 3 to keep output short
        start_datetime=START,
        end_datetime=END,
    )
    data = json.loads(result)
    print(json.dumps(data, indent=2))
    assert isinstance(data, dict)
    assert len(data) == 3
    for email, info in data.items():
        assert "busy_blocks" in info
        assert "status" in info
    print("\n✅ PASSED")


def test_find_meeting_slots() -> None:
    _header("TEST 3 — find_meeting_slots  (ranked candidate slots, 60 min)")
    result = find_meeting_slots(
        attendee_emails=ATTENDEES,
        start_datetime=START,
        end_datetime=END,
        duration_minutes=60,
        max_results=5,
    )
    slots = json.loads(result)
    print(json.dumps(slots, indent=2))
    assert isinstance(slots, list)
    assert len(slots) > 0
    # Best slot should have the most free attendees
    best = slots[0]
    assert best["free_count"] >= slots[-1]["free_count"], \
        "Slots should be sorted best-first by free_count"
    print(f"\n  Best slot  : {best['start']} → {best['end']}")
    print(f"  Free count : {best['free_count']} / {len(ATTENDEES)}")
    print(f"  Busy users : {best['busy_users'] or 'none — perfect slot!'}")
    print("\n✅ PASSED")


def test_find_slots_30min() -> None:
    _header("TEST 4 — find_meeting_slots  (30 min standup)")
    result = find_meeting_slots(
        attendee_emails=ATTENDEES,
        start_datetime=START,
        end_datetime=END,
        duration_minutes=30,
        max_results=3,
    )
    slots = json.loads(result)
    print(json.dumps(slots, indent=2))
    assert len(slots) > 0
    print("\n✅ PASSED")


def test_find_slots_subgroup() -> None:
    _header("TEST 5 — find_meeting_slots  (sub-group of 4 people)")
    sub = ["alice@contoso.com", "bob@contoso.com", "carol@contoso.com", "dave@contoso.com"]
    result = find_meeting_slots(
        attendee_emails=sub,
        start_datetime=START,
        end_datetime=END,
        duration_minutes=60,
        max_results=3,
    )
    slots = json.loads(result)
    print(json.dumps(slots, indent=2))
    assert len(slots) > 0
    print("\n✅ PASSED")


# ── Run all tests ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(SEP)
    print("  Calendar MCP Server — Local Tests (mock mode, no Azure)")
    print(SEP)

    passed = 0
    failed = 0
    tests = [
        test_attendee_summary,
        test_check_availability,
        test_find_meeting_slots,
        test_find_slots_30min,
        test_find_slots_subgroup,
    ]

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as exc:
            print(f"\n❌ FAILED: {exc}")
            failed += 1

    print(f"\n{SEP}")
    print(f"  Results: {passed} passed, {failed} failed")
    print(SEP)

    sys.exit(1 if failed else 0)
