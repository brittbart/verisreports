import sys
sys.path.insert(0, '/home/veris/projects/veris')
from datetime import date, time, datetime, timedelta, timezone as tz
from debate_routes import _derive_status

MT = tz(timedelta(hours=-6))
EVENT_DATE = date(2026, 5, 26)
START = time(19, 0)  # 7:00 PM MT

def make_utc(delta_minutes, ref_date=EVENT_DATE):
    event_start = datetime(ref_date.year, ref_date.month, ref_date.day, 19, 0, tzinfo=MT)
    return (event_start + timedelta(minutes=delta_minutes)).astimezone(tz.utc)

def test_upcoming():
    result = _derive_status(EVENT_DATE, None, START, 'MT', _now=make_utc(-180))
    assert result == 'upcoming', f"Expected upcoming, got {result}"
    print("PASS: 3 hours before start -> upcoming")

def test_live():
    result = _derive_status(EVENT_DATE, None, START, 'MT', _now=make_utc(30))
    assert result == 'live', f"Expected live, got {result}"
    print("PASS: 30 min after start -> live")

def test_complete_same_day():
    result = _derive_status(EVENT_DATE, None, START, 'MT', _now=make_utc(240))
    assert result == 'complete', f"Expected complete, got {result}"
    print("PASS: 4 hours after start -> complete")

def test_complete_next_day():
    result = _derive_status(EVENT_DATE, None, START, 'MT', _now=make_utc(0, ref_date=date(2026, 5, 27)))
    assert result == 'complete', f"Expected complete, got {result}"
    print("PASS: next day -> complete")

if __name__ == '__main__':
    test_upcoming()
    test_live()
    test_complete_same_day()
    test_complete_next_day()
    print("\nAll 4 tests passed.")
