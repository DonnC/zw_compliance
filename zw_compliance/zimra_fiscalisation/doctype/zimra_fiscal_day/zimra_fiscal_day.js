// Copyright (c) 2026, Donald Chinhuru and contributors
// For license information, please see license.txt

const DAY_INDICATORS = {
	"Pending Open": "blue",
	Opened: "green",
	"Close Requested": "orange",
	"Close Initiated": "orange",
	"Close Failed": "red",
	Closed: "gray",
};

frappe.ui.form.on("Zimra Fiscal Day", {
	refresh(frm) {
		frm.page.set_indicator(__(frm.doc.status), DAY_INDICATORS[frm.doc.status] || "gray");
		const manager = frappe.user.has_role(["System Manager", "ZIMRA Manager"]);
		if (manager && ["Opened", "Pending Open", "Close Failed"].includes(frm.doc.status)) {
			frm.add_custom_button(__(frm.doc.status === "Close Failed" ? "Retry Close" : "Close Fiscal Day"), () =>
				frappe.confirm(__("Close fiscal day {0}? New invoices will open the next day.", [frm.doc.fiscal_day_no]), () =>
					frappe.call({
						method: "zw_compliance.fiscalisation.api.request_close",
						args: { day: frm.doc.name },
						freeze: true,
						callback: () => frm.reload_doc(),
					})
				)
			).addClass("btn-primary");
		}
		frm.add_custom_button(__(frm.doc.status === "Closed" ? "Z Report" : "X Report"), () =>
			frappe.utils.print(frm.doctype, frm.docname, "ZIMRA Z Report")
		);
		frm.add_custom_button(__("Receipts"), () => frappe.set_route("List", "Zimra Fiscal Receipt", { fiscal_day: frm.doc.name }));
		if (frm.doc.last_error) frm.dashboard.set_headline_alert(frappe.utils.escape_html(frm.doc.last_error), "red");
	},
});
