"""The workspace definition: every block must resolve to a widget.

Frappe matches a block to its widget by the widget's *label*; a block naming
anything else silently renders nothing."""

from __future__ import annotations

import json

import frappe

from zw_compliance.testing import UnitTestCase

WORKSPACE = (
	"zw_compliance",
	"zimra_fiscalisation",
	"workspace",
	"zimra_fiscalisation",
	"zimra_fiscalisation.json",
)


class TestWorkspace(UnitTestCase):
	def setUp(self):
		with open(frappe.get_app_path(*WORKSPACE)) as fh:
			self.workspace = json.load(fh)
		self.blocks = json.loads(self.workspace["content"])

	def test_every_block_resolves_to_a_widget(self):
		tables = {
			"number_card": ("number_cards", "number_card_name"),
			"chart": ("charts", "chart_name"),
			"quick_list": ("quick_lists", "quick_list_name"),
			"shortcut": ("shortcuts", "shortcut_name"),
			"card": ("links", "card_name"),
		}
		for block in self.blocks:
			if block["type"] not in tables:
				continue
			table, key = tables[block["type"]]
			labels = {row["label"] for row in self.workspace[table]}
			self.assertIn(
				block["data"][key], labels, f"{block['type']} block does not match any {table} label"
			)

	def test_widgets_exist(self):
		for row in self.workspace["number_cards"]:
			self.assertTrue(frappe.db.exists("Number Card", row["number_card_name"]), row)
		for row in self.workspace["charts"]:
			self.assertTrue(frappe.db.exists("Dashboard Chart", row["chart_name"]), row)
