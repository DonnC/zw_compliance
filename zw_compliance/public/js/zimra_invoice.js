// Copyright (c) 2026, Donald Chinhuru and contributors
// For license information, please see license.txt
// ZIMRA fiscalisation status on Sales Invoice and POS Invoice.

const ZIMRA_INDICATORS = {
	Queued: "blue",
	Submitted: "green",
	Retrying: "orange",
	Rejected: "red",
	"Fiscalised via POS": "green",
};

function zimra_refresh(frm) {
	const status = frm.doc.zimra_status;
	if (frm.doc.docstatus !== 1 || !status || status === "Not Applicable") return;

	frm.dashboard.add_indicator(__("ZIMRA: {0}", [__(status)]), ZIMRA_INDICATORS[status] || "gray");

	if (frm.doc.zimra_fiscal_receipt) {
		frm.add_custom_button(
			__("Fiscal Receipt"),
			() => frappe.set_route("Form", "Zimra Fiscal Receipt", frm.doc.zimra_fiscal_receipt),
			__("ZIMRA")
		);
		frm.add_custom_button(
			__("Print Fiscal Receipt"),
			() => {
				const format = frm.doc.doctype === "POS Invoice" ? "ZIMRA Receipt48" : "ZIMRA Invoice A4";
				frappe.utils.print(frm.doctype, frm.docname, format, frm.doc.letter_head, frm.doc.language);
			},
			__("ZIMRA")
		);
	}
	if (status === "Rejected" || status === "Retrying") {
		frm.dashboard.set_headline_alert(
			__("This invoice is fiscalised and printable, but ZIMRA has not accepted it yet. See the Fiscal Receipt for details."),
			status === "Rejected" ? "red" : "orange"
		);
	}
}

frappe.ui.form.on("Sales Invoice", { refresh: zimra_refresh });
frappe.ui.form.on("POS Invoice", { refresh: zimra_refresh });
