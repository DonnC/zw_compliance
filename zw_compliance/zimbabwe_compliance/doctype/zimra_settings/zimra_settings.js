// Copyright (c) 2026, Donald Chinhuru and contributors
// For license information, please see license.txt

frappe.ui.form.on("Zimra Settings", {
	refresh(frm) {
		frm.add_custom_button(
			__("Register Device"),
			() => {
				frappe.confirm(
					__("This generates a new device key pair and submits a CSR to ZIMRA using the Activation Key. Continue?"),
					() => {
						frappe.call({
							method: "zw_compliance.api.register_device",
							freeze: true,
							freeze_message: __("Registering device with ZIMRA..."),
						}).then((r) => {
							frappe.msgprint(__("Device registered: {0}", [r.message.commonName]));
							frm.reload_doc();
						});
					}
				);
			},
			__("ZIMRA")
		);

		frm.add_custom_button(
			__("Sync Config & Taxes"),
			() => {
				frappe.call({
					method: "zw_compliance.api.sync_config",
					freeze: true,
					freeze_message: __("Fetching config from ZIMRA..."),
				}).then((r) => {
					frappe.msgprint(__("Synced {0} tax(es) for {1}.", [r.message.taxesSynced, r.message.taxpayerName]));
					frm.reload_doc();
				});
			},
			__("ZIMRA")
		);

		frm.add_custom_button(
			__("Open Fiscal Day"),
			() => {
				frappe.call({
					method: "zw_compliance.api.open_day",
					freeze: true,
					freeze_message: __("Opening fiscal day..."),
				}).then(() => frm.reload_doc());
			},
			__("ZIMRA")
		);

		frm.add_custom_button(
			__("Close Fiscal Day"),
			() => {
				frappe.confirm(__("Close the current fiscal day? This is irreversible."), () => {
					frappe.call({
						method: "zw_compliance.api.close_day",
						freeze: true,
						freeze_message: __("Closing fiscal day..."),
					}).then(() => frm.reload_doc());
				});
			},
			__("ZIMRA")
		);

		frm.add_custom_button(
			__("Renew Certificate"),
			() => {
				frappe.confirm(
					__("Renew the device certificate now (same key, new certificate)? This calls ZIMRA over the current certificate, so it only works before expiry."),
					() => {
						frappe.call({
							method: "zw_compliance.api.renew_certificate",
							freeze: true,
							freeze_message: __("Renewing certificate with ZIMRA..."),
						}).then(() => {
							frappe.msgprint(__("Certificate renewed."));
							frm.reload_doc();
						});
					}
				);
			},
			__("ZIMRA")
		);

		frm.add_custom_button(
			__("Check Status"),
			() => {
				frappe.call({ method: "zw_compliance.api.get_status", freeze: true }).then((r) => {
					frappe.msgprint({
						title: __("ZIMRA Device Status"),
						message: `<pre>${JSON.stringify(r.message, null, 2)}</pre>`,
					});
					frm.reload_doc();
				});
			},
			__("ZIMRA")
		);

		frm.add_custom_button(
			__("Offline Queue Status"),
			() => {
				frappe.call({ method: "zw_compliance.api.queue_status" }).then((r) => {
					const ageMin = r.message.oldestPendingAgeMs ? Math.round(r.message.oldestPendingAgeMs / 60000) : 0;
					frappe.msgprint(
						__("{0} receipt(s) queued offline. Oldest: {1} minute(s) ago.", [r.message.size, ageMin])
					);
				});
			},
			__("ZIMRA")
		);

		frm.add_custom_button(
			__("Flush Offline Queue"),
			() => {
				frappe.call({
					method: "zw_compliance.api.flush_offline_queue",
					freeze: true,
					freeze_message: __("Submitting queued receipts to ZIMRA..."),
				}).then((r) => {
					frappe.msgprint(
						__("Submitted {0}, {1} still pending.{2}", [
							r.message.submitted,
							r.message.remaining,
							r.message.error ? "<br>" + __("Error: {0}", [r.message.error]) : "",
						])
					);
				});
			},
			__("ZIMRA")
		);
	},
});
