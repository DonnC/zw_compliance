// Copyright (c) 2026, Donald Chinhuru and contributors
// For license information, please see license.txt

frappe.query_reports["Zimra Fiscal Day Summary"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "currency",
			label: __("Currency"),
			fieldtype: "Link",
			options: "Currency",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nOpened\nClosed\nClose Failed",
		},
		{
			fieldname: "fiscal_day_no",
			label: __("Fiscal Day No"),
			fieldtype: "Int",
		},
	],
};
