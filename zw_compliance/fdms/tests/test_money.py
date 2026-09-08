import pytest

from zw_compliance.fdms.money import (
	Money,
	MoneyError,
	amount_to_cents,
	cents,
	cents_to_amount,
	format_cents,
	money_from_json_number,
)


def test_cents_parses_exact_decimals():
	assert cents("11.50").cents == 1150
	assert cents("2.5").cents == 250
	assert cents("-2").cents == -200
	assert cents("0.01").cents == 1


def test_cents_rejects_garbage():
	with pytest.raises(MoneyError):
		cents("11.5.0")
	with pytest.raises(MoneyError):
		cents("abc")


def test_amount_to_cents_whole_number_ok():
	assert amount_to_cents(115, "price") == 11500
	assert amount_to_cents(Money(1150), "price") == 1150


def test_amount_to_cents_rejects_fractional_float():
	with pytest.raises(MoneyError):
		amount_to_cents(11.5, "price")


def test_cents_to_amount_round_trip():
	assert cents_to_amount(11500) == 115
	assert cents_to_amount(11550) == 115.5


def test_format_cents():
	assert format_cents(1150) == "11.50"
	assert format_cents(-150) == "-1.50"


def test_money_from_json_number():
	assert money_from_json_number(2, "amount").cents == 200
	assert money_from_json_number(2.5, "amount").cents == 250
	with pytest.raises(MoneyError):
		money_from_json_number(2.567, "amount")
