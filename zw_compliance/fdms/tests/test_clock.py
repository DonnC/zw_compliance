from datetime import datetime, timedelta

from zw_compliance.fdms.clock import corrected_now, learn_offset


def test_first_sample_is_taken_as_is_then_blended():
	local = datetime(2026, 10, 1, 12, 0, 0)
	offset, n = learn_offset(0, 0, local + timedelta(seconds=10), 0, local)
	assert (offset, n) == (10_000, 1)
	offset, n = learn_offset(offset, n, local + timedelta(seconds=12), 0, local)
	assert n == 2 and offset == 11_000


def test_round_trip_half_is_added():
	local = datetime(2026, 10, 1, 12, 0, 0)
	offset, _ = learn_offset(0, 0, local, 400, local)
	assert offset == 200


def test_corrected_now_whole_seconds():
	local = datetime(2026, 10, 1, 12, 0, 0, 900_000)
	assert corrected_now(1500, local) == datetime(2026, 10, 1, 12, 0, 2)
