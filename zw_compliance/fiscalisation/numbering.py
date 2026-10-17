"""Global receipt numbering.

ZIMRA lets a device start its receiptGlobalNo again at 1 only with the first
receipt of a new fiscal day (RCPT012 accepts 1 there). Each device chooses
when that happens (``global_number_reset``); a restart is only ever applied
when a new fiscal day is created, never mid-day.

* Near Limit (default): numbers continue across days; the first new day after
  the last number passed 90% of the limit starts again at 1. Reaching the
  limit during a day closes that day so the next receipt can restart.
* Every Fiscal Day: every new day starts again at 1.
* Never: numbers never restart; at the limit invoices are refused.
"""

from __future__ import annotations

import frappe
from frappe import _

from .constants import GLOBAL_RESET_EVERY_DAY, GLOBAL_RESET_NEAR_LIMIT, GLOBAL_RESET_NEVER

# receiptGlobalNo is an int in the API: the largest number a receipt can carry.
MAX_GLOBAL_NO = 2**31 - 1


def reset_policy(device) -> str:
	return device.get("global_number_reset") or GLOBAL_RESET_NEAR_LIMIT


def limit(device) -> int:
	value = device.get("global_number_limit") or 0
	return value if 0 < value < MAX_GLOBAL_NO else MAX_GLOBAL_NO


def at_limit(device) -> bool:
	return (device.last_receipt_global_no or 0) >= limit(device)


def restarts_in_new_day(device) -> bool:
	"""Whether the next new fiscal day starts global numbering again at 1."""
	last = device.last_receipt_global_no or 0
	policy = reset_policy(device)
	if policy == GLOBAL_RESET_NEVER:
		return False
	if at_limit(device):
		return True
	if policy == GLOBAL_RESET_EVERY_DAY:
		return last > 0
	return last * 10 > limit(device) * 9


def require_number_available(device) -> None:
	"""Refuse invoicing when the device can issue no further global number."""
	if reset_policy(device) == GLOBAL_RESET_NEVER and at_limit(device):
		frappe.throw(
			_(
				"Fiscal device {0} reached its global receipt number limit ({1}) and is set never to restart numbering. Change its Global Number Reset to continue invoicing."
			).format(frappe.bold(device.device_name), limit(device)),
			title=_("Global Number Limit Reached"),
		)
