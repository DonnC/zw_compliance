"""Net fiscal sales per day, last 30 days (one series per currency)."""

import frappe

from zw_compliance.fiscalisation import dashboard


@frappe.whitelist()
def get(
	chart_name=None,
	chart=None,
	no_cache=None,
	filters=None,
	from_date=None,
	to_date=None,
	timespan=None,
	time_interval=None,
	heatmap_year=None,
):
	return dashboard.fiscal_sales(chart_name=chart_name, chart=chart, no_cache=no_cache, filters=filters)
