"""Time utilities exposed by the MCP server (pure Python, stdlib zoneinfo).

Accepted datetime inputs for every function:
  * ``"now"`` (default), ``"today"``, ``"tomorrow"``, ``"yesterday"``
  * ISO 8601 strings (``2026-09-19T14:30:00``, ``2026-09-19 14:30``, ``2026-09-19T14:30:00+02:00``)
  * Unix epoch seconds or milliseconds (``1789000000`` / ``1789000000000``)
  * a few common formats (``19/09/2026 14:30``, ``Sep 19 2026 2:30 PM``)
Naive inputs are interpreted in the timezone given by the ``tz``/``from_tz`` argument (default UTC).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

_COMMON_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%b %d %Y %I:%M %p",
    "%b %d %Y %H:%M",
    "%b %d %Y",
    "%B %d %Y %I:%M %p",
    "%B %d %Y",
    "%d %b %Y %H:%M",
    "%d %b %Y",
    "%d %B %Y",
    "%Y%m%dT%H%M%S",
    "%Y%m%d",
]

_TZ_ALIASES = {
    "utc": "UTC", "gmt": "UTC", "z": "UTC", "local": "local",
    "est": "America/New_York", "edt": "America/New_York", "eastern": "America/New_York",
    "cst": "America/Chicago", "cdt": "America/Chicago", "central": "America/Chicago",
    "mst": "America/Denver", "mdt": "America/Denver", "mountain": "America/Denver",
    "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles", "pacific": "America/Los_Angeles",
    "cet": "Europe/Paris", "cest": "Europe/Paris", "bst": "Europe/London", "wet": "Europe/Lisbon",
    "eet": "Europe/Athens", "ist": "Asia/Kolkata", "jst": "Asia/Tokyo", "kst": "Asia/Seoul",
    "aest": "Australia/Sydney", "aedt": "Australia/Sydney", "aet": "Australia/Sydney",
    "hkt": "Asia/Hong_Kong", "sgt": "Asia/Singapore",
    "irst": "Asia/Tehran", "tehran": "Asia/Tehran", "msk": "Europe/Moscow",
}


def get_zone(name: str | None):
    """Return a tzinfo for an IANA name, common abbreviation, 'local', or fixed offset like '+02:00'."""
    if not name or name.strip() == "":
        return timezone.utc
    key = name.strip()
    lower = key.lower()
    if lower in _TZ_ALIASES:
        key = _TZ_ALIASES[lower]
    if key == "local":
        return datetime.now().astimezone().tzinfo
    m = re.fullmatch(r"(?:utc|gmt)?([+-])(\d{1,2})(?::?(\d{2}))?", key, flags=re.IGNORECASE)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        hours = int(m.group(2))
        minutes = int(m.group(3) or 0)
        label = f"UTC{m.group(1)}{hours:02d}:{minutes:02d}"
        return timezone(sign * timedelta(hours=hours, minutes=minutes), name=label)
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError):
        for tzname in available_timezones():
            if tzname.lower() == lower:
                return ZoneInfo(tzname)
        raise ValueError(
            f"Unknown timezone {name!r}. Use an IANA name such as Europe/Berlin or an offset like +02:00."
        )


def parse_datetime(value: str | int | float | None, tz: str | None = None) -> datetime:
    """Parse *value* into an aware datetime. Naive values are localized to *tz* (default UTC)."""
    zone = get_zone(tz)
    if value is None or (isinstance(value, str) and value.strip().lower() in ("", "now")):
        return datetime.now(tz=zone)
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value.strip())
    ):
        num = float(value)
        if abs(num) > 1e11:  # milliseconds
            num /= 1000.0
        return datetime.fromtimestamp(num, tz=timezone.utc).astimezone(zone)
    text = str(value).strip()
    lowered = text.lower()
    if lowered in ("today", "tomorrow", "yesterday"):
        base = datetime.now(tz=zone).replace(hour=0, minute=0, second=0, microsecond=0)
        shift = {"today": 0, "tomorrow": 1, "yesterday": -1}[lowered]
        return base + timedelta(days=shift)
    iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(iso_text)
    except ValueError:
        dt = None
        for fmt in _COMMON_FORMATS:
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            raise ValueError(
                f"Could not parse datetime {value!r}. Use ISO 8601 (YYYY-MM-DDTHH:MM:SS) or a unix timestamp."
            )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone)
    return dt


def describe(dt: datetime, fmt: str | None = None) -> dict[str, Any]:
    """Return a rich description of an aware datetime."""
    offset = dt.utcoffset() or timedelta(0)
    total_min = int(offset.total_seconds() // 60)
    sign = "+" if total_min >= 0 else "-"
    total_min = abs(total_min)
    tzname = dt.tzname() or ""
    zone_key = getattr(dt.tzinfo, "key", None) or tzname
    iso_year, iso_week, iso_weekday = dt.isocalendar()
    info: dict[str, Any] = {
        "iso": dt.isoformat(),
        "date": dt.strftime("%Y-%m-%d"),
        "time": dt.strftime("%H:%M:%S"),
        "timezone": zone_key,
        "tz_abbreviation": tzname,
        "utc_offset": f"{sign}{total_min // 60:02d}:{total_min % 60:02d}",
        "is_dst": bool(dt.dst()),
        "unix": int(dt.timestamp()),
        "unix_ms": int(dt.timestamp() * 1000),
        "utc_iso": dt.astimezone(timezone.utc).isoformat(),
        "weekday": dt.strftime("%A"),
        "day_of_year": dt.timetuple().tm_yday,
        "iso_week": f"{iso_year}-W{iso_week:02d}-{iso_weekday}",
        "rfc2822": dt.strftime("%a, %d %b %Y %H:%M:%S %z"),
    }
    if fmt:
        info["formatted"] = dt.strftime(fmt)
    return info


def now(tz: str | None = "UTC", fmt: str | None = None) -> dict[str, Any]:
    return describe(datetime.now(tz=get_zone(tz)), fmt)


def convert(value: str, from_tz: str | None = "UTC", to_tz: str | None = "UTC",
            fmt: str | None = None) -> dict[str, Any]:
    dt = parse_datetime(value, from_tz)
    target = dt.astimezone(get_zone(to_tz))
    return {"input": describe(dt), "converted": describe(target, fmt)}


def add(value: str = "now", tz: str | None = "UTC", weeks: float = 0, days: float = 0, hours: float = 0,
        minutes: float = 0, seconds: float = 0, fmt: str | None = None) -> dict[str, Any]:
    dt = parse_datetime(value, tz)
    delta = timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)
    result = dt + delta
    return {"start": describe(dt), "delta_seconds": delta.total_seconds(), "result": describe(result, fmt)}


def _humanize(seconds: float) -> str:
    neg = seconds < 0
    s = abs(int(round(seconds)))
    parts = []
    for label, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        qty, s = divmod(s, size)
        if qty or (label == "s" and not parts):
            parts.append(f"{qty}{label}")
    text = " ".join(parts)
    return f"-{text}" if neg else text


def difference(start: str, end: str = "now", tz: str | None = "UTC") -> dict[str, Any]:
    a = parse_datetime(start, tz)
    b = parse_datetime(end, tz)
    delta = b - a
    secs = delta.total_seconds()
    return {
        "start": a.isoformat(),
        "end": b.isoformat(),
        "seconds": secs,
        "minutes": secs / 60,
        "hours": secs / 3600,
        "days": secs / 86400,
        "weeks": secs / 604800,
        "human": _humanize(secs),
        "end_is_after_start": secs >= 0,
    }


def format_datetime(value: str = "now", fmt: str = "%Y-%m-%d %H:%M:%S",
                    tz: str | None = "UTC") -> dict[str, Any]:
    dt = parse_datetime(value, tz)
    return {"iso": dt.isoformat(), "formatted": dt.strftime(fmt), "format": fmt,
            "timezone": describe(dt)["timezone"]}


def list_zones(filter_text: str | None = None, limit: int = 100) -> dict[str, Any]:
    zones = sorted(available_timezones())
    if filter_text:
        f = filter_text.lower()
        zones = [z for z in zones if f in z.lower()]
    total = len(zones)
    return {"total": total, "timezones": zones[: max(1, limit)]}


DEFAULT_WORLD_CLOCK = [
    "UTC", "America/Los_Angeles", "America/New_York", "Europe/London", "Europe/Berlin",
    "Asia/Tehran", "Asia/Kolkata", "Asia/Shanghai", "Asia/Tokyo", "Australia/Sydney",
]


def compare_zones(value: str = "now", zones: list[str] | None = None,
                  from_tz: str | None = "UTC") -> dict[str, Any]:
    """Show the same instant in several timezones (a world clock)."""
    dt = parse_datetime(value, from_tz)
    rows = []
    for z in zones or DEFAULT_WORLD_CLOCK:
        local = dt.astimezone(get_zone(z))
        rows.append({
            "timezone": z,
            "local_time": local.strftime("%Y-%m-%d %H:%M:%S"),
            "utc_offset": describe(local)["utc_offset"],
            "weekday": local.strftime("%A"),
            "is_dst": bool(local.dst()),
        })
    return {"instant_utc": dt.astimezone(timezone.utc).isoformat(), "zones": rows}


def unix_time(value: str = "now", tz: str | None = "UTC") -> dict[str, Any]:
    dt = parse_datetime(value, tz)
    return {"unix": int(dt.timestamp()), "unix_ms": int(dt.timestamp() * 1000), "iso": dt.isoformat()}
