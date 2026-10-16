"""Exact decimal money for FDMS.

FDMS signs over amounts in cents (spec §13.2.1 field 6) and validates totals
to the cent (RCPT019, RCPT026, RCPT027, RCPT038, RCPT039). Binary floats are
therefore never used for arithmetic in this package: every amount is a
:class:`decimal.Decimal`, and all rounding goes through :data:`ROUNDING`.

Rounding mode: banker's rounding (half to even). It is Frappe's default
``rounding_method`` and the default of .NET ``Math.Round(decimal)`` - FDMS is
an ASP.NET service - so tax amounts we compute agree with both the books and
the gateway on exact half-cent ties.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

ROUNDING = ROUND_HALF_EVEN
CENT = Decimal("0.01")
MICRO = Decimal("0.000001")  # receiptLinePrice / receiptLineQuantity are Decimal(25,6)


class MoneyError(ValueError):
	"""An amount cannot be represented exactly the way FDMS requires."""


def to_decimal(value, field: str = "amount") -> Decimal:
	"""Coerce ``value`` to an exact Decimal.

	Accepts Decimal, int and str. A float is accepted through its shortest
	round-trip ``repr`` (``0.1`` becomes ``Decimal("0.1")``, never
	``0.1000000000000000055…``); this is how values read from Frappe documents,
	which are floats already rounded to the field precision, enter the library.
	"""
	if isinstance(value, bool):
		raise MoneyError(f"{field}: expected a number, got bool")
	if isinstance(value, Decimal):
		result = value
	elif isinstance(value, int):
		result = Decimal(value)
	elif isinstance(value, float):
		result = Decimal(repr(value))
	elif isinstance(value, str):
		try:
			result = Decimal(value.strip())
		except InvalidOperation as e:
			raise MoneyError(f"{field}: {value!r} is not a number") from e
	else:
		raise MoneyError(f"{field}: expected a number, got {type(value).__name__}")
	if not result.is_finite():
		raise MoneyError(f"{field}: {value!r} is not finite")
	return result


def q2(value: Decimal) -> Decimal:
	"""Round to cents with :data:`ROUNDING`."""
	return value.quantize(CENT, rounding=ROUNDING)


def q6(value: Decimal) -> Decimal:
	"""Round to 6 decimal places (price/quantity precision)."""
	return value.quantize(MICRO, rounding=ROUNDING)


def to_cents(value: Decimal, field: str = "amount") -> int:
	"""Exact integer cents of an amount that is already a whole number of cents.

	Raises :class:`MoneyError` rather than silently rounding: an amount with a
	sub-cent fraction reaching the signing step is a bug upstream.
	"""
	scaled = value * 100
	if scaled != scaled.to_integral_value():
		raise MoneyError(f"{field}: {value} has more than two decimal places")
	return int(scaled)


def from_cents(cents: int) -> Decimal:
	return (Decimal(cents) / 100).quantize(CENT)


def split_proportionally(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
	"""Split ``total`` (a cent amount) across ``weights`` so the parts sum exactly.

	Largest-remainder method on integer cents; ties go to the earlier weight.
	Used to reconcile per-line tax-inclusive totals to the document grand total.
	"""
	if not weights:
		return []
	signed_cents = to_cents(total, "total")
	sign = -1 if signed_cents < 0 else 1
	total_cents = abs(signed_cents)
	abs_weights = [abs(w) for w in weights]
	weight_sum = sum(abs_weights, Decimal(0))
	if weight_sum == 0:
		parts = [0] * len(weights)
		parts[0] = total_cents
	else:
		raw = [Decimal(total_cents) * w / weight_sum for w in abs_weights]
		parts = [int(r.to_integral_value(rounding="ROUND_FLOOR")) for r in raw]
		remainder = total_cents - sum(parts)
		order = sorted(range(len(weights)), key=lambda i: raw[i] - parts[i], reverse=True)
		for i in order[:remainder]:
			parts[i] += 1
	return [from_cents(sign * p) for p in parts]
