// Copyright (c) 2026, Donald Chinhuru and contributors
// For license information, please see license.txt

frappe.query_reports["ZIMRA Device Status"] = {
	filters: [{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" }],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value; // filter and total rows have no data
		if (!data) return value;
		const danger =
			(column.fieldname === "certificate_days_left" && data.certificate_days_left !== null && data.certificate_days_left <= 30) ||
			(column.fieldname === "hours_open" && data.day_max_hours && data.hours_open >= data.day_max_hours - 2) ||
			(["rejected", "blocking_receipts"].includes(column.fieldname) && data[column.fieldname] > 0);
		return danger ? `<span class="text-danger">${value}</span>` : value;
	},
};
