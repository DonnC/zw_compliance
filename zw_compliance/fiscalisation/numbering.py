"""Global receipt numbering.

ZIMRA allows receiptGlobalNo to start again at 1 only with the first receipt
of a fiscal day (field rules for receiptGlobalNo; §9 req. 6-7; RCPT012 is Red
for a restart mid-day). So a restart is only ever applied when a new fiscal
day is created. Each device chooses when (``global_number_reset``):

* Near Limit (default): numbers continue across days; the first new day after
  the last number passed 90% of the limit starts again at 1, so the restart
  normally lands on an ordinary day boundary. With the default limit numbering
  simply continues for the life of the device.
* Every Fiscal Day: every new day starts again at 1.

receiptGlobalNo is an Int on the wire, and after the limit the only number
ZIMRA accepts is 1 in a new day. So there is no policy that stops invoicing:
under either policy, reaching the limit during a day closes that day (a close
is allowed at any time; ZIMRA sets no minimum day length) and the next receipt
opens a new day at 1. Managers are told, because a Z report then appears
during trading.
"""

from __future__ import annotations

from .constants import GLOBAL_RESET_EVERY_DAY, GLOBAL_RESET_NEAR_LIMIT

# receiptGlobalNo is an Int in the API (and a signed int column here).
MAX_GLOBAL_NO = 2**31 - 1


def reset_policy(device) -> str:
	return (
		GLOBAL_RESET_EVERY_DAY
		if device.get("global_number_reset") == GLOBAL_RESET_EVERY_DAY
		else GLOBAL_RESET_NEAR_LIMIT
	)


def limit(device) -> int:
	value = device.get("global_number_limit") or 0
	return value if 0 < value < MAX_GLOBAL_NO else MAX_GLOBAL_NO


def at_limit(device) -> bool:
	return (device.last_receipt_global_no or 0) >= limit(device)


def restarts_in_new_day(device) -> bool:
	"""Whether the next new fiscal day starts global numbering again at 1."""
	last = device.last_receipt_global_no or 0
	if reset_policy(device) == GLOBAL_RESET_EVERY_DAY:
		return last > 0
	return last * 10 > limit(device) * 9
