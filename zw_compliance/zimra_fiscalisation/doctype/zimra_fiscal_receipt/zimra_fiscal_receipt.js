// Copyright (c) 2026, Donald Chinhuru and contributors
// For license information, please see license.txt

const RECEIPT_INDICATORS = { Queued: "blue", Submitted: "green", Retrying: "orange", Rejected: "red" };

frappe.ui.form.on("Zimra Fiscal Receipt", {
	refresh(frm) {
		frm.page.set_indicator(__(frm.doc.sync_status), RECEIPT_INDICATORS[frm.doc.sync_status] || "gray");
		frm.add_custom_button(__(frm.doc.reference_doctype), () => frappe.set_route("Form", frm.doc.reference_doctype, frm.doc.reference_name));
		if (frm.doc.worst_colour) {
			const colour = { Red: "red", Grey: "gray", Yellow: "orange" }[frm.doc.worst_colour];
			frm.dashboard.set_headline_alert(__("ZIMRA flagged this receipt {0}. See Validation Errors.", [__(frm.doc.worst_colour)]), colour);
		} else if (frm.doc.last_error) {
			frm.dashboard.set_headline_alert(frappe.utils.escape_html(frm.doc.last_error), frm.doc.sync_status === "Rejected" ? "red" : "orange");
		}
		if (["Queued", "Retrying"].includes(frm.doc.sync_status) && frappe.user.has_role(["System Manager", "ZIMRA Manager"])) {
			frm.add_custom_button(__("Send Now"), () =>
				frappe.call({
					method: "zw_compliance.fiscalisation.api.sync_now",
					args: { device: frm.doc.fiscal_device },
					callback: () => frappe.show_alert({ message: __("Sending to ZIMRA..."), indicator: "blue" }),
				})
			);
		}
	},
});
