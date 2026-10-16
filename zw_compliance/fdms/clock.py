"""Server-corrected clock.

Receipt dates must not be in the future relative to FDMS (RCPT031, Yellow)
and must always increase (RCPT030, Red). A host whose clock drifts would
produce flagged receipts, so the device learns FDMS's clock from the HTTP
``Date`` header of every response and stamps receipts with corrected time.

The ``Date`` header has whole-second resolution and marks when the response
was *sent*, about half a round trip before it was received, hence
``estimate = server + rtt/2 - local_now``. Estimates are blended with an
exponential moving average whose weight settles at 1/8.
"""

from __future__ import annotations

from datetime import datetime, timedelta

MAX_WEIGHT_SAMPLES = 8


def learn_offset(
	previous_offset_ms: float, samples: int, server_time: datetime, round_trip_ms: float, local_now: datetime
) -> tuple[float, int]:
	"""Return the updated ``(offset_ms, samples)`` after one observation."""
	estimate = (server_time - local_now).total_seconds() * 1000 + round_trip_ms / 2
	samples += 1
	if samples == 1:
		return estimate, samples
	return previous_offset_ms + (estimate - previous_offset_ms) / min(samples, MAX_WEIGHT_SAMPLES), samples


def corrected_now(offset_ms: float, local_now: datetime | None = None) -> datetime:
	"""Local wall-clock time corrected to FDMS's clock, to whole seconds."""
	now = (local_now or datetime.now()) + timedelta(milliseconds=offset_ms or 0)
	return now.replace(microsecond=0)
