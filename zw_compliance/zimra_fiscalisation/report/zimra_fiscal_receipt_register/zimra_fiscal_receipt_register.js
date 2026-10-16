// Copyright (c) 2026, Donald Chinhuru and contributors
// For license information, please see license.txt

frappe.query_reports["ZIMRA Fiscal Receipt Register"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "fiscal_device", label: __("Fiscal Device"), fieldtype: "Link", options: "Zimra Fiscal Device" },
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.month_start() },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{ fieldname: "receipt_type", label: __("Receipt Type"), fieldtype: "Select", options: "\nFiscalInvoice\nCreditNote\nDebitNote" },
		{ fieldname: "receipt_currency", label: __("Currency"), fieldtype: "Link", options: "Currency" },
		{ fieldname: "sync_status", label: __("ZIMRA Status"), fieldtype: "Select", options: "\nQueued\nSubmitted\nRetrying\nRejected" },
		{ fieldname: "worst_colour", label: __("Validation"), fieldtype: "Select", options: "\nGrey\nYellow\nRed" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		const colours = { Red: "red", Grey: "gray", Yellow: "orange", Rejected: "red", Retrying: "orange", Queued: "blue", Submitted: "green" };
		if (["worst_colour", "sync_status"].includes(column.fieldname) && colours[data[column.fieldname]]) {
			value = `<span class="indicator-pill ${colours[data[column.fieldname]]}">${value}</span>`;
		}
		return value;
	},
};
