"""Device signing (§13.1), CSR (§4.2) and verification of FDMS signatures (§13.2.2)."""

from datetime import datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from zw_compliance.fdms.csr import build_csr, certificate_valid_till, device_common_name
from zw_compliance.fdms.signer import (
	generate_private_key,
	load_private_key,
	private_key_to_pem,
	sha256_base64,
	sign,
)
from zw_compliance.fdms.signing import receipt_server_string
from zw_compliance.fdms.verify import certificate_thumbprints, select_certificate, verify_signature


def _self_signed(key, cn="FDMS"):
	name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, cn)])
	now = datetime(2026, 1, 1)
	cert = (
		x509.CertificateBuilder()
		.subject_name(name)
		.issuer_name(name)
		.public_key(key.public_key())
		.serial_number(1)
		.not_valid_before(now)
		.not_valid_after(now + timedelta(days=365))
		.sign(key, hashes.SHA256())
	)
	from cryptography.hazmat.primitives import serialization

	return cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.mark.parametrize(
	"key",
	[ec.generate_private_key(ec.SECP256R1()), rsa.generate_private_key(65537, 2048)],
	ids=["ecc", "rsa"],
)
def test_sign_and_verify(key):
	canonical = "322CREDITNOTEUSD852020-09-19T09:23:07-4035"
	sig = sign(key, canonical)
	assert sig.hash == sha256_base64(canonical)
	cert = _self_signed(key)
	assert verify_signature(cert, canonical, {"hash": sig.hash, "signature": sig.signature})
	assert not verify_signature(cert, canonical + "x", {"signature": sig.signature})
	assert not verify_signature(cert, canonical, {"hash": "wrong", "signature": sig.signature})


def test_receipt_server_string_example():
	"""§13.2.2 example concatenation."""
	device_sig = "YyXTSizBBrMjMk4VQL+sCNr+2AC6aQbDAn9JMV2rk3yJ6MDZwie0wqQW3oisNWrMkeZsuAyFSnFkU2A+pKm91sOHVdjeRBebjQgAQQIMTCVIcYrx+BizQ7Ib9iCdsVI+Jel2nThqQiQzfRef6EgtgsaIAN+PV55xSrHvPkIe+Bc="
	assert (
		receipt_server_string(device_sig, 48377, datetime(2019, 9, 19, 15, 43, 12))
		== f"{device_sig}483772019-09-19T15:43:12"
	)


def test_select_certificate_by_thumbprint():
	a, b = (
		_self_signed(ec.generate_private_key(ec.SECP256R1()), "A"),
		_self_signed(ec.generate_private_key(ec.SECP256R1()), "B"),
	)
	thumb = sorted(certificate_thumbprints(b))[0]
	assert select_certificate([a, b], thumb) == b
	assert select_certificate([a, b], None) == a
	assert select_certificate([a], "nope") is None


def test_key_round_trip_and_curve_check():
	key = generate_private_key()
	assert load_private_key(private_key_to_pem(key)).private_numbers() == key.private_numbers()
	with pytest.raises(ValueError):
		load_private_key(private_key_to_pem(ec.generate_private_key(ec.SECP384R1())))


def test_csr_subject_and_certificate_expiry():
	key = generate_private_key()
	pem = build_csr(key, "SN: 001", 187)
	csr = x509.load_pem_x509_csr(pem.encode())
	assert csr.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value == "ZIMRA-SN: 001-0000000187"
	assert device_common_name("SN: 001", 187) == "ZIMRA-SN: 001-0000000187"
	assert csr.is_signature_valid
	assert certificate_valid_till(_self_signed(key)) == datetime(2027, 1, 1)
