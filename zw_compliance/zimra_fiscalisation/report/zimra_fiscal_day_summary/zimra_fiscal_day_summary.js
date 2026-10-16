// Copyright (c) 2026, Donald Chinhuru and contributors
// For license information, please see license.txt

frappe.query_reports["ZIMRA Fiscal Day Summary"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "fiscal_device", label: __("Fiscal Device"), fieldtype: "Link", options: "Zimra Fiscal Device" },
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -30) },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{ fieldname: "currency", label: __("Currency"), fieldtype: "Link", options: "Currency" },
		{ fieldname: "status", label: __("Status"), fieldtype: "Select", options: "\nPending Open\nOpened\nClose Requested\nClose Initiated\nClose Failed\nClosed" },
	],
};
