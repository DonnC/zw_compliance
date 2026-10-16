from decimal import Decimal as D

import pytest

from zw_compliance.fdms.money import MoneyError, q2, split_proportionally, to_cents, to_decimal


@pytest.mark.parametrize(
	"value,expected",
	[
		(0.1, D("0.1")),
		(115, D("115")),
		("11.50", D("11.50")),
		(D("2.5"), D("2.5")),
		(21474836.47, D("21474836.47")),
	],
)
def test_to_decimal_is_exact(value, expected):
	assert to_decimal(value) == expected


@pytest.mark.parametrize("bad", [True, None, "abc", float("inf"), float("nan"), [1]])
def test_to_decimal_rejects(bad):
	with pytest.raises(MoneyError):
		to_decimal(bad)


def test_q2_is_bankers_rounding():
	assert q2(D("0.125")) == D("0.12")
	assert q2(D("0.135")) == D("0.14")
	assert q2(D("-0.125")) == D("-0.12")


def test_to_cents_refuses_sub_cent_amounts():
	assert to_cents(D("-12.34")) == -1234
	assert to_cents(D("0.05")) == 5
	with pytest.raises(MoneyError):
		to_cents(D("1.005"))


@pytest.mark.parametrize(
	"total,weights",
	[
		("100.00", ["1", "1", "1"]),
		("0.01", ["3", "1"]),
		("-50.00", ["-30", "-20", "-0.01"]),
		("10.00", ["0", "0"]),
		("33.33", ["10", "20", "3.33"]),
	],
)
def test_split_proportionally_sums_exactly(total, weights):
	parts = split_proportionally(D(total), [D(w) for w in weights])
	assert sum(parts) == D(total)
	assert len(parts) == len(weights)
	assert all(p.as_tuple().exponent >= -2 for p in parts)


def test_split_proportionally_keeps_sign():
	assert split_proportionally(D("-0.03"), [D("1"), D("1"), D("1")]) == [D("-0.01")] * 3
