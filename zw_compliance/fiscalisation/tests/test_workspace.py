"""The workspaces: every block must resolve to a widget, and the pages must
nest under Fiscalisation.

Frappe matches a block to its widget by the widget's *label*; a block naming
anything else silently renders nothing."""

from __future__ import annotations

import json

import frappe
from frappe.desk.desktop import get_workspace_sidebar_items

from zw_compliance.testing import IntegrationTestCase, UnitTestCase

PARENT = "Fiscalisation"
PAGES = ["Fiscal Operations", "Fiscal Reports", "Fiscal Setup"]


def definition(name: str) -> dict:
	folder = frappe.scrub(name)
	path = frappe.get_app_path("zw_compliance", "zimra_fiscalisation", "workspace", folder, f"{folder}.json")
	with open(path) as fh:
		return json.load(fh)


class TestWorkspaceDefinitions(UnitTestCase):
	def test_every_block_resolves_to_a_widget(self):
		tables = {
			"number_card": ("number_cards", "number_card_name"),
			"chart": ("charts", "chart_name"),
			"quick_list": ("quick_lists", "quick_list_name"),
			"shortcut": ("shortcuts", "shortcut_name"),
			"card": ("links", "card_name"),
		}
		for name in (PARENT, *PAGES):
			workspace = definition(name)
			for block in json.loads(workspace["content"]):
				if block["type"] not in tables:
					continue
				table, key = tables[block["type"]]
				labels = {row["label"] for row in workspace[table]}
				self.assertIn(
					block["data"][key],
					labels,
					f"{name}: {block['type']} block does not match any {table} label",
				)

	def test_widgets_exist(self):
		workspace = definition(PARENT)
		self.assertTrue(workspace["number_cards"] and workspace["charts"])
		for row in workspace["number_cards"]:
			self.assertTrue(frappe.db.exists("Number Card", row["number_card_name"]), row)
		for row in workspace["charts"]:
			self.assertTrue(frappe.db.exists("Dashboard Chart", row["chart_name"]), row)

	def test_every_link_exists(self):
		for name in PAGES:
			links = [row for row in definition(name)["links"] if row["type"] == "Link"]
			self.assertTrue(links, name)
			for link in links:
				self.assertTrue(frappe.db.exists(link["link_type"], link["link_to"]), link)


class TestSidebar(IntegrationTestCase):
	def test_pages_nest_under_fiscalisation(self):
		for page in PAGES:
			self.assertEqual(frappe.db.get_value("Workspace", page, "parent_page"), PARENT)
		self.assertFalse(frappe.db.get_value("Workspace", PARENT, "parent_page"))

	def test_the_old_workspace_is_gone(self):
		self.assertFalse(frappe.db.exists("Workspace", "ZIMRA Fiscalisation"))

	def test_the_app_opens_on_the_dashboard(self):
		self.assertEqual(frappe.get_hooks("app_home", app_name="zw_compliance"), ["/app/fiscalisation"])

	def test_a_zimra_user_sees_all_the_pages(self):
		user = "zimra.sidebar@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{"doctype": "User", "email": user, "first_name": "Sidebar", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.get_doc("User", user).add_roles("ZIMRA User")
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user(user)
		names = {page.name for page in get_workspace_sidebar_items()["pages"]}
		self.assertLessEqual({PARENT, *PAGES}, names)
