// Copyright (c) 2026, Donald Chinhuru and contributors
// For license information, please see license.txt

frappe.listview_settings["Zimra Fiscal Receipt"] = {
	add_fields: ["sync_status", "worst_colour"],
	get_indicator(doc) {
		if (doc.worst_colour === "Red") return [__("Red"), "red", "worst_colour,=,Red"];
		if (doc.worst_colour === "Grey") return [__("Grey"), "gray", "worst_colour,=,Grey"];
		const colours = { Queued: "blue", Submitted: "green", Retrying: "orange", Rejected: "red" };
		return [__(doc.sync_status), colours[doc.sync_status] || "gray", "sync_status,=," + doc.sync_status];
	},
};
