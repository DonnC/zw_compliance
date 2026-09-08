from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256

from zw_compliance.fdms.signer import der_to_p1363, p1363_to_der


def test_p1363_der_round_trip():
	key = ec.generate_private_key(ec.SECP256R1())
	der_sig = key.sign(b"hello world", ec.ECDSA(SHA256()))
	p1363 = der_to_p1363(der_sig)
	assert len(p1363) == 64
	back_to_der = p1363_to_der(p1363)
	# Round trip must verify against the same public key even if byte layout
	# differs from the original (both are valid minimal-form DER INTEGERs).
	key.public_key().verify(back_to_der, b"hello world", ec.ECDSA(SHA256()))
