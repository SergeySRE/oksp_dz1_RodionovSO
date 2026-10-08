from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import duration_is_suitable, intervals_overlap, merged_seconds

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_adjacent_intervals_do_not_overlap():
    assert not intervals_overlap(START, START + timedelta(minutes=30),
                                 START + timedelta(minutes=30), START + timedelta(hours=1))


def test_intervals_overlap():
    assert intervals_overlap(START, START + timedelta(hours=1),
                             START + timedelta(minutes=30), START + timedelta(hours=2))


def test_duration_must_match_service():
    slot = SimpleNamespace(starts_at=START, ends_at=START + timedelta(minutes=30))
    assert duration_is_suitable(slot, SimpleNamespace(duration_minutes=30))
    assert not duration_is_suitable(slot, SimpleNamespace(duration_minutes=45))


def test_schedule_overlap_is_counted_once():
    intervals = [(START, START + timedelta(hours=1)),
                 (START + timedelta(minutes=30), START + timedelta(hours=2))]
    assert merged_seconds(intervals) == 7200


def test_empty_schedule_has_zero_duration():
    assert merged_seconds([]) == 0
