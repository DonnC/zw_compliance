// Copyright (c) 2026, Donald Chinhuru and contributors
// For license information, please see license.txt
// Guided set-up: Verify taxpayer -> Register -> (automatic) configure -> Active.

const API = "zw_compliance.fiscalisation.api.";

function zimra_call(frm, method, message, args) {
	return frappe.call({
		method: API + method,
		args: Object.assign({ device: frm.doc.name }, args || {}),
		freeze: true,
		freeze_message: message,
		callback: () => frm.reload_doc(),
	});
}

frappe.ui.form.on("Zimra Fiscal Device", {
	refresh(frm) {
		if (frm.is_new()) {
			frm.set_intro(__("Enter the Device ID, Serial No and model exactly as registered with ZIMRA, then save."));
			return;
		}
		const manager = frappe.user.has_role(["System Manager", "ZIMRA Manager"]);
		const status = frm.doc.status;

		if (status === "Draft") {
			frm.set_intro(__("Step 1 of 2: save the activation key, then confirm which taxpayer this device belongs to."), "blue");
			manager && frm.add_custom_button(__("Verify Taxpayer"), () =>
				zimra_call(frm, "verify_taxpayer", __("Asking ZIMRA...")).then((r) => {
					const d = r.message || {};
					frappe.msgprint({
						title: __("Is this the right taxpayer?"),
						indicator: "blue",
						message: __("ZIMRA registered this device to <b>{0}</b> (TIN {1}{2}). If correct, register the device.", [
							d.taxpayer_name, d.taxpayer_tin, d.vat_number ? ", VAT " + d.vat_number : "",
						]),
					});
				})
			).addClass("btn-primary");
		} else if (status === "Taxpayer Verified") {
			frm.set_intro(__("Step 2 of 2: register the device to obtain its ZIMRA certificate."), "blue");
			manager && frm.add_custom_button(__("Register Device"), () =>
				frappe.confirm(__("Register this device with ZIMRA for {0}?", [frm.doc.taxpayer_name]), () =>
					zimra_call(frm, "register_device", __("Registering with ZIMRA..."))
				)
			).addClass("btn-primary");
		} else if (status === "Registered") {
			manager && frm.add_custom_button(__("Sync Configuration"), () =>
				zimra_call(frm, "sync_configuration", __("Fetching configuration from ZIMRA..."))
			).addClass("btn-primary");
		}

		if (["Active", "Registered"].includes(status) && manager) {
			frm.add_custom_button(__("Sync Configuration"), () => zimra_call(frm, "sync_configuration", __("Fetching configuration from ZIMRA...")), __("Actions"));
			frm.add_custom_button(__("Renew Certificate"), () =>
				frappe.confirm(__("Request a new ZIMRA certificate for this device now?"), () =>
					zimra_call(frm, "renew_certificate", __("Renewing certificate..."))
				), __("Actions"));
		}
		if (status === "Active" && manager) {
			frm.add_custom_button(__("Send Now"), () => zimra_call(frm, "sync_now", __("Sending to ZIMRA...")), __("Actions"));
			frm.add_custom_button(__("Open Fiscal Day"), () => zimra_call(frm, "open_day", __("Opening fiscal day...")), __("Actions"));
			frm.add_custom_button(__("Disable"), () =>
				frappe.confirm(__("Disable this device? It will stop fiscalising invoices."), () => zimra_call(frm, "set_enabled", __("Disabling..."), { enabled: 0 })),
				__("Actions"));
		}
		if (status === "Disabled" && manager) {
			frm.add_custom_button(__("Enable"), () => zimra_call(frm, "set_enabled", __("Enabling..."), { enabled: 1 }));
		}
		if (frm.doc.current_fiscal_day) {
			frm.add_custom_button(__("Current Fiscal Day"), () => frappe.set_route("Form", "Zimra Fiscal Day", frm.doc.current_fiscal_day), __("View"));
		}
		frm.add_custom_button(__("Fiscal Receipts"), () => frappe.set_route("List", "Zimra Fiscal Receipt", { fiscal_device: frm.doc.name }), __("View"));

		if (status === "Active") {
			frappe.call({ method: API + "device_summary", args: { device: frm.doc.name } }).then((r) => {
				const s = r.message || {};
				const day = s.day ? __("Fiscal day {0}: {1}, {2} receipts", [s.day.fiscal_day_no, s.day.status, s.day.receipt_counter || 0]) : __("No fiscal day yet");
				const parts = [day];
				if (s.queued) parts.push(__("{0} waiting for ZIMRA", [s.queued]));
				if (s.rejected) parts.push(__("{0} rejected", [s.rejected]));
				if (s.certificate_days_left !== null && s.certificate_days_left <= 30) parts.push(__("certificate expires in {0} days", [s.certificate_days_left]));
				const colour = s.rejected || (s.day && s.day.blocking_receipts) ? "red" : s.queued || s.last_sync_error ? "orange" : "green";
				frm.dashboard.set_headline_alert(parts.join(" · ") + (s.last_sync_error ? "<br><small>" + frappe.utils.escape_html(s.last_sync_error.slice(0, 300)) + "</small>" : ""), colour);
			});
		}
	},
});
