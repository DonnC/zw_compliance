frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["ZIMRA Output VAT by Tax"] = {
	method: "zw_compliance.zimra_fiscalisation.dashboard_chart_source.zimra_output_vat_by_tax.zimra_output_vat_by_tax.get",
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
