"""The error catalogue must cover every code the spec defines, with the spec's colours."""

from zw_compliance.fdms.errors import CATALOGUE, DAY_CLOSE, REQUEST, VALIDATION, describe, support_code

# §8.2.1 validation table: code -> (colour, requires previous receipt)
SPEC_VALIDATION = {
	"RCPT010": ("Red", False),
	"RCPT011": ("Red", True),
	"RCPT012": ("Red", True),
	"RCPT013": ("Red", False),
	"RCPT014": ("Yellow", False),
	"RCPT015": ("Red", False),
	"RCPT016": ("Red", False),
	"RCPT017": ("Red", False),
	"RCPT018": ("Red", False),
	"RCPT019": ("Red", False),
	"RCPT020": ("Red", False),
	"RCPT021": ("Red", False),
	"RCPT022": ("Red", False),
	"RCPT023": ("Red", False),
	"RCPT024": ("Red", False),
	"RCPT025": ("Red", False),
	"RCPT026": ("Red", False),
	"RCPT027": ("Red", False),
	"RCPT028": ("Red", False),
	"RCPT029": ("Red", False),
	"RCPT030": ("Red", True),
	"RCPT031": ("Yellow", False),
	"RCPT032": ("Red", False),
	"RCPT033": ("Red", False),
	"RCPT034": ("Red", False),
	"RCPT035": ("Red", False),
	"RCPT036": ("Red", False),
	"RCPT037": ("Red", False),
	"RCPT038": ("Red", False),
	"RCPT039": ("Red", False),
	"RCPT040": ("Red", False),
	"RCPT041": ("Yellow", False),
	"RCPT042": ("Red", False),
	"RCPT043": ("Red", False),
	"RCPT047": ("Red", False),
	"RCPT048": ("Red", False),
}
SPEC_REQUEST = (
	[f"DEV{n:02d}" for n in range(1, 16)]
	+ ["RCPT01", "RCPT02", "FISC01", "FISC03", "FISC04"]
	+ [f"FILE0{n}" for n in range(1, 6)]
)
SPEC_DAY_CLOSE = [
	"BadCertificateSignature",
	"MissingReceipts",
	"ReceiptsWithValidationErrors",
	"CountersMismatch",
]


def test_validation_codes_match_spec_table():
	for code, (colour, needs_previous) in SPEC_VALIDATION.items():
		entry = CATALOGUE[code]
		assert entry.kind == VALIDATION
		assert (entry.colour, entry.needs_previous) == (colour, needs_previous), code
	assert {c for c, e in CATALOGUE.items() if e.kind == VALIDATION} == set(SPEC_VALIDATION)


def test_request_and_day_close_codes_present():
	for code in SPEC_REQUEST:
		assert CATALOGUE[code].kind == REQUEST
	for code in SPEC_DAY_CLOSE:
		assert CATALOGUE[code].kind == DAY_CLOSE


def test_every_entry_has_guidance():
	assert all(e.message and e.fix for e in CATALOGUE.values())


def test_describe_and_support_code():
	assert describe("DEV02").message == "Activation key is incorrect"
	assert describe(None) is None
	assert support_code("RCPT020", "0hnoabb4t00a3xyz:1") == "RCPT020-0HNOABB4T00A"
	assert support_code("RCPT020", None) == "RCPT020"
