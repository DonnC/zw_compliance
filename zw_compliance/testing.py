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

__all__ = ["IntegrationTestCase", "UnitTestCase"]
