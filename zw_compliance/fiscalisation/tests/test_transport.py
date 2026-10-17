"""TLS settings of the requests transport."""

from __future__ import annotations

import ssl

from zw_compliance.fiscalisation.transport import RequestsTransport, _TrustedCaAdapter
from zw_compliance.testing import UnitTestCase


class TestTransport(UnitTestCase):
	def test_zimra_servers_use_the_default_strict_context(self):
		transport = RequestsTransport()
		self.assertNotIsInstance(
			transport.session.get_adapter("https://fdmsapi.zimra.co.zw"), _TrustedCaAdapter
		)
		self.assertIs(transport.verify, True)

	def test_trusted_ca_override_relaxes_only_the_strict_profile(self):
		import certifi

		transport = RequestsTransport(verify=certifi.where())
		adapter = transport.session.get_adapter("https://127.0.0.1:8443")
		self.assertIsInstance(adapter, _TrustedCaAdapter)
		context = adapter.poolmanager.connection_pool_kw["ssl_context"]
		self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
		self.assertTrue(context.check_hostname)
		self.assertFalse(context.verify_flags & getattr(ssl, "VERIFY_X509_STRICT", 0))
