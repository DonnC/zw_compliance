"""Bench commands, picked up by Frappe from ``<app>.commands``. ::

bench --site <site> zw-demo-seed [--days 21]
bench --site <site> zw-demo-wipe
"""

from __future__ import annotations

import click
import frappe
from frappe.commands import pass_context
from frappe.exceptions import SiteNotSpecifiedError


def _on_each_site(context, method: str, **kwargs) -> None:
	if not context.sites:
		raise SiteNotSpecifiedError
	for site in context.sites:
		frappe.init(site)
		frappe.connect()
		try:
			frappe.get_attr(method)(**kwargs)
			frappe.db.commit()
		finally:
			frappe.destroy()


@click.command("zw-demo-seed")
@click.option(
	"--days",
	type=click.IntRange(min=1),
	default=21,
	show_default=True,
	help="Days of fiscal history, ending today",
)
@pass_context
def zw_demo_seed(context, days):
	"""Fill the site with demo fiscal history in the company ZW Demo Traders"""
	_on_each_site(context, "zw_compliance.fiscalisation.demo.seed", days=days)


@click.command("zw-demo-wipe")
@pass_context
def zw_demo_wipe(context):
	"""Remove everything zw-demo-seed created"""
	_on_each_site(context, "zw_compliance.fiscalisation.demo.wipe")


commands = [zw_demo_seed, zw_demo_wipe]
