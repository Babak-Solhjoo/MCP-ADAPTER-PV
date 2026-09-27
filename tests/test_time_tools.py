from datetime import timezone

import pytest

from mcp_adapter import time_tools as t


def test_now_has_core_fields():
    info = t.now("UTC")
    for key in ("iso", "unix", "utc_offset", "weekday", "iso_week", "timezone"):
        assert key in info
    assert info["utc_offset"] == "+00:00"


def test_convert_between_zones():
    out = t.convert("2026-09-19 14:30", "Europe/Berlin", "America/New_York")
    assert out["converted"]["iso"] == "2026-09-19T08:30:00-04:00"


def test_parse_variants():
    assert t.parse_datetime("2026-09-19T10:00:00Z").utcoffset().total_seconds() == 0
    assert t.parse_datetime("1789000000").year == 2026
    assert t.parse_datetime("1789000000000").year == 2026
    assert t.parse_datetime("19/09/2026 14:30", "UTC").hour == 14
    assert t.parse_datetime("Sep 19 2026 2:30 PM", "UTC").hour == 14
    with pytest.raises(ValueError):
        t.parse_datetime("not a date")


def test_zone_aliases_and_offsets():
    assert t.get_zone("est").key == "America/New_York"
    assert t.get_zone("+05:30").utcoffset(None).total_seconds() == 5.5 * 3600
    assert t.get_zone("") is timezone.utc
    with pytest.raises(ValueError):
        t.get_zone("Mars/Olympus")


def test_add_and_difference():
    added = t.add("2026-09-19", "UTC", days=10, hours=5)
    assert added["result"]["iso"] == "2026-09-29T05:00:00+00:00"
    diff = t.difference("2026-01-01", "2026-09-19T12:00:00")
    assert diff["human"] == "261d 12h"
    assert diff["end_is_after_start"]


def test_world_clock_and_zones():
    wc = t.compare_zones("2026-06-01T12:00:00", ["UTC", "Asia/Tehran"])
    assert wc["zones"][1]["local_time"] == "2026-06-01 15:30:00"
    assert t.list_zones("tehran")["timezones"] == ["Asia/Tehran"]
