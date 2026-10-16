"""Pure-Python implementation of the ZIMRA Fiscal Device Gateway API, spec v7.2.

This package has no Frappe imports and performs no I/O except through the
:class:`~zw_compliance.fdms.client.Transport` the caller supplies, so it is
testable with plain ``pytest`` (see ``fdms/tests``).

Modules:

* :mod:`.money` - exact Decimal amounts and the rounding rule
* :mod:`.types` - the data model (snake_case)
* :mod:`.receipt` - receipt assembly, pre-submission validation, fiscal counters (§4.7, §6)
* :mod:`.signing` - canonical strings for device and FDMS signatures (§13)
* :mod:`.signer` - device keys and signatures (§12, §13.1)
* :mod:`.csr` - certificate signing requests (§4.2)
* :mod:`.wire` - JSON mapping to the gateway's camelCase fields
* :mod:`.client` - the HTTP client (§4, §7, §8)
* :mod:`.verify` - verification of FDMS's signatures (§13.2.2, §13.3.2)
* :mod:`.qr` - QR code and verification code (§11)
* :mod:`.errors` - error and validation codes (§8)
* :mod:`.clock` - server-corrected clock

The ZIMRA protocol knowledge here was first ported from the MIT-licensed
`zimra-fdms <https://github.com/munashe-chivandire/zimra-fdms>`_ TypeScript
SDK and has since been aligned with the published specification where the two
differ (see ``docs/COMPLIANCE.md``).
"""
