"""event_time.py - the one place that turns an event's (event_date, start_time, timezone) into real instants.
events.timezone may hold an IANA name ('America/Phoenix', 'America/New_York') or an abbreviation. Abbreviations
resolve exactly as Postgres AT TIME ZONE resolves them, so the page window (here) and the health/live-event
predicates (SQL) agree: EST/CST/MST/PST and EDT/CDT/MDT/PDT are FIXED offsets; ET/CT/MT/PT are the DST-aware
IANA zones (Postgres does not accept ET/CT/MT/PT at all, so new events must be stored as IANA names).
Unknown values fall back to UTC and are logged - a wrong-by-hours window is exactly what this module replaces,
but a 500 on the public page is worse."""
import logging
from datetime import datetime, date, time, timedelta, timezone as _fixed, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
log = logging.getLogger(__name__)
WINDOW_BEFORE = timedelta(minutes=30)
WINDOW_AFTER = timedelta(hours=3)
_IANA_FOR_GENERIC = {'ET': 'America/New_York', 'CT': 'America/Chicago', 'MT': 'America/Denver', 'PT': 'America/Los_Angeles'}
_FIXED_HOURS = {'EST': -5, 'EDT': -4, 'CST': -6, 'CDT': -5, 'MST': -7, 'MDT': -6, 'PST': -8, 'PDT': -7, 'UTC': 0, 'GMT': 0, 'Z': 0}
def zone(tz_value):
    """tzinfo for an events.timezone value. Never raises; unknown -> UTC with a log line."""
    v = (tz_value or '').strip()
    if not v:
        log.warning('event_time.zone: empty timezone, using UTC')
        return _fixed.utc
    up = v.upper()
    if up in _FIXED_HOURS:
        return _fixed(timedelta(hours=_FIXED_HOURS[up]), name=up)
    if up in _IANA_FOR_GENERIC:
        return ZoneInfo(_IANA_FOR_GENERIC[up])
    try:
        return ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning('event_time.zone: unknown timezone %r, using UTC', tz_value)
        return _fixed.utc
def start_dt(event_date, start_time, tz_value):
    """Aware datetime of the event start in the event's own zone, or None if date/time missing."""
    if event_date is None or start_time is None:
        return None
    return datetime.combine(event_date, start_time).replace(tzinfo=zone(tz_value))
def start_iso(event_date, start_time, tz_value):
    """'YYYY-MM-DDTHH:MM:SS-07:00' (the shape the templates already consume), or ''."""
    s = start_dt(event_date, start_time, tz_value)
    return s.isoformat() if s else ''
def window(event_date, start_time, tz_value, before=WINDOW_BEFORE, after=WINDOW_AFTER):
    """(window_start_utc, window_end_utc) or None. The live window: start - before .. start + after."""
    s = start_dt(event_date, start_time, tz_value)
    if s is None:
        return None
    return (s - before).astimezone(_fixed.utc), (s + after).astimezone(_fixed.utc)
def derive_status(event_date, start_time=None, tz_value=None, _now=None):
    """'complete' | 'live' | 'upcoming' - same semantics as debate_routes._derive_status, zone-correct."""
    if event_date is None:
        return 'complete'
    z = zone(tz_value)
    now_utc = _now if _now is not None else datetime.now(_fixed.utc)
    today_local = now_utc.astimezone(z).date()
    if event_date < today_local:
        return 'complete'
    if event_date == today_local:
        if start_time is not None:
            w0, w1 = window(event_date, start_time, tz_value)
            if w0 <= now_utc <= w1:
                return 'live'
            return 'complete' if now_utc > w1 else 'upcoming'
        return 'live'
    return 'upcoming'
def is_postgres_zone(tz_value):
    """True if Postgres AT TIME ZONE will accept the value (IANA name or a fixed abbreviation). ET/CT/MT/PT are NOT."""
    v = (tz_value or '').strip()
    if not v or v.upper() in _IANA_FOR_GENERIC:
        return False
    if v.upper() in _FIXED_HOURS:
        return True
    try:
        ZoneInfo(v); return True
    except (ZoneInfoNotFoundError, ValueError):
        return False
