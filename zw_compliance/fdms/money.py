"""Integer-cents money handling, ported from src/core/money.ts.

FDMS signs over amounts in cents. Floats are never trusted: every amount
that enters this package is either already an integer number of cents, or
an exact decimal string/whole number that is converted to cents without
float arithmetic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CENTS_RE = re.compile(r"^(-?)(\d+)(?:\.(\d{1,2}))?$")


class MoneyError(ValueError):
	pass


@dataclass(frozen=True)
class Money:
	cents: int

	def __post_init__(self):
		if not isinstance(self.cents, int):
			raise MoneyError(f"Money.cents must be an int, got {self.cents!r}")


def cents(amount: str) -> Money:
	"""Parse an exact decimal string ("11.50", "-2", "2.5") into cents."""
	m = _CENTS_RE.match(amount.strip())
	if not m:
		raise MoneyError(f"Not a valid decimal amount: {amount!r}")
	sign, whole, frac = m.group(1), m.group(2), m.group(3) or ""
	frac = (frac + "00")[:2]
	value = int(whole) * 100 + int(frac)
	return Money(-value if sign else value)


def from_cents(value: int) -> Money:
	if not isinstance(value, int):
		raise MoneyError(f"from_cents expects an int, got {value!r}")
	return Money(value)


def is_money(value) -> bool:
	return isinstance(value, Money)


def amount_to_cents(value, field: str) -> int:
	"""Accept a Money, or a whole number of major units (int/float with no
	fractional part). A fractional float is a hard error - never silently
	rounded, since e.g. 11.5 and 11.499999 are the same float.
	"""
	if isinstance(value, Money):
		return value.cents
	if isinstance(value, bool):
		raise MoneyError(f"{field} must be a number or Money, got bool")
	if isinstance(value, int):
		return value * 100
	if isinstance(value, float):
		if value != value or value in (float("inf"), float("-inf")):
			raise MoneyError(f"{field} must be finite")
		if value != int(value):
			raise MoneyError(
				f"{field} has a fractional part ({value}); use money.cents('...') for sub-unit amounts"
			)
		return int(value) * 100
	raise MoneyError(f"{field} must be a number or Money, got {type(value).__name__}")


def cents_to_amount(value: int) -> float:
	"""Cents -> the decimal major-unit number sent on the wire."""
	result = value / 100
	return int(result) if result == int(result) else result


def format_cents(value: int) -> str:
	sign = "-" if value < 0 else ""
	value = abs(value)
	return f"{sign}{value // 100}.{value % 100:02d}"


def money_from_json_number(value, field: str) -> Money:
	"""For amounts arriving through JSON (POS payload, HTTP body): integers
	pass through, fractional numbers are converted via a 2-decimal round
	trip and rejected if that loses precision (more than 2 decimal places).
	"""
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		raise MoneyError(f"{field} must be a number")
	if isinstance(value, int) or float(value).is_integer():
		return Money(int(value) * 100)
	text = f"{value:.2f}"
	if abs(float(text) - value) > 1e-9:
		raise MoneyError(f"{field} has more than two decimal places: {value!r}")
	return cents(text)


def receipt_input_from_json(input_dict: dict) -> dict:
	"""Apply money_from_json_number to every lines[].price and payments[].amount."""
	out = dict(input_dict)
	out["lines"] = [
		{**line, "price": money_from_json_number(line["price"], f"lines[{i}].price")}
		for i, line in enumerate(input_dict.get("lines", []))
	]
	out["payments"] = [
		{**p, "amount": money_from_json_number(p["amount"], f"payments[{i}].amount")}
		for i, p in enumerate(input_dict.get("payments", []))
	]
	return out
