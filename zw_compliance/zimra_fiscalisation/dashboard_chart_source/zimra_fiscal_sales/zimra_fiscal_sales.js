frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["ZIMRA Fiscal Sales"] = {
	method: "zw_compliance.zimra_fiscalisation.dashboard_chart_source.zimra_fiscal_sales.zimra_fiscal_sales.get",
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "currency",
			label: __("Currency"),
			fieldtype: "Link",
			options: "Currency",
			description: __("Default: the company currency"),
		},
	],
};
