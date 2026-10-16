// Copyright (c) 2026, Donald Chinhuru and contributors
// For license information, please see license.txt

frappe.listview_settings["Zimra Fiscal Day"] = {
	get_indicator(doc) {
		const colours = { "Pending Open": "blue", Opened: "green", "Close Requested": "orange", "Close Initiated": "orange", "Close Failed": "red", Closed: "gray" };
		return [__(doc.status), colours[doc.status] || "gray", "status,=," + doc.status];
	},
};
