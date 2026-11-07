"""The bench commands wrap the demo seeder."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from frappe.tests import IntegrationTestCase
from frappe.utils.bench_helper import get_app_commands

from zw_compliance import commands


class TestDemoCommands(IntegrationTestCase):
	def _invoke(self, command, *args, sites=("one.local",)):
		method = MagicMock()
		context = SimpleNamespace(sites=list(sites), profile=False)
		with (
			patch.object(commands.frappe, "init"),
			patch.object(commands.frappe, "connect"),
			patch.object(commands.frappe, "destroy"),
			patch.object(commands.frappe.db, "commit"),
			patch.object(commands.frappe, "get_attr", return_value=method) as get_attr,
		):
			result = CliRunner().invoke(command, args, obj=context)
		return result, get_attr, method

	def test_frappe_finds_the_commands(self):
		self.assertLessEqual({"zw-demo-seed", "zw-demo-wipe"}, set(get_app_commands("zw_compliance")))

	def test_seed_defaults_to_three_weeks(self):
		result, get_attr, method = self._invoke(commands.zw_demo_seed)
		self.assertEqual(result.exit_code, 0, result.output)
		get_attr.assert_called_once_with("zw_compliance.fiscalisation.demo.seed")
		method.assert_called_once_with(days=21)

	def test_seed_takes_a_number_of_days(self):
		_, _, method = self._invoke(commands.zw_demo_seed, "--days", "5")
		method.assert_called_once_with(days=5)

	def test_seed_refuses_zero_days(self):
		result, _, method = self._invoke(commands.zw_demo_seed, "--days", "0")
		self.assertNotEqual(result.exit_code, 0)
		method.assert_not_called()

	def test_wipe_runs_on_every_site(self):
		result, _, method = self._invoke(commands.zw_demo_wipe, sites=("one.local", "two.local"))
		self.assertEqual(result.exit_code, 0, result.output)
		self.assertEqual(method.call_count, 2)

	def test_a_site_is_required(self):
		result, _, method = self._invoke(commands.zw_demo_wipe, sites=())
		self.assertNotEqual(result.exit_code, 0)
		method.assert_not_called()
