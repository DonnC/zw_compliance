"""App-level permission checks referenced from ``hooks.py``."""

import frappe

from zw_compliance.fiscalisation.constants import ZIMRA_MANAGER, ZIMRA_USER

APP_ROLES = {ZIMRA_MANAGER, ZIMRA_USER, "System Manager", "Accounts Manager"}


def check_app_permission() -> bool:
	"""Show the app on the apps screen to the people who work with it."""
	if frappe.session.user == "Administrator":
		return True
	return bool(APP_ROLES & set(frappe.get_roles()))
