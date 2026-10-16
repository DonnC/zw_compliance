"""Test base classes that work on Frappe v15 and v16.

Frappe v16 (and late v15 releases) provide ``frappe.tests.IntegrationTestCase``
and ``UnitTestCase``; older v15 releases only have
``frappe.tests.utils.FrappeTestCase``.
"""

try:
	from frappe.tests import IntegrationTestCase, UnitTestCase
except ImportError:  # pragma: no cover - older Frappe v15
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

	UnitTestCase = IntegrationTestCase

__all__ = ["IntegrationTestCase", "UnitTestCase", "before_tests"]


def before_tests() -> None:
	"""``before_tests`` hook: give a fresh test site ERPNext's base data.

	``bench run-tests --app zw_compliance`` runs only this app's
	``before_tests`` hooks, so ERPNext's own hook, which completes the setup
	wizard (warehouse types, customer groups, territories, price lists, UOMs
	...), would never run on a new CI site. A site that is already set up is
	left as it is.
	"""
	from erpnext.setup.utils import before_tests as erpnext_before_tests

	erpnext_before_tests()
