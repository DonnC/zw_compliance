// Copyright (c) 2026, Donald Chinhuru and contributors
// For license information, please see license.txt

frappe.query_reports["ZIMRA Sync Exceptions"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "fiscal_device", label: __("Fiscal Device"), fieldtype: "Link", options: "Zimra Fiscal Device" },
		{ fieldname: "colour", label: __("Validation"), fieldtype: "Select", options: "\nGrey\nYellow\nRed" },
	],
};
