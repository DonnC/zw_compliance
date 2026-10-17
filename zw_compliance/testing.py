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
	``before_tests`` hooks, so ERPNext's own test set-up would never run on a
	new CI site (no warehouse types, customer groups, territories, price lists
	...). ERPNext provides it differently per version:

	* v15 (and early v16 development builds): the ``before_tests`` hook
	  ``erpnext.setup.utils.before_tests``, which completes the setup wizard;
	* v16: importing ``erpnext.tests.utils`` runs ``BootStrapTestData()``,
	  as ERPNext's own CI does.

	Both leave a site that is already set up as it is.
	"""
	try:
		from erpnext.setup.utils import before_tests as erpnext_before_tests
	except ImportError:
		import erpnext.tests.utils  # bootstraps ERPNext test data on import
	else:
		erpnext_before_tests()
