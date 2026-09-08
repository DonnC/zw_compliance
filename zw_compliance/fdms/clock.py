"""Server-corrected clock, ported from src/core/clock.ts.

FDMS's HTTP Date header resolves to whole seconds and represents the moment
the response was sent - roughly half a round trip before it's received - so
`learn()` estimates the offset as serverDate + rtt/2 - localNowAtLearnTime,
and folds it in with a decaying exponential moving average once the sample
count warms up past a weight of 1/8.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol


class Clock(Protocol):
	def now(self) -> datetime: ...


class SystemClock:
	def now(self) -> datetime:
		return datetime.now()


class ServerCorrectedClock:
	def __init__(self, base: Clock | None = None):
		self.base: Clock = base or SystemClock()
		self.offset_ms: float = 0.0
		self.samples: int = 0

	def learn(self, server_date: datetime, round_trip_ms: float = 0) -> None:
		local_now_ms = self.base.now().timestamp() * 1000
		server_ms = server_date.timestamp() * 1000
		estimate = server_ms + round_trip_ms / 2 - local_now_ms
		self.samples += 1
		if self.samples == 1:
			self.offset_ms = estimate
		else:
			self.offset_ms += (estimate - self.offset_ms) / min(self.samples, 8)

	def now(self) -> datetime:
		return self.base.now() + timedelta(milliseconds=self.offset_ms)

	@property
	def confidence(self) -> int:
		return self.samples
